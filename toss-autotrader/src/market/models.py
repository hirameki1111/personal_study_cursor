"""시세 표준 모델 (제안서 Step 2-1) ― 공식 OpenAPI 명세(v1.1.5) 확정.

명세 요점:
- 가격·수량은 decimal 문자열("72000")로 내려옴 → float 변환
- timestamp는 ISO 8601, 체결 미발생 시 null 가능
- currency는 KRW | USD (unknown enum 값 허용 구현 권장)
"""

from pydantic import BaseModel


class PriceTick(BaseModel):
    """GET /api/v1/prices 응답 result[] 항목의 표준화."""

    symbol: str
    price: float
    currency: str
    ts: str | None = None          # ISO 8601, 체결 미발생 시 None

    @classmethod
    def from_api(cls, d: dict) -> "PriceTick":
        return cls(symbol=d["symbol"],
                   price=float(d["lastPrice"]),
                   currency=d["currency"],
                   ts=d.get("timestamp"))


class Candle(BaseModel):
    """GET /api/v1/candles 응답 result.candles[] 항목의 표준화."""

    ts: str                        # 봉 시작 시각 (ISO 8601)
    open: float
    high: float
    low: float
    close: float
    volume: float                  # 미국주식 소수점 거래 대비 float

    @classmethod
    def from_api(cls, d: dict) -> "Candle":
        return cls(ts=d["timestamp"],
                   open=float(d["openPrice"]),
                   high=float(d["highPrice"]),
                   low=float(d["lowPrice"]),
                   close=float(d["closePrice"]),
                   volume=float(d["volume"]))
