"""시세 표준 모델 (제안서 Step 2-1).

API 응답키 → 모델 필드 매핑은 공식 OpenAPI JSON 대조 후 확정 [확인 필요].
"""

from pydantic import BaseModel


class PriceTick(BaseModel):
    code: str
    price: float
    volume: int | None = None
    ts: float


class Candle(BaseModel):
    ts: float
    open: float
    high: float
    low: float
    close: float
    volume: int
