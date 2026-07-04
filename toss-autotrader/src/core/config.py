"""설정·시크릿 로더 (Phase 9, Step 9-1).

- settings.yaml: 전략·한도·모드 (mode 전환은 이 파일 1곳에서만)
- .env: 자격증명 (git 제외)
"""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
VALID_MODES = ("dry_run", "paper", "live")


def load_settings(path: str | Path | None = None) -> dict:
    path = Path(path) if path else ROOT / "config" / "settings.yaml"
    settings = yaml.safe_load(path.read_text(encoding="utf-8"))
    mode = settings.get("runtime", {}).get("mode")
    if mode not in VALID_MODES:
        raise ValueError(f"runtime.mode는 {VALID_MODES} 중 하나여야 함: {mode}")
    return settings


def load_credentials() -> dict:
    """환경변수 로드. 자격증명 누락 시 기동 거부."""
    load_dotenv(ROOT / ".env")
    creds = {
        "client_id": os.getenv("TOSS_CLIENT_ID", ""),
        "client_secret": os.getenv("TOSS_CLIENT_SECRET", ""),
        "account_no": os.getenv("ACCOUNT_NO") or None,
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN") or None,
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID") or None,
    }
    if not creds["client_id"] or not creds["client_secret"]:
        raise ValueError(".env에 TOSS_CLIENT_ID/TOSS_CLIENT_SECRET 필요")
    return creds
