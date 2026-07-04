"""Phase 5 전략 엔진 테스트 (제안서 5.4 테스트 플랜).

- 동일 캔들 2회 실행: 동일 신호(결정론)
- 데이터 부족(캔들 < long+1): None 반환(보류)
- 골든/데드크로스: BUY/SELL 신호
- 미등록 전략명 로딩: ValueError → 기동 거부
- Signal 불변성
"""

import dataclasses

import pytest

from src.strategy.base import Signal
from src.strategy.registry import REGISTRY, load_strategy
from src.strategy.sma_cross import SmaCrossStrategy

PARAMS = {"stock_code": "005930", "short_window": 2,
          "long_window": 3, "qty": 1}


def _candles(closes):
    return [{"close": float(c)} for c in closes]


def test_golden_cross_generates_buy():
    st = SmaCrossStrategy(PARAMS)
    # idx3: ss=8.5 <= ll≈8.67 / idx4: ss=10.5 > ll≈9.67 → 골든크로스
    sig = st.generate(_candles([10, 9, 8, 9, 12]), {})
    assert sig is not None and sig.side == "BUY"
    assert sig.reason == "golden_cross" and sig.order_type == "MARKET"


def test_dead_cross_generates_sell():
    st = SmaCrossStrategy(PARAMS)
    # idx3: ss=9.5 >= ll≈9.33 / idx4: ss=7.5 < ll≈8.33 → 데드크로스
    sig = st.generate(_candles([8, 9, 10, 9, 6]), {})
    assert sig is not None and sig.side == "SELL"
    assert sig.reason == "dead_cross"


def test_no_cross_returns_none():
    st = SmaCrossStrategy(PARAMS)
    assert st.generate(_candles([10, 10, 10, 10, 10]), {}) is None


def test_insufficient_data_returns_none():
    st = SmaCrossStrategy(PARAMS)
    assert st.generate(_candles([10, 9, 8]), {}) is None   # < long+1


def test_determinism_same_input_same_signal():
    """G2 조건 ①: 동일 입력 → 동일 신호 (2회 재현)."""
    candles = _candles([10, 9, 8, 9, 12])
    sig1 = SmaCrossStrategy(PARAMS).generate(candles, {})
    sig2 = SmaCrossStrategy(PARAMS).generate(candles, {})
    assert sig1 == sig2                        # frozen dataclass 동등 비교


def test_signal_is_immutable():
    sig = Signal("005930", "BUY", 1, None, "MARKET", "test")
    with pytest.raises(dataclasses.FrozenInstanceError):
        sig.side = "SELL"                      # type: ignore[misc]


def test_registry_loads_by_name():
    st = load_strategy("sma_cross", PARAMS)
    assert isinstance(st, SmaCrossStrategy)


def test_unregistered_strategy_rejected():
    with pytest.raises(ValueError, match="미등록 전략"):
        load_strategy("sma_corss", PARAMS)     # 오타 주입


def test_plugin_addition_without_core_change():
    """신규 전략 추가 = REGISTRY 등록만 (엔진 코어 무변경 확인)."""
    class DummyStrategy(SmaCrossStrategy):
        pass

    REGISTRY["dummy"] = DummyStrategy
    try:
        assert isinstance(load_strategy("dummy", PARAMS), DummyStrategy)
    finally:
        del REGISTRY["dummy"]
