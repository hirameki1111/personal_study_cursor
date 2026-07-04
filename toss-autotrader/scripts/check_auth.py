"""Phase 1 실발급 검증 스크립트 ― .env 자격증명으로 토큰 발급 1회 시도.

사용법 (toss-autotrader 디렉토리에서):
    python scripts/check_auth.py

토큰 값은 앞 6자만 표시하고 나머지는 마스킹한다 (Step 1-4).
실패 시 AuthError와 함께 종료 ― 자격증명·엔드포인트([확인 필요])를 점검할 것.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from src.auth.auth_manager import AuthManager
from src.core.exceptions import AuthError


def main() -> int:
    load_dotenv()
    client_id = os.getenv("TOSS_CLIENT_ID", "")
    client_secret = os.getenv("TOSS_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("오류: .env에 TOSS_CLIENT_ID / TOSS_CLIENT_SECRET을 기재하세요.")
        return 1

    # 주의: 클라이언트당 유효 토큰은 1개. 여기서 발급하면 기존 토큰은 즉시 무효화됨
    #       (자동매매 프로세스 가동 중에는 이 스크립트를 실행하지 말 것)
    am = AuthManager(client_id, client_secret)
    try:
        token = am.get_token()
    except AuthError as e:
        print(f"토큰 발급 실패: {e}")
        print("점검 가이드:")
        print("  - 'IP address not allowed' → 개발자 포털에 현재 공인 IP 미등록")
        print("    (토스증권 Open API 설정에서 허용 IP 등록 후 재시도)")
        print("  - HTTP 401 → 자격증명 오류 (Client ID/Secret 재확인)")
        print("  - HTTP 404 → 경로 오류 (공식 문서 docs/auth 대조)")
        print("  - ConnectError/ConnectTimeout → 도메인·네트워크·방화벽 확인")
        return 1

    print(f"토큰 발급 성공: {token[:6]}{'*' * 10} (마스킹)")
    print("Phase 1 실발급 확인 완료 ― DoD 체크리스트에 기록하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
