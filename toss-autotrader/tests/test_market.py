"""Phase 2 MarketDataClient 테스트 (제안서 2.4 테스트 플랜).

- 조회(모의 응답): 표준 모델 매핑 정상
- 결측/0/음수 응답: validate_price=False → 사이클 보류
- 직전가 대비 ±30% 급변: False 반환
- 호출 한도 임박: 폴링 완화 신호
- 429 응답: MarketError 전파
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.market.market_client import MarketDataClient, RateLimitGuard
from src.market.models import Candle, PriceTick
from src.core.exceptions import MarketError


@pytest.fixture
def client():
    auth = MagicMock()
    auth.auth_header.return_value = {"Authorization": "Bearer tok"}
    http = MagicMock(spec=httpx.Client)
    return MarketDataClient(auth, http=http)


def _resp(json_data, status=200):
    r = MagicMock()
    r.json.return_value = json_data
    if status >= 400:
        err = httpx.HTTPStatusError(
            "err", request=MagicMock(),
            response=MagicMock(status_code=status, text="limit"))
        r.raise_for_status.side_effect = err
    else:
        r.raise_for_status.return_value = None
    return r


def test_get_price_returns_json(client):
    client._http.get.return_value = _resp({"price": 71000})
    assert client.get_price("005930") == {"price": 71000}
    client._http.get.assert_called_once()


def test_models_map_from_response():
    tick = PriceTick(code="005930", price=71000.0, volume=1000, ts=1.0)
    assert tick.price == 71000.0
    candle = Candle(ts=1.0, open=1, high=2, low=0.5, close=1.5, volume=10)
    assert candle.close == 1.5


@pytest.mark.parametrize("bad", [{"price": None}, {"price": 0},
                                 {"price": -100}, {}])
def test_validate_price_rejects_missing_zero_negative(client, bad):
    assert client.validate_price("005930", bad) is False


def test_validate_price_rejects_30pct_swing(client):
    assert client.validate_price("005930", {"price": 70000}) is True
    assert client.validate_price("005930", {"price": 70000 * 1.31}) is False
    # 급변 거부 시 last_price는 갱신되지 않아야 함
    assert client._last_price["005930"] == 70000


def test_validate_price_accepts_normal_move(client):
    client.validate_price("005930", {"price": 70000})
    assert client.validate_price("005930", {"price": 70000 * 1.05}) is True
    assert client._last_price["005930"] == 70000 * 1.05


def test_rate_guard_near_limit_signal():
    g = RateLimitGuard(limit_per_min=10)
    for _ in range(7):
        g.record()
    assert g.near_limit() is False
    g.record()                    # 8회 = 80%
    assert g.near_limit() is True


def test_rate_guard_no_limit_never_signals():
    g = RateLimitGuard(limit_per_min=None)
    for _ in range(1000):
        g.record()
    assert g.near_limit() is False


def test_429_raises_market_error(client):
    client._http.get.return_value = _resp({}, status=429)
    with pytest.raises(MarketError, match="rate limited"):
        client.get_price("005930")


def test_http_error_wrapped_as_market_error(client):
    client._http.get.side_effect = httpx.ConnectError("down")
    with pytest.raises(MarketError, match="ConnectError"):
        client.get_price("005930")
