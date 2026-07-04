"""Orchestrator ― 메인 실행 진입점 (Phase 9에서 구현).

실행 루프 설계 (제안서 9.1):
  1. 시작: 설정·시크릿 로드 → 모듈 초기화 → 잔고 reconciliation
  2. 휴장일·거래시간 확인 → 비거래시간이면 대기
  3. 매 사이클: 시세 수집 → 이상치 검증 → 전략 신호
     → RiskManager.approve() → OrderExecutor 실행 → 동기화·적재
  4. 종료 조건(kill switch·장 종료·오류 한계) → 안전 종료
  5. 비정상 종료 재기동 시: DB·실잔고 기준 상태 복구

핵심 원칙:
  - 전략 신호는 주문이 아님. 반드시 RiskManager 승인 경유
  - 실행 모드(dry_run/paper/live)는 config/settings.yaml 1곳에서만 전환
  - 알림·로깅 실패가 거래 로직을 중단시키지 않도록 격리
"""


def main() -> None:
    raise NotImplementedError(
        "Phase 9(오케스트레이션)에서 구현 예정. "
        "Phase 1~8 완료 및 G1~G3 게이트 통과가 선행 조건입니다."
    )


if __name__ == "__main__":
    main()
