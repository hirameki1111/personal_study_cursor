"""전략 추상 인터페이스·신호 모델 (제안서 Step 5-1·5-2).

- Signal은 불변(frozen) ― 재현성·감사추적 보장
- 신호는 즉시 주문이 아님: 반드시 RiskManager.approve() 경유
- generate()는 데이터 부족 시 None 반환 (보류 규약)
- 난수·현재시각 의존 코드 금지 (동일 입력 → 동일 출력)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)          # 불변 → 재현성 보장
class Signal:
    stock_code: str
    side: str                    # BUY / SELL / HOLD
    quantity: float
    price: float | None
    order_type: str              # LIMIT / MARKET
    reason: str


class BaseStrategy(ABC):
    def __init__(self, params: dict) -> None:
        self.params = params

    @abstractmethod
    def generate(self, candles: list[dict],
                 holdings: dict) -> Signal | None:
        """캔들·보유 정보 → 신호. 데이터 부족·판단 보류 시 None."""
