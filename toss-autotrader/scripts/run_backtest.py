"""Phase 7 실데이터 백테스트 스크립트 (Step 7-1·7-2·7-4).

사용법 (toss-autotrader 디렉토리에서):
    python scripts/run_backtest.py [종목코드] [수집봉수]   # 기본 005930, 200

수행 내용:
1. 일봉 캔들 수집(API, 페이지네이션) → data/ 캐시(재실행 시 재사용)
2. 인샘플 70% / 아웃오브샘플 30% 분리
3. settings.yaml의 sma_cross 파라미터로 각각 백테스트
4. 성과지표(수익률·MDD·승률·거래횟수) 병기 출력

주의: 클라이언트당 유효 토큰 1개 ― 자동매매 프로세스 가동 중 실행 금지.
※ SMA 교차는 파이프라인 검증용 예시 전략 ― 성과가 좋아도 수익 보장 아님.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import yaml
from dotenv import load_dotenv

from src.auth.auth_manager import AuthManager
from src.backtest.backtester import Backtester, split_candles
from src.backtest.paper_broker import PaperBroker
from src.core.exceptions import TradingError
from src.market.market_client import MarketDataClient
from src.market.models import Candle
from src.strategy.registry import load_strategy

ROOT = Path(__file__).resolve().parents[1]


def fetch_daily_candles(market: MarketDataClient, code: str,
                        total: int) -> pd.DataFrame:
    cache = ROOT / "data" / f"candles_{code}_1d.json"
    if cache.exists():
        print(f"캐시 사용: {cache.name}")
        raw = json.loads(cache.read_text(encoding="utf-8"))
    else:
        raw, before = [], None
        while len(raw) < total:
            page = market.get_candles(code, "1d",
                                      count=min(200, total - len(raw)),
                                      before=before)
            raw.extend(page["candles"])
            before = page.get("nextBefore")
            if not before:
                break
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(json.dumps(raw, ensure_ascii=False),
                         encoding="utf-8")
        print(f"수집 {len(raw)}봉 → 캐시 저장: {cache.name}")
    ticks = [Candle.from_api(c).model_dump() for c in raw]
    df = pd.DataFrame(ticks).sort_values("ts").reset_index(drop=True)
    return df.drop_duplicates(subset="ts").reset_index(drop=True)


def run(name: str, strategy, candles: pd.DataFrame) -> None:
    broker = PaperBroker(initial_cash=10_000_000)
    m = Backtester(strategy, broker, candles).run()
    print(f"  [{name}] 캔들 {len(candles)}개 ― "
          f"수익률 {m['return']:+.2%} / MDD {m['mdd']:.2%} / "
          f"승률 {m['win_rate']} / 거래 {m['trades']}회")


def main() -> int:
    load_dotenv()
    client_id = os.getenv("TOSS_CLIENT_ID", "")
    client_secret = os.getenv("TOSS_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("오류: .env에 TOSS_CLIENT_ID / TOSS_CLIENT_SECRET을 기재하세요.")
        return 1

    code = sys.argv[1] if len(sys.argv) > 1 else "005930"
    total = int(sys.argv[2]) if len(sys.argv) > 2 else 200

    settings = yaml.safe_load(
        (ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    params = dict(settings["strategies"]["sma_cross"], stock_code=code)

    market = MarketDataClient(AuthManager(client_id, client_secret))
    try:
        candles = fetch_daily_candles(market, code, total)
    except TradingError as e:
        print(f"캔들 수집 실패: {e}")
        return 1

    if len(candles) < params["long_window"] * 3:
        print(f"경고: 캔들 {len(candles)}개는 검증에 부족 ― 수집봉수를 늘리세요.")

    ins, oos = split_candles(candles)
    print(f"\n=== SMA({params['short_window']}/{params['long_window']}) "
          f"백테스트 ({code}) ===")
    run("인샘플 70%", load_strategy("sma_cross", params), ins)
    run("아웃오브샘플 30%", load_strategy("sma_cross", params), oos)
    print("\n※ 인샘플·아웃오브샘플 성과 괴리가 크면 과최적화 신호 (부록 D)")
    print("Phase 7 백테스트 확인 완료 ― DoD 체크리스트에 기록하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
