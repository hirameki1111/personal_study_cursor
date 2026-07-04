"""알림 채널 (Phase 8, Step 8-3).

원칙: 알림 실패가 거래 로직을 중단시키지 않는다 (try-except 격리).
"""

import httpx
from loguru import logger


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._url = (f"https://api.telegram.org/bot"
                     f"{bot_token}/sendMessage")
        self._chat_id = chat_id

    def send(self, text: str) -> bool:
        """전송 성공 여부 반환. 실패해도 예외를 전파하지 않음."""
        try:
            r = httpx.post(self._url, timeout=5.0,
                           data={"chat_id": self._chat_id, "text": text})
            r.raise_for_status()
            return True
        except Exception as e:   # 알림 실패가 거래를 막지 않도록 격리
            logger.error(f"알림 전송 실패: {type(e).__name__}")
            return False


class NullNotifier:
    """알림 미설정 시 대체 (dry-run·테스트용)."""

    def send(self, text: str) -> bool:
        logger.info(f"[알림 생략] {text}")
        return True
