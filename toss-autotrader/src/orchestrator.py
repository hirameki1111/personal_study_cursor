"""Orchestrator ― 실행 루프·안전종료·상태복구 (Phase 9).

실행 루프 (제안서 9.1):
1. 설정·시크릿 로드 → 모듈 초기화 → (live) 잔고 reconciliation
2. 휴장일·거래시간 확인 → 비거래시간 대기
3. 매 사이클: 시세→이상치 검증→신호→Risk 승인→실행→적재·알림
4. 종료 조건(kill switch·장 종료·헬스체크 연속 실패) → 안전 종료
5. 사이클 내 예외는 격리 ― 해당 사이클만 보류, 루프는 지속

핵심 원칙:
- 전략 신호는 주문이 아님 ― 반드시 RiskManager.approve() 경유
- 모드 전환(dry_run/paper/live)은 config/settings.yaml 1곳에서만
- 알림·적재 실패가 거래 로직을 중단시키지 않음

실행:
    python -m src.orchestrator            # 상시 루프
    python -m src.orchestrator --once     # 1회 사이클 후 종료 (검증용)
"""

import argparse
import signal as os_signal
import time
import uuid
from datetime import datetime, timezone, timedelta

from loguru import logger

KST = timezone(timedelta(hours=9))


class Orchestrator:
    def __init__(self, settings: dict, market, account, strategies: dict,
                 risk, executor, store, notifier,
                 healthcheck=None,
                 install_signal_handlers: bool = True) -> None:
        """strategies: {symbol: (strategy_name, BaseStrategy)}"""
        self.settings = settings
        self.mode = settings["runtime"]["mode"]
        self.poll_interval = settings["runtime"].get("poll_interval_sec", 5)
        self.market, self.account = market, account
        self.strategies = strategies
        self.risk, self.executor = risk, executor
        self.store, self.notifier = store, notifier
        self.healthcheck = healthcheck
        self._running = True
        self._calendar_cache: tuple[str, dict] | None = None
        if install_signal_handlers:
            os_signal.signal(os_signal.SIGTERM, self._graceful)
            os_signal.signal(os_signal.SIGINT, self._graceful)

    # ── 종료·기동 ─────────────────────────────────────────

    def _graceful(self, *_) -> None:
        logger.info("종료 신호 수신 ― 안전 종료 절차 개시")
        self._running = False

    def startup(self) -> bool:
        """기동 절차. 사전 점검(토큰·API) 실패 시 기동 거부 + 알림."""
        logger.info(f"기동: mode={self.mode}, "
                    f"universe={list(self.strategies)}")
        if self.healthcheck and not self.healthcheck.check():
            logger.error("기동 사전 점검 실패 ― IP 등록·자격증명·네트워크 확인")
            self.notifier.send(
                "⛔ 기동 실패: 토큰/API 사전 점검 실패 "
                "― 공인 IP 변경(포털 재등록) 여부를 먼저 확인하세요")
            return False
        if self.mode == "live":
            positions = self.account.parse_positions(
                self.account.get_holdings())
            if not self.account.reconcile(positions):
                logger.error("기동 reconcile 실패 ― 기동 중단")
                return False
            open_orders = getattr(self.executor, "open_orders", lambda: [])()
            if open_orders:
                logger.warning(f"미종결 주문 {len(open_orders)}건 존재 "
                               "― 수동 확인 권장 (부록 E 복구 절차)")
        self.notifier.send(f"▶ 기동 (mode={self.mode})")
        return True

    # ── 거래시간 (Step 9-2) ───────────────────────────────

    def is_trading_time(self, now: datetime | None = None) -> bool:
        """KR 정규장 여부. 휴장일(integrated=null)이면 False."""
        now = now or datetime.now(KST)
        day_key = now.date().isoformat()
        if self._calendar_cache and self._calendar_cache[0] == day_key:
            cal = self._calendar_cache[1]
        else:
            cal = self.market.get_market_calendar("KR")
            self._calendar_cache = (day_key, cal)
        integrated = cal.get("today", {}).get("integrated")
        if not integrated:
            return False                       # 휴장일
        regular = integrated.get("regularMarket")
        if not regular:
            return False
        start = datetime.fromisoformat(regular["startTime"])
        end = datetime.fromisoformat(regular["endTime"])
        return start <= now <= end

    # ── 사이클 (Step 9-3) ─────────────────────────────────

    def safe_cycle(self, symbol: str) -> None:
        """사이클 예외 격리 ― 루프 전체를 중단시키지 않음."""
        try:
            self.run_cycle(symbol)
        except Exception as e:
            logger.error(f"사이클 예외({symbol}) ― 해당 사이클 보류: "
                         f"{type(e).__name__}: {e}")
            self.store.record_log("ERROR", "orchestrator",
                                  f"cycle error {symbol}: {type(e).__name__}")

    def run_cycle(self, symbol: str) -> None:
        if self.account.halted:                # 정합성 중단 시 보류
            return
        price_data = self.market.get_price(symbol)
        if not self.market.validate_price(symbol, price_data):
            return                             # 시세 이상 ― 사이클 보류
        last_price = float(price_data["lastPrice"])

        name, strategy = self.strategies[symbol]
        interval = strategy.params.get("interval", "1d")
        page = self.market.get_candles(symbol, interval, count=200)
        candles = sorted(page.get("candles", []),
                         key=lambda c: c["timestamp"])   # 시간 오름차순
        candles = [{"close": float(c["closePrice"]),
                    "open": float(c["openPrice"]),
                    "high": float(c["highPrice"]),
                    "low": float(c["lowPrice"]),
                    "volume": float(c["volume"]),
                    "ts": c["timestamp"]} for c in candles]
        positions = self.account.parse_positions(self.account.get_holdings())

        sig = strategy.generate(candles, positions)
        if sig is None or sig.side == "HOLD":
            logger.info(f"사이클 완료({symbol}): 현재가 {last_price:,.0f}, "
                        f"캔들 {len(candles)}개, 신호 없음")
            return
        self.store.record_signal(sig.stock_code, sig.side, sig.quantity,
                                 sig.order_type, name, sig.reason)

        ok, reason = self.risk.approve(sig)
        if not ok:
            self.store.record_risk_event("reject", sig.stock_code,
                                         reason or "unknown")
            return                             # 거부 사유는 Risk가 로깅

        # 페이퍼 모드의 시장가 주문은 현재가를 체결 기준가로 사용
        exec_price = sig.price
        if exec_price is None and self.mode == "paper":
            exec_price = last_price
        result = self.executor.place_order(
            sig.stock_code, sig.side, sig.quantity,
            price=exec_price, order_type=sig.order_type)

        order_id = result.get("orderId") or f"{self.mode}-{uuid.uuid4()}"
        self.store.record_order(order_id, str(uuid.uuid4()),
                                sig.stock_code, sig.side, sig.quantity,
                                exec_price, result.get("status", "PENDING"))
        if result.get("status") == "FILLED":   # 페이퍼 즉시 체결 → 체결 기록
            self.store.record_execution(
                str(uuid.uuid4()), order_id,
                fill_price=result.get("price") or exec_price or 0.0,
                fill_qty=sig.quantity)
        self.notifier.send(f"주문[{self.mode}]: {sig.side} {sig.stock_code} "
                           f"x{sig.quantity} → {result.get('status')}")
        # [Phase 9 후속: live 상태 폴링(sync_status) → 체결 확인 후 reconcile]

    # ── 메인 루프 ─────────────────────────────────────────

    def run(self, once: bool = False) -> None:
        if not self.startup():
            return
        cycle_count = 0
        loop_errors = 0                        # 루프 수준 연속 오류 (사이클 밖)
        try:
            while self._running:
                try:
                    trading = once or self.is_trading_time()
                except Exception as e:
                    # 거래시간 조회 실패(인증·네트워크)도 프로세스를 죽이지 않음
                    loop_errors += 1
                    logger.error(f"루프 오류 {loop_errors}회: "
                                 f"{type(e).__name__}: {e}")
                    self.store.record_log("ERROR", "orchestrator",
                                          f"loop error: {type(e).__name__}")
                    if loop_errors >= 5:
                        self.notifier.send("⛔ 루프 연속 오류 5회 ― 안전 종료 "
                                           "(IP 등록·네트워크 확인)")
                        break
                    time.sleep(max(self.poll_interval * 12, 60))
                    continue
                loop_errors = 0
                if not trading:
                    logger.info("비거래시간 ― 대기")
                    time.sleep(max(self.poll_interval * 12, 60))
                    continue
                for symbol in self.strategies:
                    self.safe_cycle(symbol)
                cycle_count += 1
                if self.healthcheck and cycle_count % 60 == 0:
                    self.healthcheck.check()
                    if self.healthcheck.should_shutdown():
                        logger.error("헬스체크 연속 실패 ― 안전 종료")
                        break
                if once:
                    break
                time.sleep(self.poll_interval)
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        logger.info("안전 종료: 상태 저장·일일 리포트·알림")
        self.store.record_log("INFO", "orchestrator",
                              f"shutdown (mode={self.mode})")
        try:                                   # 리포트 실패도 종료를 막지 않음
            from src.monitor.report import send_daily_report
            pnl = getattr(self.risk, "daily_pnl", None)
            if not isinstance(pnl, (int, float)):
                pnl = None
            send_daily_report(self.store, self.notifier,
                              daily_pnl=pnl, mode=self.mode)
        except Exception as e:
            logger.error(f"일일 리포트 생성 실패: {type(e).__name__}")
        self.notifier.send(f"■ 종료 (mode={self.mode})")


# ── 조립 (Step 9-1: 모드별 주입) ──────────────────────────

def build_orchestrator(settings: dict, creds: dict,
                       install_signal_handlers: bool = True) -> Orchestrator:
    from src.account.account_manager import AccountManager
    from src.auth.auth_manager import AuthManager
    from src.backtest.paper_broker import PaperBroker
    from src.market.market_client import MarketDataClient
    from src.monitor.db import EventStore
    from src.monitor.healthcheck import HealthChecker
    from src.monitor.logging_setup import register_secret
    from src.monitor.notifier import NullNotifier, TelegramNotifier
    from src.order.order_executor import OrderExecutor
    from src.risk.risk_manager import RiskManager
    from src.strategy.registry import load_strategy

    register_secret(creds["client_secret"])

    mode = settings["runtime"]["mode"]
    auth = AuthManager(creds["client_id"], creds["client_secret"])
    market = MarketDataClient(auth)
    account = AccountManager(auth, account_no=creds["account_no"])

    if mode == "paper":
        executor = PaperBroker(
            initial_cash=settings.get("paper", {}).get(
                "initial_cash", 10_000_000))
    else:
        executor = OrderExecutor(auth, account,
                                 dry_run=(mode != "live"))

    risk = RiskManager(settings["limits"], account, market,
                       executor=executor if mode != "paper" else None)

    strategies = {}
    for entry in settings["universe"]:
        code, name = entry["code"], entry["strategy"]
        params = dict(settings["strategies"][name], stock_code=code)
        strategies[code] = (name, load_strategy(name, params))

    store = EventStore(settings["logging"]["db_url"])
    if creds["telegram_bot_token"] and creds["telegram_chat_id"]:
        notifier = TelegramNotifier(creds["telegram_bot_token"],
                                    creds["telegram_chat_id"])
    else:
        notifier = NullNotifier()
    hc = HealthChecker(auth, market, notifier, store=store,
                       probe_symbol=settings["universe"][0]["code"])

    return Orchestrator(settings, market, account, strategies, risk,
                        executor, store, notifier, healthcheck=hc,
                        install_signal_handlers=install_signal_handlers)


def main() -> int:
    from src.core.config import load_credentials, load_settings
    from src.monitor.logging_setup import setup_logging

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true",
                        help="1회 사이클 후 종료 (검증용, 거래시간 무시)")
    args = parser.parse_args()

    settings = load_settings()
    setup_logging(settings.get("logging", {}).get("level", "INFO"))
    creds = load_credentials()

    orch = build_orchestrator(settings, creds)
    logger.info(f"모드: {orch.mode} ― 전환은 config/settings.yaml에서만")
    orch.run(once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
