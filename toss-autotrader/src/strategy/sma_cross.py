"""SMA 교차 전략 (제안서 Step 5-3).

※ 시스템 파이프라인 검증용 예시 ― 수익 보장 아님.
실거래 채택은 백테스트(인샘플·아웃오브샘플 분리)·페이퍼 검증 전제.

candles: [{"close": float, ...}] 시간 오름차순 (Candle.from_api 산출물 호환)
"""

import pandas as pd

from src.strategy.base import BaseStrategy, Signal


class SmaCrossStrategy(BaseStrategy):
    def generate(self, candles: list[dict],
                 holdings: dict) -> Signal | None:
        df = pd.DataFrame(candles)
        s = self.params["short_window"]
        l = self.params["long_window"]
        if len(df) < l + 1:
            return None                       # 데이터 부족 → 보류
        df["ss"] = df["close"].rolling(s).mean()
        df["ll"] = df["close"].rolling(l).mean()
        prev, curr = df.iloc[-2], df.iloc[-1]
        code, qty = self.params["stock_code"], self.params["qty"]
        if prev.ss <= prev.ll and curr.ss > curr.ll:   # 골든크로스
            return Signal(code, "BUY", qty, None,
                          "MARKET", "golden_cross")
        if prev.ss >= prev.ll and curr.ss < curr.ll:   # 데드크로스
            return Signal(code, "SELL", qty, None,
                          "MARKET", "dead_cross")
        return None
