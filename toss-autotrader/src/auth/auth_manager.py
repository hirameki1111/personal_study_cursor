"""AuthManager ― OAuth 2.0 Client Credentials 토큰 발급·캐싱·선제 갱신 (Phase 1).

- 발급 1회 후 캐싱, expires_in 기준 만료 10분 전 선제 갱신
- 발급 실패 시 지수 백오프 재시도(3회), 한계 초과 시 AuthError → 거래 중단 트리거
- 로깅 시 Secret·토큰 마스킹, threading.Lock으로 중복 갱신 방지

토큰 발급 스펙 (developers.tossinvest.com/docs/auth 기준 ― 실발급으로 최종 확인):
- POST https://openapi.tossinvest.com/oauth2/token
- 자격증명은 form body로 전송 (client_id / client_secret) ― Basic 헤더 아님
- 응답: access_token · token_type · expires_in
- 클라이언트당 유효 토큰 1개 ― 재발급 시 기존 토큰 즉시 무효화, refresh token 없음
"""

import threading
import time

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.exceptions import AuthError

BASE_URL = "https://openapi.tossinvest.com"
TOKEN_PATH = "/oauth2/token"
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
        try:
            resp = self._http.post(
                TOKEN_PATH,
                data={"grant_type": "client_credentials",
                      "client_id": self._id,
                      "client_secret": self._secret})
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            # 서버가 거부한 경우: 상태코드·응답 앞부분만 로깅 (Secret 미포함)
            body = e.response.text[:200]
            logger.error(f"토큰 발급 거부 HTTP {e.response.status_code}: {body}")
            raise AuthError(
                f"token issue rejected: HTTP {e.response.status_code}") from e
        except httpx.HTTPError as e:
            # 네트워크·DNS·타임아웃 등: 예외 타입으로 원인 구분
            logger.error(f"토큰 발급 실패({type(e).__name__}) ― 네트워크·도메인 확인")
            raise AuthError(f"token issue failed: {type(e).__name__}") from e
        data = resp.json()
        self._token = data["access_token"]
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
