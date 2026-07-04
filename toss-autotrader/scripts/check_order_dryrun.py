"""Phase 4 dry-run 검증 스크립트 ― 외부 전송 없이 주문 파이프라인 점검.

사용법 (toss-autotrader 디렉토리에서):
    python scripts/check_order_dryrun.py [종목코드]    # 기본 005930

수행 내용:
1. 실계좌 accountSeq 해석 + 실수수료율 조회 (읽기 전용 API만 호출)
2. 현재가 조회 → 지정가 dry-run 주문 시뮬레이션 (POST 전송 0건)
3. 예상 비용(수수료·거래세 포함) 출력

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
from src.market.market_client import MarketDataClient
from src.order.order_executor import OrderExecutor


def main() -> int:
    load_dotenv()
    client_id = os.getenv("TOSS_CLIENT_ID", "")
    client_secret = os.getenv("TOSS_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("오류: .env에 TOSS_CLIENT_ID / TOSS_CLIENT_SECRET을 기재하세요.")
        return 1

    code = sys.argv[1] if len(sys.argv) > 1 else "005930"
    auth = AuthManager(client_id, client_secret)
    account = AccountManager(auth, account_no=os.getenv("ACCOUNT_NO") or None)
    market = MarketDataClient(auth)

    try:
        # 실수수료율 반영 (KR)
        rates = {c["marketCountry"]: float(c["commissionRate"]) / 100
                 for c in account.get_commissions()}
        commission = rates.get("KR", 0.00015)
        print(f"실수수료율(KR): {commission:.5%}")

        ex = OrderExecutor(auth, account, dry_run=True,
                           commission_rate=commission)

        price_data = market.get_price(code)
        price = float(price_data["lastPrice"])
        print(f"현재가({code}): {price:,.0f}")

        print("\n=== dry-run 매수 1주 (지정가=현재가) ===")
        out = ex.place_order(code, "BUY", 1, price=price)
        print(f"결과: {out['status']} (외부 전송 없음)")
        print(f"payload: {out['payload']}")
        print(f"예상 매수 비용: {ex.estimate_cost('BUY', 1, price):,.2f}원")
        print(f"예상 매도 수취액: {ex.estimate_cost('SELL', 1, price):,.2f}원")
    except TradingError as e:
        print(f"실패: {e}")
        return 1

    print("\nPhase 4 dry-run 확인 완료 ― DoD 체크리스트에 기록하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
