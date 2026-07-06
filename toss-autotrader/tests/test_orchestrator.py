"""Phase 9 Orchestrator 테스트 (제안서 9.4 테스트 플랜).

- 정상 사이클: 시세→검증→신호→승인→실행→적재·알림 순서
- 보류 조건: halted / 시세 이상 / 신호 없음 / 리스크 거부
- 사이클 내 예외 격리: 루프 지속·해당 사이클만 보류
- 거래시간 판정: 정규장·휴장일
- 모드별 조립: dry_run/paper/live 주입 확인
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.backtest.paper_broker import PaperBroker
from src.order.order_executor import OrderExecutor
from src.orchestrator import KST, Orchestrator, build_orchestrator
from src.strategy.base import Signal

SETTINGS = {
    "runtime": {"mode": "dry_run", "poll_interval_sec": 5},
    "universe": [{"code": "005930", "strategy": "sma_cross"}],
    "strategies": {"sma_cross": {"short_window": 5, "long_window": 20,
                                 "qty": 1}},
    "limits": {"daily_loss_limit": 200000, "max_order_amount": 1000000,
               "max_position_pct": 20.0, "max_concurrent": 5},
    "logging": {"db_url": "sqlite:///:memory:"},
}

CANDLE = {"timestamp": "2026-07-03T00:00:00+09:00", "openPrice": "100",
          "highPrice": "110", "lowPrice": "90", "closePrice": "105",
          "volume": "1000", "currency": "KRW"}
BUY = Signal("005930", "BUY", 1, None, "MARKET", "golden_cross")


@pytest.fixture
def orch():
    market, account = MagicMock(), MagicMock()
    account.halted = False
    account.parse_positions.return_value = {}
    market.get_price.return_value = {"lastPrice": "70000"}
    market.validate_price.return_value = True
    market.get_candles.return_value = {"candles": [CANDLE]}
    strategy = MagicMock()
    strategy.params = {"interval": "1d"}
    strategy.generate.return_value = BUY
    risk = MagicMock()
    risk.approve.return_value = (True, None)
    executor = MagicMock()
    executor.place_order.return_value = {"status": "DRY_RUN"}
    store, notifier = MagicMock(), MagicMock()
    o = Orchestrator(SETTINGS, market, account,
                     {"005930": ("sma_cross", strategy)}, risk,
                     executor, store, notifier,
                     install_signal_handlers=False)
    return o


def test_normal_cycle_full_flow(orch):
    orch.run_cycle("005930")
    orch.store.record_signal.assert_called_once()
    orch.risk.approve.assert_called_once_with(BUY)
    orch.executor.place_order.assert_called_once()
    orch.store.record_order.assert_called_once()
    orch.notifier.send.assert_called_once()


def test_halted_account_skips_cycle(orch):
    orch.account.halted = True
    orch.run_cycle("005930")
    orch.market.get_price.assert_not_called()


def test_invalid_price_holds_cycle(orch):
    orch.market.validate_price.return_value = False
    orch.run_cycle("005930")
    orch.market.get_candles.assert_not_called()
    orch.executor.place_order.assert_not_called()


def test_no_signal_no_order(orch):
    orch.strategies["005930"][1].generate.return_value = None
    orch.run_cycle("005930")
    orch.risk.approve.assert_not_called()
    orch.executor.place_order.assert_not_called()


def test_risk_reject_records_event_no_order(orch):
    orch.risk.approve.return_value = (False, "over_order_limit")
    orch.run_cycle("005930")
    orch.store.record_risk_event.assert_called_once()
    orch.executor.place_order.assert_not_called()


def test_candles_passed_ascending_and_mapped(orch):
    older = dict(CANDLE, timestamp="2026-07-02T00:00:00+09:00",
                 closePrice="90")
    orch.market.get_candles.return_value = {"candles": [CANDLE, older]}
    orch.run_cycle("005930")
    passed = orch.strategies["005930"][1].generate.call_args.args[0]
    assert [c["close"] for c in passed] == [90.0, 105.0]   # 오름차순


def test_paper_market_order_uses_last_price(orch):
    orch.mode = "paper"
    orch.run_cycle("005930")
    kwargs = orch.executor.place_order.call_args.kwargs
    assert kwargs["price"] == 70000.0          # 현재가를 체결 기준가로


def test_cycle_exception_isolated(orch):
    orch.market.get_price.side_effect = RuntimeError("boom")
    orch.safe_cycle("005930")                  # 예외 전파 없어야 함
    orch.store.record_log.assert_called_once()


def test_loop_survives_trading_time_failure(orch):
    """거래시간 조회 실패(인증 403 등)가 프로세스를 죽이지 않아야 함.

    연속 5회 실패 시 안전 종료로 수렴 (traceback 크래시 금지).
    """
    from unittest.mock import patch as _patch
    orch.market.get_market_calendar.side_effect = RuntimeError("403")
    orch.store.daily_summary.return_value = {
        "date": "d", "orders": 0, "fills": 0, "fill_amount": 0.0,
        "fees": 0.0, "rejects": 0, "reject_reasons": [], "errors": 0}
    orch.risk.daily_pnl = 0.0
    with _patch("src.orchestrator.time.sleep"):
        orch.run(once=False)                   # 예외 전파 없이 반환해야 함
    sent = [c.args[0] for c in orch.notifier.send.call_args_list]
    assert any("연속 오류" in t for t in sent)  # 안전 종료 알림


def test_startup_preflight_failure_aborts(orch):
    """기동 사전 점검 실패(IP 미등록 등) 시 기동 거부 + 알림."""
    hc = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    hc.check.return_value = False
    orch.healthcheck = hc
    assert orch.startup() is False
    sent = [c.args[0] for c in orch.notifier.send.call_args_list]
    assert any("기동 실패" in t for t in sent)


def test_paper_fill_records_execution(orch):
    orch.mode = "paper"
    orch.executor.place_order.return_value = {"status": "FILLED",
                                              "price": 70070.0}
    orch.run_cycle("005930")
    kwargs = orch.store.record_execution.call_args
    assert kwargs.args[2] if len(kwargs.args) > 2 else \
        kwargs.kwargs["fill_price"] == 70070.0


def test_dry_run_no_execution_record(orch):
    orch.executor.place_order.return_value = {"status": "DRY_RUN"}
    orch.run_cycle("005930")
    orch.store.record_execution.assert_not_called()


def test_shutdown_sends_daily_report(orch):
    """G3 조건③: 안전 종료 시 일일 리포트 자동 발송."""
    orch.store.daily_summary.return_value = {
        "date": "2026-07-06", "orders": 1, "fills": 1,
        "fill_amount": 70000.0, "fees": 10.5, "rejects": 0,
        "reject_reasons": [], "errors": 0}
    orch.risk.daily_pnl = 0.0
    orch.shutdown()
    sent = [c.args[0] for c in orch.notifier.send.call_args_list]
    assert any("일일 리포트" in t for t in sent)
    assert any("■ 종료" in t for t in sent)


# ── 거래시간 (Step 9-2) ───────────────────────────────────

CAL_OPEN = {"today": {"date": "2026-07-03", "integrated": {
    "regularMarket": {"startTime": "2026-07-03T09:00:00+09:00",
                      "endTime": "2026-07-03T15:30:00+09:00"}}}}
CAL_CLOSED = {"today": {"date": "2026-07-04", "integrated": None}}


def test_trading_time_within_regular_session(orch):
    orch.market.get_market_calendar.return_value = CAL_OPEN
    now = datetime(2026, 7, 3, 10, 0, tzinfo=KST)
    assert orch.is_trading_time(now) is True
    after = datetime(2026, 7, 3, 16, 0, tzinfo=KST)
    orch._calendar_cache = None
    assert orch.is_trading_time(after) is False


def test_holiday_returns_false(orch):
    orch.market.get_market_calendar.return_value = CAL_CLOSED
    now = datetime(2026, 7, 4, 10, 0, tzinfo=KST)
    assert orch.is_trading_time(now) is False


def test_calendar_cached_per_day(orch):
    orch.market.get_market_calendar.return_value = CAL_OPEN
    now = datetime(2026, 7, 3, 10, 0, tzinfo=KST)
    orch.is_trading_time(now)
    orch.is_trading_time(now)
    assert orch.market.get_market_calendar.call_count == 1


# ── 조립 (Step 9-1) ───────────────────────────────────────

CREDS = {"client_id": "id", "client_secret": "sec", "account_no": None,
         "telegram_bot_token": None, "telegram_chat_id": None}


def _settings(mode):
    return {**SETTINGS, "runtime": {"mode": mode, "poll_interval_sec": 5},
            "strategies": {"sma_cross": {"short_window": 5,
                                         "long_window": 20, "qty": 1,
                                         "stock_code": "005930"}}}


def test_build_dry_run_injects_dry_executor():
    o = build_orchestrator(_settings("dry_run"), CREDS,
                           install_signal_handlers=False)
    assert isinstance(o.executor, OrderExecutor) and o.executor.dry_run


def test_build_paper_injects_paper_broker():
    o = build_orchestrator(_settings("paper"), CREDS,
                           install_signal_handlers=False)
    assert isinstance(o.executor, PaperBroker)


def test_build_multi_symbol_universe():
    """종목 추가 = universe 항목 추가만으로 종목별 전략 인스턴스 생성."""
    s = _settings("dry_run")
    s["universe"] = [{"code": "005930", "strategy": "sma_cross"},
                     {"code": "069500", "strategy": "sma_cross"}]
    o = build_orchestrator(s, CREDS, install_signal_handlers=False)
    assert set(o.strategies) == {"005930", "069500"}
    # 종목별 인스턴스가 분리되고 stock_code가 각각 주입되어야 함
    _, st1 = o.strategies["005930"]
    _, st2 = o.strategies["069500"]
    assert st1 is not st2
    assert st1.params["stock_code"] == "005930"
    assert st2.params["stock_code"] == "069500"
    # 공통 파라미터는 동일 (기존과 동일 설정 선택)
    assert st1.params["short_window"] == st2.params["short_window"]


def test_build_live_injects_live_executor():
    o = build_orchestrator(_settings("live"), CREDS,
                           install_signal_handlers=False)
    assert isinstance(o.executor, OrderExecutor)
    assert o.executor.dry_run is False
