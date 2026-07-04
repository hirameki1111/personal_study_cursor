"""Backtester 코어 ― 미래참조(look-ahead) 방지 (Phase 7, Step 7-2).

원칙:
- 신호는 t 캔들 확정 후 산출, 체결은 t+1 시가 (look-ahead 방지)
- 캔들은 시간 오름차순 DataFrame (open/high/low/close/volume 컬럼)
- 성과지표: 수익률·MDD·승률·거래횟수 (Step 7-4)
- 인샘플/아웃오브샘플 분리는 split_candles()로 수행 (Step 7-1)
"""

import pandas as pd

from src.backtest.paper_broker import PaperBroker


def split_candles(candles: pd.DataFrame,
                  in_sample_ratio: float = 0.7
                  ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """인샘플/아웃오브샘플 분리 (기본 70:30, 시간 순서 유지)."""
    cut = int(len(candles) * in_sample_ratio)
    return candles.iloc[:cut].copy(), candles.iloc[cut:].copy()


class Backtester:
    def __init__(self, strategy, broker: PaperBroker,
                 candles: pd.DataFrame) -> None:
        self.strategy, self.broker = strategy, broker
        self.candles = candles.reset_index(drop=True)
        self.equity_curve: list[float] = []

    def run(self) -> dict:
        symbol = self.strategy.params["stock_code"]
        for t in range(len(self.candles) - 1):
            window = self.candles.iloc[: t + 1]     # t까지 확정 데이터만
            sig = self.strategy.generate(
                window.to_dict("records"), self.broker.positions)
            if sig and sig.side in ("BUY", "SELL"):
                nxt_open = float(self.candles.iloc[t + 1]["open"])
                self.broker.place_order(sig.stock_code, sig.side,
                                        sig.quantity, nxt_open,
                                        sig.order_type)   # t+1 시가 체결
            close_t = float(self.candles.iloc[t]["close"])
            self.equity_curve.append(
                self.broker.equity({symbol: close_t}))
        return self.metrics()

    def metrics(self) -> dict:
        """수익률·MDD·승률·거래횟수 (Step 7-4). 수기 대조 검증 대상."""
        if not self.equity_curve:
            return {"return": 0.0, "mdd": 0.0, "win_rate": None, "trades": 0}
        s = pd.Series(self.equity_curve)
        ret = s.iloc[-1] / s.iloc[0] - 1
        mdd = float(((s - s.cummax()) / s.cummax()).min())

        # 승률: 매도 시점 실현손익 기준 (평균 매수단가 대비)
        wins = losses = 0
        avg_cost: dict[str, tuple[float, float]] = {}   # sym → (qty, avg)
        for tr in self.broker.trade_log:
            sym, qty, fill = tr["symbol"], tr["qty"], tr["fill_price"]
            if tr["side"] == "BUY":
                held_qty, held_avg = avg_cost.get(sym, (0.0, 0.0))
                new_qty = held_qty + qty
                avg_cost[sym] = (
                    new_qty, (held_qty * held_avg + qty * fill) / new_qty)
            else:
                held_qty, held_avg = avg_cost.get(sym, (0.0, 0.0))
                if fill > held_avg:
                    wins += 1
                else:
                    losses += 1
                remain = held_qty - qty
                avg_cost[sym] = (remain, held_avg if remain > 0 else 0.0)
        closed = wins + losses
        return {"return": round(float(ret), 4),
                "mdd": round(mdd, 4),
                "win_rate": round(wins / closed, 4) if closed else None,
                "trades": len(self.broker.trade_log)}
