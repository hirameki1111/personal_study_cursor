"""AuthManager ― OAuth 2.0 Client Credentials 토큰 발급·캐싱·선제 갱신 (Phase 1).

- 발급 1회 후 캐싱, expires_in 기준 만료 10분 전 선제 갱신
- 발급 실패 시 지수 백오프 재시도(3회), 한계 초과 시 AuthError → 거래 중단 트리거
- 로깅 시 Secret·토큰 마스킹, threading.Lock으로 중복 갱신 방지
- 엔드포인트·응답키는 공식 OpenAPI JSON 대조 후 확정 [확인 필요]
"""

import base64
import threading
import time

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.exceptions import AuthError

BASE_URL = "https://openapi.tossinvest.com"   # [확인 필요]
REFRESH_MARGIN_SEC = 600                       # 만료 10분 전 갱신


class AuthManager:
    def __init__(self, client_id: str, client_secret: str) -> None:
        self._id, self._secret = client_id, client_secret
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()          # 동시 갱신 방지
        self._http = httpx.Client(base_url=BASE_URL, timeout=10.0)

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=20),
           reraise=True)   # 한계 초과 시 RetryError가 아닌 AuthError를 그대로 전파
    def _issue_token(self) -> None:
        basic = base64.b64encode(
            f"{self._id}:{self._secret}".encode()).decode()
        try:
            resp = self._http.post(
                "/oauth2/token",               # [확인 필요: 경로]
                headers={"Authorization": f"Basic {basic}",
                         "Content-Type":
                         "application/x-www-form-urlencoded"},
                data={"grant_type": "client_credentials"})
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("토큰 발급 실패(자격증명·네트워크 확인)")
            raise AuthError("token issue failed") from e
        data = resp.json()
        self._token = data["access_token"]     # [확인 필요: 응답키]
        self._expires_at = (time.time()
                            + data["expires_in"] - REFRESH_MARGIN_SEC)
        logger.info("토큰 발급 완료(값 마스킹)")  # 토큰 값 로깅 금지

    def get_token(self) -> str:
        with self._lock:
            if self._token is None or time.time() >= self._expires_at:
                self._issue_token()
        return self._token  # type: ignore[return-value]

    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.get_token()}"}
