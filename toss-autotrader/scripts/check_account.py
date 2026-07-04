"""Phase 3 실호출 검증 스크립트 ― 계좌 목록·보유·매수가능 조회.

사용법 (toss-autotrader 디렉토리에서):
    python scripts/check_account.py

계좌번호는 일부 마스킹 출력. 잔고 금액이 표시되므로 공유 시 유의.
주의: 클라이언트당 유효 토큰 1개 ― 자동매매 프로세스 가동 중 실행 금지.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from src.account.account_manager import AccountManager
from src.auth.auth_manager import AuthManager
from src.core.exceptions import TradingError


def main() -> int:
    load_dotenv()
    client_id = os.getenv("TOSS_CLIENT_ID", "")
    client_secret = os.getenv("TOSS_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("오류: .env에 TOSS_CLIENT_ID / TOSS_CLIENT_SECRET을 기재하세요.")
        return 1

    account_no = os.getenv("ACCOUNT_NO") or None
    am = AccountManager(AuthManager(client_id, client_secret),
                        account_no=account_no)
    try:
        print("=== 계좌 확인 ===")
        seq = am.account_seq
        print(f"accountSeq: {seq} (X-Tossinvest-Account 헤더에 사용됨)")

        print("\n=== 보유 주식 ===")
        holdings = am.get_holdings()
        positions = am.parse_positions(holdings)
        if positions:
            for sym, qty in positions.items():
                print(f"  {sym}: {qty}주")
        else:
            print("  (보유 종목 없음)")

        print("\n=== 매수 가능 금액 (KRW) ===")
        print(f"  {am.get_buying_power('KRW'):,.0f}원")

        print("\n=== 수수료율 ===")
        for c in am.get_commissions():
            print(f"  {c.get('marketCountry')}: {c.get('commissionRate')}%")

        print("\n=== reconcile 검증 (현재 실잔고 기준) ===")
        print(f"  일치 여부: {am.reconcile(positions)}")
    except TradingError as e:
        print(f"조회 실패: {e}")
        return 1

    print("\nPhase 3 실호출 확인 완료 ― DoD 체크리스트에 기록하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
