"""MarketDataClient ― 현재가·호가·캔들 수집, 이상치 검증 (Phase 2).

- AuthManager 주입·인증 헤더 자동 부착, 공용 HTTP 클라이언트 재사용
- 응답 결측·이상치(0·음수·±30% 급변) 검출 → 해당 사이클 보류 신호
- 분당 호출 카운터: 한도 80% 도달 시 폴링 주기 완화 신호 (Step 2-4)
- 429 응답은 MarketError(rate_limited=True)로 구분 전파
- 엔드포인트 경로는 공식 문서 확인, 응답키는 [확인 필요] ― 실호출로 확정
"""

import time

import httpx
from loguru import logger

from src.auth.auth_manager import AuthManager, BASE_URL
from src.core.exceptions import MarketError

PRICE_SWING_LIMIT = 0.30      # 직전가 대비 ±30% 급변 시 보류


class RateLimitGuard:
    """분당 호출 카운터. 한도의 80% 도달 시 폴링 완화 신호를 낸다.

    실제 분당 한도 수치는 공식 문서 확인 후 설정 [확인 필요].
    한도를 모르는 동안(limit=None)은 카운트만 유지한다.
    """

    def __init__(self, limit_per_min: int | None = None) -> None:
        self.limit = limit_per_min
        self._window_start = time.time()
        self.count = 0

    def record(self) -> None:
        now = time.time()
        if now - self._window_start >= 60:
            self._window_start, self.count = now, 0
        self.count += 1

    def near_limit(self) -> bool:
        """한도 80% 도달 여부 ― True면 호출측이 폴링 주기를 2배 완화."""
        return self.limit is not None and self.count >= self.limit * 0.8


class MarketDataClient:
    def __init__(self, auth: AuthManager,
                 http: httpx.Client | None = None,
                 rate_limit_per_min: int | None = None) -> None:
        self._auth = auth
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=10.0)
        self._last_price: dict[str, float] = {}   # 급변 검증용
        self.rate_guard = RateLimitGuard(rate_limit_per_min)

    def _get(self, path: str, params: dict) -> dict:
        self.rate_guard.record()
        try:
            resp = self._http.get(path, params=params,
                                  headers=self._auth.auth_header())
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning("호출 한도 초과(429) ― 백오프·주기 완화 필요")
                raise MarketError("rate limited (429)") from e
            logger.error(f"시세 조회 거부 HTTP {e.response.status_code}: "
                         f"{e.response.text[:200]}")
            raise MarketError(
                f"market request rejected: HTTP {e.response.status_code}"
            ) from e
        except httpx.HTTPError as e:
            logger.error(f"시세 조회 실패({type(e).__name__})")
            raise MarketError(f"market request failed: {type(e).__name__}") from e
        return resp.json()

    def get_price(self, code: str) -> dict:
        """현재가 조회. 응답키 매핑은 실호출로 확정 [확인 필요]."""
        return self._get("/v1/market/price", {"stockCode": code})

    def get_orderbook(self, code: str) -> dict:
        """호가 조회 [확인 필요: 경로·키]."""
        return self._get("/v1/market/orderbook", {"stockCode": code})

    def get_candles(self, code: str, interval: str,
                    count: int = 200) -> dict:
        """캔들 조회. interval 값 체계는 공식 문서 대조 [확인 필요]."""
        return self._get("/v1/market/candles",
                         {"stockCode": code,
                          "interval": interval, "count": count})

    def validate_price(self, code: str, data: dict) -> bool:
        """결측·0·음수·급변 검증. False면 해당 사이클 전체 보류 (Step 2-3)."""
        price = data.get("price")                  # [확인 필요: 응답키]
        if price is None or price <= 0:
            logger.warning(f"시세 결측/이상치({code}) ― 사이클 보류")
            return False
        prev = self._last_price.get(code)
        if prev and abs(price / prev - 1) > PRICE_SWING_LIMIT:
            logger.warning(f"시세 급변 감지({code}: {prev}→{price}) "
                           "― 재조회 후 판정 권장")
            return False
        self._last_price[code] = price
        return True
