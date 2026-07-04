"""헬스체크 (Phase 8, Step 8-4).

주기 점검: 토큰 유효 · API 응답 · (선택) DB 기록 가능 여부.
이상 시 알림, 연속 N회 실패 시 안전 종료 판단 신호(should_shutdown).
헬스체크 자체 오류도 격리 ― 거래 루프를 중단시키지 않는다.
"""

from loguru import logger


class HealthChecker:
    def __init__(self, auth, market, notifier, store=None,
                 probe_symbol: str = "005930",
                 shutdown_after: int = 3) -> None:
        self._auth, self._market = auth, market
        self._notifier, self._store = notifier, store
        self._probe = probe_symbol
        self._shutdown_after = shutdown_after
        self.fail_count = 0

    def check(self) -> bool:
        try:
            self._auth.get_token()                    # 토큰 유효
            self._market.get_price(self._probe)       # API 응답
            if self._store is not None:               # DB 기록 가능
                self._store.record_log("INFO", "healthcheck", "ok")
            self.fail_count = 0
            return True
        except Exception as e:
            self.fail_count += 1
            logger.warning(f"헬스체크 실패 {self.fail_count}회: "
                           f"{type(e).__name__}")
            try:
                self._notifier.send(
                    f"⚠ 헬스체크 실패 {self.fail_count}회: {type(e).__name__}")
            except Exception:
                pass                                  # 알림 실패도 격리
            return False

    def should_shutdown(self) -> bool:
        """연속 실패 한계 도달 ― 호출측(Orchestrator)이 안전 종료 판단."""
        return self.fail_count >= self._shutdown_after
