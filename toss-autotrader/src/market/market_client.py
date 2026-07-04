"""MarketDataClient ― 현재가·호가·캔들 수집, 이상치 검증 (Phase 2).

공식 OpenAPI 명세(v1.1.5) 확정 사항:
- GET /api/v1/prices?symbols=005930,000660   (콤마 구분, 최대 200종목)
- GET /api/v1/orderbook?symbol=005930
- GET /api/v1/candles?symbol=&interval=1m|1d&count(≤200)&before&adjusted
- 성공 응답은 {"result": ...} envelope, 실패는 {"error": {requestId, code, message}}
- 한도는 초당 토큰버킷 ― X-RateLimit-Limit / X-RateLimit-Remaining 헤더 제공
  (MARKET_DATA 그룹과 MARKET_DATA_CHART 그룹이 별도 관리됨)
"""

import httpx
from loguru import logger

from src.auth.auth_manager import AuthManager, BASE_URL
from src.core.exceptions import MarketError

PRICE_SWING_LIMIT = 0.30      # 직전가 대비 ±30% 급변 시 보류
CANDLE_INTERVALS = ("1m", "1d")
MAX_CANDLE_COUNT = 200


class RateLimitGuard:
    """응답 헤더(X-RateLimit-*) 기반 한도 추적.

    remaining이 limit의 20% 이하로 내려가면 폴링 완화 신호(near_limit)를 낸다.
    캔들(MARKET_DATA_CHART)과 기타 시세(MARKET_DATA)는 그룹별로 따로 추적한다.
    """

    def __init__(self) -> None:
        self._state: dict[str, tuple[int, int]] = {}   # group → (limit, remaining)

    def update(self, group: str, headers) -> None:
        limit = headers.get("X-RateLimit-Limit")
        remaining = headers.get("X-RateLimit-Remaining")
        if limit is not None and remaining is not None:
            self._state[group] = (int(limit), int(remaining))

    def near_limit(self, group: str) -> bool:
        """한도 잔여 20% 이하 ― True면 호출측이 폴링 주기를 2배 완화."""
        if group not in self._state:
            return False
        limit, remaining = self._state[group]
        return remaining <= limit * 0.2


class MarketDataClient:
    def __init__(self, auth: AuthManager,
                 http: httpx.Client | None = None) -> None:
        self._auth = auth
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=10.0)
        self._last_price: dict[str, float] = {}   # 급변 검증용
        self.rate_guard = RateLimitGuard()

    def _get(self, path: str, params: dict, group: str = "MARKET_DATA") -> dict:
        try:
            resp = self._http.get(path, params=params,
                                  headers=self._auth.auth_header())
            self.rate_guard.update(group, resp.headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning("호출 한도 초과(429) ― 백오프·주기 완화 필요")
                raise MarketError("rate limited (429)") from e
            body = e.response.text[:300]   # error.requestId 포함 ― CS 문의용
            logger.error(f"시세 조회 거부 HTTP {e.response.status_code}: {body}")
            raise MarketError(
                f"market request rejected: HTTP {e.response.status_code}"
            ) from e
        except httpx.HTTPError as e:
            logger.error(f"시세 조회 실패({type(e).__name__})")
            raise MarketError(f"market request failed: {type(e).__name__}") from e
        return resp.json()["result"]          # 성공 envelope 해제

    def get_prices(self, symbols: list[str]) -> list[dict]:
        """현재가 일괄 조회 (최대 200종목). result[] 그대로 반환."""
        return self._get("/api/v1/prices", {"symbols": ",".join(symbols)})

    def get_price(self, symbol: str) -> dict:
        """단일 종목 현재가. {symbol, timestamp, lastPrice, currency}"""
        result = self.get_prices([symbol])
        if not result:
            raise MarketError(f"empty price result for {symbol}")
        return result[0]

    def get_orderbook(self, symbol: str) -> dict:
        """호가 조회. {timestamp, currency, asks[], bids[]} (price/volume은 문자열)"""
        return self._get("/api/v1/orderbook", {"symbol": symbol})

    def get_candles(self, symbol: str, interval: str = "1d",
                    count: int = MAX_CANDLE_COUNT,
                    before: str | None = None,
                    adjusted: bool = True) -> dict:
        """캔들 조회. {candles[], nextBefore} ― nextBefore로 페이지네이션."""
        if interval not in CANDLE_INTERVALS:
            raise ValueError(f"interval은 {CANDLE_INTERVALS} 중 하나여야 함")
        params: dict = {"symbol": symbol, "interval": interval,
                        "count": min(count, MAX_CANDLE_COUNT),
                        "adjusted": adjusted}
        if before:
            params["before"] = before
        return self._get("/api/v1/candles", params, group="MARKET_DATA_CHART")

    def validate_price(self, symbol: str, data: dict) -> bool:
        """결측·0·음수·급변 검증. False면 해당 사이클 전체 보류 (Step 2-3).

        data는 get_price() 반환값(lastPrice는 decimal 문자열).
        """
        raw = data.get("lastPrice")
        try:
            price = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            price = None
        if price is None or price <= 0:
            logger.warning(f"시세 결측/이상치({symbol}) ― 사이클 보류")
            return False
        prev = self._last_price.get(symbol)
        if prev and abs(price / prev - 1) > PRICE_SWING_LIMIT:
            logger.warning(f"시세 급변 감지({symbol}: {prev}→{price}) "
                           "― 재조회 후 판정 권장")
            return False
        self._last_price[symbol] = price
        return True
