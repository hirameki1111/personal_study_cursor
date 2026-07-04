"""Phase 6 RiskManager 테스트 (제안서 6.4 테스트 플랜).

- kill switch 발동 후 신호 → 거부(KILL_SWITCH), 경계값 검증 포함
- 1회 상한/매수가능/매도가능/종목비중/동시포지션/중복주문/투자유의 각 거부
- 정상 신호 → 승인 통과 (True, None)
"""

from unittest.mock import MagicMock

import pytest

from src.risk.risk_manager import RejectReason, RiskManager
from src.strategy.base import Signal

LIMITS = {"daily_loss_limit": 200000, "max_order_amount": 1000000,
          "max_position_pct": 20.0, "max_concurrent": 5}

BUY = Signal("005930", "BUY", 1, 70000.0, "LIMIT", "test")
SELL = Signal("005930", "SELL", 10, 70000.0, "LIMIT", "test")


def _holdings(items):
    return {"items": items}


@pytest.fixture
def rm():
    account = MagicMock()
    account.get_buying_power.return_value = 10_000_000.0
    account.get_sellable.return_value = 100.0
    account.get_holdings.return_value = _holdings(
        [{"symbol": "005930", "quantity": "10", "lastPrice": "70000"}])
    market = MagicMock()
    market.get_price.return_value = {"lastPrice": "70000"}
    market.get_warnings.return_value = []
    executor = MagicMock()
    executor.estimate_cost.side_effect = (
        lambda side, qty, price: qty * price * 1.00015)
    executor.open_orders.return_value = []
    return RiskManager(LIMITS, account, market, executor)


# ── kill switch (Step 6-2) ────────────────────────────────

def test_kill_switch_blocks_all_signals(rm):
    rm.update_pnl(-200000)
    assert rm.killed is True
    assert rm.approve(BUY) == (False, RejectReason.KILL_SWITCH.value)
    assert rm.approve(SELL) == (False, RejectReason.KILL_SWITCH.value)


def test_kill_switch_boundary(rm):
    rm.update_pnl(-199999.99)          # 한도 직전 ― 미발동
    assert rm.killed is False
    rm.update_pnl(-0.01)               # 정확히 한도 도달 ― 발동
    assert rm.killed is True


def test_kill_switch_no_auto_release_manual_reset_only(rm):
    rm.update_pnl(-200000)
    rm.update_pnl(+500000)             # 회복해도 당일 해제 없음
    assert rm.killed is True
    rm.reset_daily()                   # 익일 수동 리셋
    assert rm.killed is False and rm.daily_pnl == 0.0


# ── 검증 체인 각 단계 (Step 6-3) ──────────────────────────

def test_over_order_limit(rm):
    big = Signal("005930", "BUY", 100, 70000.0, "LIMIT", "t")   # 700만
    ok, reason = rm.approve(big)
    assert (ok, reason) == (False, RejectReason.OVER_ORDER_LIMIT.value)


def test_insufficient_buying_power(rm):
    rm.account.get_buying_power.return_value = 50000.0
    ok, reason = rm.approve(BUY)                                # ~7만 > 5만
    assert (ok, reason) == (False, RejectReason.INSUFFICIENT_BP.value)


def test_over_sellable(rm):
    rm.account.get_sellable.return_value = 5.0
    ok, reason = rm.approve(SELL)                               # 10주 > 5주
    assert (ok, reason) == (False, RejectReason.OVER_SELLABLE.value)


def test_over_position_pct(rm):
    # 현금 200만으로 축소 → 총자산 270만(보유 70만+현금 200만), 상한 20% = 54만
    # 기존 70만 + 신규 7만 = 77만 > 54만 → 거부 (주문금액 자체는 상한 이내)
    rm.account.get_buying_power.return_value = 2_000_000.0
    ok, reason = rm.approve(BUY)
    assert (ok, reason) == (False, RejectReason.OVER_POSITION_PCT.value)


def test_over_concurrent_new_symbol(rm):
    rm.limits = dict(LIMITS, max_concurrent=1)
    sig = Signal("000660", "BUY", 1, 100000.0, "LIMIT", "t")    # 신규 종목
    ok, reason = rm.approve(sig)
    assert (ok, reason) == (False, RejectReason.OVER_CONCURRENT.value)


def test_concurrent_ok_for_existing_symbol(rm):
    rm.limits = dict(LIMITS, max_concurrent=1)
    assert rm.approve(BUY) == (True, None)     # 기존 보유 종목 추가 매수는 허용


def test_duplicate_open_order(rm):
    open_order = MagicMock()
    open_order.symbol, open_order.side = "005930", "BUY"
    rm.executor.open_orders.return_value = [open_order]
    ok, reason = rm.approve(BUY)
    assert (ok, reason) == (False, RejectReason.DUPLICATE_ORDER.value)


def test_warning_stock_blocked(rm):
    rm.market.get_warnings.return_value = [
        {"warningType": "INVESTMENT_RISK"}]
    ok, reason = rm.approve(BUY)
    assert (ok, reason) == (False, RejectReason.WARNING_STOCK.value)


def test_vi_warning_not_blocked_by_default(rm):
    rm.market.get_warnings.return_value = [{"warningType": "VI_STATIC"}]
    assert rm.approve(BUY) == (True, None)     # VI는 기본 차단 대상 아님


def test_warning_api_failure_blocks_conservatively(rm):
    rm.market.get_warnings.side_effect = RuntimeError("down")
    ok, reason = rm.approve(BUY)
    assert (ok, reason) == (False, RejectReason.WARNING_STOCK.value)


# ── 정상 통과 ─────────────────────────────────────────────

def test_normal_buy_approved(rm):
    assert rm.approve(BUY) == (True, None)


def test_normal_sell_approved(rm):
    assert rm.approve(SELL) == (True, None)


def test_market_order_uses_last_price(rm):
    sig = Signal("005930", "BUY", 1, None, "MARKET", "t")      # price=None
    assert rm.approve(sig) == (True, None)
    rm.market.get_price.assert_called_with("005930")
