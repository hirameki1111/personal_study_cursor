"""Stage 1 Telegram 알림 검증 스크립트 (운용계획서 Step 1-2·1-4).

사용법 (toss-autotrader 디렉토리에서):
    python scripts/check_telegram.py

동작:
- .env에 TELEGRAM_BOT_TOKEN만 있고 TELEGRAM_CHAT_ID가 비어 있으면:
  getUpdates로 chat_id 후보를 찾아서 알려줌 (봇에게 먼저 메시지 1회 보낼 것)
- 둘 다 있으면: 테스트 메시지를 발송하고 성공 여부 출력
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not token:
        print("오류: .env에 TELEGRAM_BOT_TOKEN을 기재하세요."
              " (@BotFather → /newbot 으로 발급)")
        return 1

    if not chat_id:
        print("TELEGRAM_CHAT_ID가 비어 있음 → getUpdates로 후보를 찾습니다.")
        print("(아직 안 했다면: 텔레그램에서 봇에게 아무 메시지나 1회 보내고 재실행)")
        try:
            r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates",
                          timeout=10.0)
            r.raise_for_status()
        except Exception as e:
            print(f"getUpdates 실패: {type(e).__name__} ― 토큰 확인")
            return 1
        chats = {str(u["message"]["chat"]["id"]):
                 u["message"]["chat"].get("first_name", "?")
                 for u in r.json().get("result", []) if "message" in u}
        if not chats:
            print("수신 메시지 없음 ― 봇에게 메시지를 먼저 보내고 재실행하세요.")
            return 1
        for cid, name in chats.items():
            print(f"  chat_id 후보: {cid} ({name})")
        print("위 값을 .env의 TELEGRAM_CHAT_ID에 기재 후 재실행하세요.")
        return 0

    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       timeout=10.0,
                       data={"chat_id": chat_id,
                             "text": "✅ toss-autotrader 알림 테스트 성공"})
        r.raise_for_status()
    except Exception as e:
        print(f"발송 실패: {type(e).__name__} ― 토큰/chat_id 확인")
        return 1
    print("발송 성공 ― 폰에서 수신을 확인하세요. (Stage 1 DoD 항목)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
