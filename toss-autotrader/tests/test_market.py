"""Phase 2 MarketDataClient 테스트 (제안서 2.4 테스트 플랜).

공식 명세(v1.1.5) 기준: {"result": ...} envelope, lastPrice는 decimal 문자열,
한도는 X-RateLimit-* 헤더 기반.
"""

from unittest.mock import MagicMock

import httpx
import pytest

from src.core.exceptions import MarketError
from src.market.market_client import MarketDataClient, RateLimitGuard
from src.market.models import Candle, PriceTick

PRICE_ITEM = {"symbol": "005930", "timestamp": "2026-03-25T09:30:00+09:00",
              "lastPrice": "72000", "currency": "KRW"}
CANDLE_ITEM = {"timestamp": "2026-03-25T09:00:00+09:00",
               "openPrice": "71600", "highPrice": "72300",
               "lowPrice": "71500", "closePrice": "72000",
               "volume": "3521000", "currency": "KRW"}


@pytest.fixture
def client():
    auth = MagicMock()
    auth.auth_header.return_value = {"Authorization": "Bearer tok"}
    http = MagicMock(spec=httpx.Client)
    return MarketDataClient(auth, http=http)


def _resp(result=None, status=200, headers=None, error_body='{"error":{}}'):
    r = MagicMock()
    r.headers = headers or {}
    r.json.return_value = {"result": result}
    if status >= 400:
        err = httpx.HTTPStatusError(
            "err", request=MagicMock(),
            response=MagicMock(status_code=status, text=error_body))
        r.raise_for_status.side_effect = err
    else:
        r.raise_for_status.return_value = None
    return r


def test_get_price_unwraps_envelope_and_uses_symbols_param(client):
    client._http.get.return_value = _resp([PRICE_ITEM])
    assert client.get_price("005930") == PRICE_ITEM
    params = client._http.get.call_args.kwargs["params"]
    assert params == {"symbols": "005930"}


def test_get_prices_batch(client):
    item2 = dict(PRICE_ITEM, symbol="000660", lastPrice="180000")
    client._http.get.return_value = _resp([PRICE_ITEM, item2])
    out = client.get_prices(["005930", "000660"])
    assert len(out) == 2
    assert client._http.get.call_args.kwargs["params"]["symbols"] == "005930,000660"


def test_empty_price_result_raises(client):
    client._http.get.return_value = _resp([])
    with pytest.raises(MarketError, match="empty"):
        client.get_price("005930")


def test_get_candles_params_and_unwrap(client):
    client._http.get.return_value = _resp(
        {"candles": [CANDLE_ITEM], "nextBefore": None})
    out = client.get_candles("005930", "1d", count=5)
    assert out["candles"] == [CANDLE_ITEM]
    params = client._http.get.call_args.kwargs["params"]
    assert params["interval"] == "1d" and params["count"] == 5
    assert params["adjusted"] is True


def test_get_candles_rejects_bad_interval(client):
    with pytest.raises(ValueError):
        client.get_candles("005930", "5m")


def test_models_map_from_official_response():
    tick = PriceTick.from_api(PRICE_ITEM)
    assert tick.price == 72000.0 and tick.currency == "KRW"
    candle = Candle.from_api(CANDLE_ITEM)
    assert candle.close == 72000.0 and candle.volume == 3521000.0


@pytest.mark.parametrize("bad", [{"lastPrice": None}, {"lastPrice": "0"},
                                 {"lastPrice": "-100"}, {},
                                 {"lastPrice": "abc"}])
def test_validate_price_rejects_missing_zero_negative(client, bad):
    assert client.validate_price("005930", bad) is False


def test_validate_price_rejects_30pct_swing(client):
    assert client.validate_price("005930", {"lastPrice": "70000"}) is True
    assert client.validate_price("005930", {"lastPrice": "91700"}) is False
    # 급변 거부 시 기준가는 오염되지 않아야 함
    assert client._last_price["005930"] == 70000.0


def test_validate_price_accepts_normal_move(client):
    client.validate_price("005930", {"lastPrice": "70000"})
    assert client.validate_price("005930", {"lastPrice": "73500"}) is True
    assert client._last_price["005930"] == 73500.0


def test_rate_guard_header_based_near_limit():
    g = RateLimitGuard()
    g.update("MARKET_DATA", {"X-RateLimit-Limit": "10",
                             "X-RateLimit-Remaining": "5"})
    assert g.near_limit("MARKET_DATA") is False
    g.update("MARKET_DATA", {"X-RateLimit-Limit": "10",
                             "X-RateLimit-Remaining": "2"})
    assert g.near_limit("MARKET_DATA") is True
    # 그룹별 독립 추적
    assert g.near_limit("MARKET_DATA_CHART") is False


def test_rate_guard_ignores_missing_headers():
    g = RateLimitGuard()
    g.update("MARKET_DATA", {})
    assert g.near_limit("MARKET_DATA") is False


def test_429_raises_market_error(client):
    client._http.get.return_value = _resp(status=429)
    with pytest.raises(MarketError, match="rate limited"):
        client.get_price("005930")


def test_http_error_wrapped_as_market_error(client):
    client._http.get.side_effect = httpx.ConnectError("down")
    with pytest.raises(MarketError, match="ConnectError"):
        client.get_price("005930")
