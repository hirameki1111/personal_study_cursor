"""Phase 1 AuthManager 테스트 (제안서 1.4 테스트 플랜).

- 정상 발급: 토큰 수신·만료시각 설정
- 캐싱: 연속 호출 시 재발급 없음
- 만료 임박: 재발급 1회만 발생
- 무효 자격증명: 3회 재시도 후 AuthError
- 보안: 로그에 Secret·토큰 원문 미노출
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from loguru import logger

from src.auth.auth_manager import AuthManager, REFRESH_MARGIN_SEC
from src.core.exceptions import AuthError


def _mock_resp(token="tok_abcdef123456", expires=3600):
    m = MagicMock()
    m.json.return_value = {"access_token": token, "expires_in": expires}
    m.raise_for_status.return_value = None
    return m


@pytest.fixture
def am():
    mgr = AuthManager("test-id", "test-secret-XYZZY")
    # 테스트에서는 재시도 대기(2~20초) 생략
    mgr._issue_token.retry.sleep = lambda _: None
    return mgr


def test_token_issued_and_expiry_set(am):
    with patch.object(am._http, "post", return_value=_mock_resp()):
        token = am.get_token()
    assert token == "tok_abcdef123456"
    # 만료시각 = 발급시각 + expires_in - REFRESH_MARGIN
    assert am._expires_at == pytest.approx(
        time.time() + 3600 - REFRESH_MARGIN_SEC, abs=5)


def test_token_request_uses_body_credentials(am):
    """공식 스펙: 자격증명은 form body ― Basic 헤더 미사용."""
    with patch.object(am._http, "post", return_value=_mock_resp()) as p:
        am.get_token()
    kwargs = p.call_args.kwargs
    data = kwargs.get("data") or (p.call_args.args[1]
                                  if len(p.call_args.args) > 1 else {})
    assert data["grant_type"] == "client_credentials"
    assert data["client_id"] == "test-id"
    assert data["client_secret"] == "test-secret-XYZZY"
    assert "Authorization" not in (kwargs.get("headers") or {})


def test_token_cached_no_duplicate_issue(am):
    with patch.object(am._http, "post", return_value=_mock_resp()) as p:
        am.get_token()
        am.get_token()
    assert p.call_count == 1               # 캐싱 → 1회만 발급


def test_reissue_once_when_expired(am):
    with patch.object(am._http, "post", return_value=_mock_resp()) as p:
        am.get_token()
        am._expires_at = time.time() - 1   # 만료 상태로 조작
        am.get_token()                     # 재발급 1회
        am.get_token()                     # 캐시 사용
    assert p.call_count == 2


def test_invalid_credential_raises_auth_error_after_retries(am):
    import httpx
    with patch.object(am._http, "post",
                      side_effect=httpx.HTTPError("401")) as p:
        with pytest.raises(AuthError):
            am.get_token()
    assert p.call_count == 3               # 지수 백오프 3회 재시도


def test_bearer_header_format(am):
    with patch.object(am._http, "post", return_value=_mock_resp()):
        h = am.auth_header()
    assert h == {"Authorization": "Bearer tok_abcdef123456"}


def test_logs_do_not_expose_secret_or_token(am):
    records: list[str] = []
    sink_id = logger.add(records.append, level="DEBUG")
    try:
        with patch.object(am._http, "post", return_value=_mock_resp()):
            am.get_token()                 # 성공 경로 로그
        import httpx
        with patch.object(am._http, "post",
                          side_effect=httpx.HTTPError("boom")):
            with pytest.raises(AuthError):
                am._issue_token()          # 실패 경로 로그
    finally:
        logger.remove(sink_id)
    joined = "".join(records)
    assert "test-secret-XYZZY" not in joined      # Secret 미노출
    assert "tok_abcdef123456" not in joined       # 토큰 미노출
