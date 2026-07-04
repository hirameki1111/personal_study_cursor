"""PaperBroker ― 실거래 OrderExecutor와 동일 인터페이스의 가상 체결기 (Phase 7).

- place_order 시그니처를 OrderExecutor와 동일하게 유지
  → 브로커 교체만으로 페이퍼 ↔ 실거래 전환 (코드 무변경 원칙, Step 7-5)
- 수수료·거래세·슬리피지(기본 0.1%) 반영
- 시장가 주문은 호출측이 전달한 체결 기준가(price)로 슬리피지 적용
"""

from loguru import logger

from src.core.exceptions import OrderError


class PaperBroker:
    def __init__(self, initial_cash: float,
                 slippage: float = 0.001,
                 commission_rate: float = 0.00015,
                 tax_rate_sell: float = 0.0015) -> None:
        self.cash = initial_cash
        self.slippage = slippage
        self.commission_rate = commission_rate
        self.tax_rate_sell = tax_rate_sell
        self.positions: dict[str, float] = {}
        self.trade_log: list[dict] = []

    # OrderExecutor.estimate_cost와 동일 의미
    def estimate_cost(self, side: str, qty: float, price: float) -> float:
        gross = qty * price
        fee = gross * self.commission_rate
        if side == "BUY":
            return gross + fee
        return gross - fee - gross * self.tax_rate_sell

    def place_order(self, symbol: str, side: str, qty: float,
                    price: float | None = None,
                    order_type: str = "MARKET",
                    time_in_force: str = "DAY",
                    client_oid: str | None = None) -> dict:
        """가상 체결. price는 체결 기준가(백테스트: t+1 시가 / 페이퍼: 현재가)."""
        if price is None or price <= 0:
            raise OrderError("페이퍼 체결에는 기준가(price) 필수")
        if qty <= 0:
            raise OrderError(f"잘못된 수량: {qty}")

        fill = price * (1 + self.slippage if side == "BUY"
                        else 1 - self.slippage)
        gross = fill * qty
        fee = gross * self.commission_rate

        if side == "BUY":
            cost = gross + fee
            if cost > self.cash:
                logger.warning(f"페이퍼: 잔고 부족 ― 거부 "
                               f"(필요 {cost:,.0f} > 보유 {self.cash:,.0f})")
                return {"status": "REJECTED", "reason": "insufficient_cash"}
            self.cash -= cost
            self.positions[symbol] = self.positions.get(symbol, 0) + qty
        elif side == "SELL":
            held = self.positions.get(symbol, 0)
            if qty > held:
                logger.warning(f"페이퍼: 보유 부족 ― 거부 "
                               f"({qty} > {held})")
                return {"status": "REJECTED", "reason": "insufficient_position"}
            proceeds = gross - fee - gross * self.tax_rate_sell
            self.cash += proceeds
            self.positions[symbol] = held - qty
            if self.positions[symbol] == 0:
                del self.positions[symbol]
        else:
            raise OrderError(f"잘못된 side: {side}")

        trade = {"symbol": symbol, "side": side, "qty": qty,
                 "fill_price": fill, "fee": fee}
        self.trade_log.append(trade)
        return {"status": "FILLED", "price": fill}

    def equity(self, prices: dict[str, float]) -> float:
        """현금 + 보유 평가금액 (prices: {symbol: 현재가})."""
        pos_val = sum(qty * prices.get(sym, 0.0)
                      for sym, qty in self.positions.items())
        return self.cash + pos_val
