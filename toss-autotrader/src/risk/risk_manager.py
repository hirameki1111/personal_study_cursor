"""RiskManager ― 전 신호 단일 검증 관문, 거부권·kill switch (Phase 6).

검증 체인 (제안서 6.1 ― 순서 고정, 빠른 차단 우선):
  kill switch → 1회 상한 → 매수가능 → 매도가능 → 종목비중
  → 동시포지션 → 중복주문 → 투자유의종목

원칙:
- 모든 신호는 주문이 되기 전 approve()를 통과해야 함 (거부권 보유)
- kill switch는 일일 실현손익이 한도 도달 시 발동, 당일 자동 해제 금지
  (익일 reset_daily() 수동 호출로만 해제)
- 거부 시 RejectReason 명시 로깅 ― DB 적재·알림은 Phase 8에서 연동
- 투자유의는 공식 API(/api/v1/stocks/{symbol}/warnings) 기반 ― 활성 항목만 반환됨
"""

from enum import Enum

from loguru import logger

from src.strategy.base import Signal


class RejectReason(str, Enum):
    KILL_SWITCH = "kill_switch"
    OVER_ORDER_LIMIT = "over_order_limit"
    INSUFFICIENT_BP = "insufficient_buying_power"
    OVER_SELLABLE = "over_sellable"
    OVER_POSITION_PCT = "over_position_pct"
    OVER_CONCURRENT = "over_concurrent"
    DUPLICATE_ORDER = "duplicate_order"
    WARNING_STOCK = "warning_stock"


# 매수 차단 대상 유의 유형 (VI는 일시 발동이므로 기본 제외 ― 필요 시 설정으로 추가)
DEFAULT_BLOCKING_WARNINGS = {"LIQUIDATION_TRADING", "OVERHEATED",
                             "INVESTMENT_WARNING", "INVESTMENT_RISK"}


class RiskManager:
    def __init__(self, limits: dict, account, market,
                 executor=None,
                 blocking_warnings: set[str] | None = None) -> None:
        """limits: settings.yaml의 limits 섹션
        (daily_loss_limit, max_order_amount, max_position_pct, max_concurrent)
        """
        self.limits = limits
        self.account, self.market = account, market
        self.executor = executor
        self.blocking_warnings = blocking_warnings or DEFAULT_BLOCKING_WARNINGS
        self.killed = False
        self.daily_pnl = 0.0

    # ── kill switch (Step 6-2) ────────────────────────────

    def update_pnl(self, pnl: float) -> None:
        """실현손익 누적. 일일 손실 한도 도달 시 kill switch 발동."""
        self.daily_pnl += pnl
        if self.daily_pnl <= -abs(self.limits["daily_loss_limit"]):
            if not self.killed:
                self.killed = True
                logger.warning(f"일일 손실 한도 도달({self.daily_pnl:,.0f}) "
                               "― kill switch 발동, 신규 진입 전면 차단")

    def reset_daily(self) -> None:
        """익일 수동 리셋 전용 ― 당일 자동 해제 금지 원칙."""
        logger.info(f"일일 리셋: pnl {self.daily_pnl:,.0f} → 0, "
                    f"kill switch {self.killed} → False")
        self.daily_pnl = 0.0
        self.killed = False

    # ── 검증 체인 (Step 6-3) ──────────────────────────────

    def approve(self, signal: Signal) -> tuple[bool, str | None]:
        if self.killed:
            return self._reject(RejectReason.KILL_SWITCH, signal)

        if signal.side == "BUY":
            cost = self._estimate_cost(signal)
            if cost > self.limits["max_order_amount"]:
                return self._reject(RejectReason.OVER_ORDER_LIMIT, signal)
            if cost > self.account.get_buying_power("KRW"):
                return self._reject(RejectReason.INSUFFICIENT_BP, signal)

        if signal.side == "SELL":
            if signal.quantity > self.account.get_sellable(signal.stock_code):
                return self._reject(RejectReason.OVER_SELLABLE, signal)

        if signal.side == "BUY":
            positions = self._position_values()
            if not self._check_position_pct(signal, cost, positions):
                return self._reject(RejectReason.OVER_POSITION_PCT, signal)
            if (signal.stock_code not in positions
                    and len(positions) >= self.limits["max_concurrent"]):
                return self._reject(RejectReason.OVER_CONCURRENT, signal)

        if self._has_open_order(signal):
            return self._reject(RejectReason.DUPLICATE_ORDER, signal)

        if signal.side == "BUY" and self._is_warning_stock(signal.stock_code):
            return self._reject(RejectReason.WARNING_STOCK, signal)

        return True, None

    # ── 내부 검증 로직 ────────────────────────────────────

    def _estimate_cost(self, signal: Signal) -> float:
        """수수료 포함 예상 비용. MARKET 주문은 현재가 기준."""
        price = signal.price
        if price is None:
            price = float(self.market.get_price(
                signal.stock_code)["lastPrice"])
        if self.executor is not None:
            return self.executor.estimate_cost(
                signal.side, signal.quantity, price)
        return signal.quantity * price

    def _position_values(self) -> dict[str, float]:
        """보유 종목별 평가금액 {symbol: quantity × lastPrice}."""
        holdings = self.account.get_holdings()
        return {it["symbol"]: float(it["quantity"]) * float(it["lastPrice"])
                for it in holdings.get("items", [])}

    def _check_position_pct(self, signal: Signal, cost: float,
                            positions: dict[str, float]) -> bool:
        """총자산(평가금액 합 + 매수가능현금) 대비 종목 편입 비중 상한."""
        total_asset = (sum(positions.values())
                       + self.account.get_buying_power("KRW"))
        if total_asset <= 0:
            return False
        new_value = positions.get(signal.stock_code, 0.0) + cost
        return (new_value / total_asset * 100
                <= self.limits["max_position_pct"])

    def _has_open_order(self, signal: Signal) -> bool:
        """동일 종목·방향 미체결 주문 존재 여부 (Step 6-4)."""
        if self.executor is None:
            return False
        return any(o.symbol == signal.stock_code and o.side == signal.side
                   for o in self.executor.open_orders())

    def _is_warning_stock(self, symbol: str) -> bool:
        """활성 유의사항 중 차단 대상 유형 존재 여부 (Step 6-5)."""
        try:
            warnings = self.market.get_warnings(symbol)
        except Exception:
            # 유의사항 조회 실패 시 보수적으로 차단 (안전 우선)
            logger.warning(f"유의사항 조회 실패({symbol}) ― 보수적 차단")
            return True
        return any(w.get("warningType") in self.blocking_warnings
                   for w in warnings)

    def _reject(self, reason: RejectReason,
                signal: Signal) -> tuple[bool, str]:
        logger.warning(f"거부[{reason.value}] ― {signal}")
        return False, reason.value
