"""전략 레지스트리 (제안서 Step 5-4).

전략명 → 클래스 매핑. settings.yaml의 전략명으로 로딩하며,
미등록 전략명은 기동 거부(ValueError) ― 오타로 인한 무전략 운행 방지.
새 전략 추가 시 이 매핑에만 등록 (엔진 코어 무변경 원칙).
"""

from src.strategy.base import BaseStrategy
from src.strategy.sma_cross import SmaCrossStrategy

REGISTRY: dict[str, type[BaseStrategy]] = {
    "sma_cross": SmaCrossStrategy,
}


def load_strategy(name: str, params: dict) -> BaseStrategy:
    if name not in REGISTRY:
        raise ValueError(f"미등록 전략: {name} ― 기동 거부 "
                         f"(등록된 전략: {sorted(REGISTRY)})")
    return REGISTRY[name](params)
