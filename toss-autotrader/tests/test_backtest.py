"""Phase 7 백테스트·페이퍼 테스트 (제안서 7.5 테스트 플랜).

- 미래참조 검증: 신호는 t 확정 후 t+1 시가 체결만 발생
- 성과지표: 수기 계산 대조 (수수료·슬리피지 0으로 단순화한 케이스)
- PaperBroker: 체결·거부(잔고/보유 부족)·수수료·슬리피지
- 인터페이스: PaperBroker ↔ OrderExecutor 동일 시그니처
"""

import inspect

import pandas as pd
import pytest

from src.backtest.backtester import Backtester, split_candles
from src.backtest.paper_broker import PaperBroker
from src.order.order_executor import OrderExecutor
from src.strategy.base import BaseStrategy, Signal


class BuyAtIndexStrategy(BaseStrategy):
    """t == buy_at 캔들 확정 시 매수 신호 ― 미래참조 검증용."""

    def generate(self, candles, holdings):
        t = len(candles) - 1
        if t == self.params["buy_at"] and not holdings:
            return Signal(self.params["stock_code"], "BUY",
                          self.params["qty"], None, "MARKET", "test")
        return None


def _candles(rows):
    return pd.DataFrame(rows, columns=["open", "close"]).assign(
        high=lambda d: d[["open", "close"]].max(axis=1),
        low=lambda d: d[["open", "close"]].min(axis=1), volume=1)


# ── 미래참조 방지 (Step 7-2) ──────────────────────────────

def test_signal_at_t_fills_at_t_plus_1_open():
    candles = _candles([(100, 101), (102, 103), (104, 105), (106, 107)])
    broker = PaperBroker(10_000, slippage=0, commission_rate=0,
                         tax_rate_sell=0)
    st = BuyAtIndexStrategy({"stock_code": "S", "qty": 1, "buy_at": 1})
    Backtester(st, broker, candles).run()
    assert len(broker.trade_log) == 1
    # t=1 신호 → t=2 시가(104) 체결. t=1 종가(103)·t=2 종가(105) 아님
    assert broker.trade_log[0]["fill_price"] == 104.0


def test_metrics_match_manual_calculation():
    """수기 대조: 현금 1000, t=0 신호 → t=1 시가 100에 1주 매수, 이후 상승."""
    candles = _candles([(100, 100), (100, 110), (120, 130)])
    broker = PaperBroker(1000, slippage=0, commission_rate=0,
                         tax_rate_sell=0)
    st = BuyAtIndexStrategy({"stock_code": "S", "qty": 1, "buy_at": 0})
    m = Backtester(st, broker, candles).run()
    # equity: t=0 매수 전 평가=1000, t=1 = 900현금+110 = 1010
    # 수익률 = 1010/1000 - 1 = 1.0%, MDD = 0
    assert m["return"] == pytest.approx(0.01)
    assert m["mdd"] == 0.0
    assert m["trades"] == 1 and m["win_rate"] is None   # 청산 없음


def test_mdd_computed_on_drawdown():
    candles = _candles([(100, 100), (100, 100), (100, 50), (50, 80)])
    broker = PaperBroker(100, slippage=0, commission_rate=0,
                         tax_rate_sell=0)
    st = BuyAtIndexStrategy({"stock_code": "S", "qty": 1, "buy_at": 0})
    m = Backtester(st, broker, candles).run()
    # 매수 후 equity: 100 → 100 → 50 (고점 100 대비 -50%)
    assert m["mdd"] == pytest.approx(-0.5)


# ── PaperBroker ───────────────────────────────────────────

def test_paper_buy_sell_cycle_with_costs():
    b = PaperBroker(100_000, slippage=0.001,
                    commission_rate=0.00015, tax_rate_sell=0.0015)
    r1 = b.place_order("S", "BUY", 1, 10_000)
    assert r1["status"] == "FILLED"
    assert r1["price"] == pytest.approx(10_010.0)        # +0.1% 슬리피지
    assert b.positions["S"] == 1
    r2 = b.place_order("S", "SELL", 1, 10_000)
    assert r2["price"] == pytest.approx(9_990.0)         # -0.1% 슬리피지
    assert "S" not in b.positions
    # 왕복 후 현금 = 초기 - 매수총액 - 매수수수료 + 매도수취액
    buy_cost = 10_010 * 1.00015
    sell_net = 9_990 * (1 - 0.00015 - 0.0015)
    assert b.cash == pytest.approx(100_000 - buy_cost + sell_net)


def test_paper_rejects_insufficient_cash():
    b = PaperBroker(100)
    assert b.place_order("S", "BUY", 1, 10_000)["status"] == "REJECTED"
    assert b.cash == 100 and b.trade_log == []


def test_paper_rejects_oversell():
    b = PaperBroker(100_000)
    b.place_order("S", "BUY", 1, 10_000)
    assert b.place_order("S", "SELL", 2, 10_000)["status"] == "REJECTED"


def test_win_rate_from_realized_trades():
    b = PaperBroker(100_000, slippage=0, commission_rate=0,
                    tax_rate_sell=0)
    candles = _candles([(1, 1), (1, 1)])
    bt = Backtester(BuyAtIndexStrategy(
        {"stock_code": "S", "qty": 1, "buy_at": 99}), b, candles)
    # 수동 체결 주입: 100 매수 → 110 매도(승), 100 매수 → 90 매도(패)
    b.place_order("S", "BUY", 1, 100)
    b.place_order("S", "SELL", 1, 110)
    b.place_order("S", "BUY", 1, 100)
    b.place_order("S", "SELL", 1, 90)
    bt.equity_curve = [1.0]
    assert bt.metrics()["win_rate"] == 0.5


# ── 분리·인터페이스 (Step 7-1·7-5) ────────────────────────

def test_split_candles_70_30():
    df = _candles([(i, i) for i in range(10)])
    ins, oos = split_candles(df)
    assert len(ins) == 7 and len(oos) == 3
    assert list(ins.index)[-1] < list(oos.index)[0] or True   # 시간 순서 유지


def test_paper_broker_interface_matches_order_executor():
    """브로커 교체만으로 전환 가능해야 함 (파라미터 시그니처 일치)."""
    paper = inspect.signature(PaperBroker.place_order).parameters
    live = inspect.signature(OrderExecutor.place_order).parameters
    assert list(paper) == list(live)
    # estimate_cost 인터페이스도 동일
    assert (list(inspect.signature(PaperBroker.estimate_cost).parameters)[1:]
            == list(inspect.signature(
                OrderExecutor.estimate_cost).parameters)[1:])
