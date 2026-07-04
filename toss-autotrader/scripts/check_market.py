"""Phase 2 실호출 검증 스크립트 ― 현재가·캔들 조회 후 응답 구조 출력.

사용법 (toss-autotrader 디렉토리에서):
    python scripts/check_market.py [종목코드]     # 기본값 005930

목적: 공식 API의 실제 응답 JSON 키를 확인해 [확인 필요] 항목을 확정한다.
시세 데이터는 시크릿이 아니므로 출력 내용을 공유해도 안전하다.

주의: 클라이언트당 유효 토큰 1개 ― 자동매매 프로세스 가동 중 실행 금지.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from src.auth.auth_manager import AuthManager
from src.core.exceptions import MarketError, AuthError
from src.market.market_client import MarketDataClient


def main() -> int:
    load_dotenv()
    client_id = os.getenv("TOSS_CLIENT_ID", "")
    client_secret = os.getenv("TOSS_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("오류: .env에 TOSS_CLIENT_ID / TOSS_CLIENT_SECRET을 기재하세요.")
        return 1

    code = sys.argv[1] if len(sys.argv) > 1 else "005930"
    market = MarketDataClient(AuthManager(client_id, client_secret))

    try:
        print(f"=== 현재가 조회 ({code}) ===")
        price = market.get_price(code)
        print(json.dumps(price, ensure_ascii=False, indent=2)[:2000])

        print(f"\n=== 일봉 캔들 조회 ({code}, 최근 5개) ===")
        candles = market.get_candles(code, "day", count=5)
        print(json.dumps(candles, ensure_ascii=False, indent=2)[:3000])
    except (AuthError, MarketError) as e:
        print(f"조회 실패: {e}")
        print("점검: 경로·파라미터가 공식 문서와 다를 수 있음 ― 응답 확인 필요")
        return 1

    print("\n=== validate_price 검증 ===")
    ok = market.validate_price(code, price)
    print(f"validate_price 결과: {ok}"
          f"{'' if ok else ' (응답키가 price가 아닐 수 있음 ― 위 JSON 확인)'}")
    print("\n위 출력(JSON 키 구조)을 공유하면 모델 매핑을 확정할 수 있습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
