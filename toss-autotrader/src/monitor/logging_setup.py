"""로깅 표준화 (Phase 8, Step 8-1).

- 포맷 통일(ts·level·component·message), 일 단위 파일 로테이션·보존 30일
- 시크릿 마스킹 필터: 등록된 시크릿 문자열·JWT 패턴을 로그에서 치환
"""

import re
import sys
from pathlib import Path

from loguru import logger

_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.?[A-Za-z0-9_\-.]*")
_secrets: list[str] = []


def register_secret(value: str) -> None:
    """마스킹 대상 시크릿 등록 (Client Secret·토큰 등)."""
    if value and value not in _secrets:
        _secrets.append(value)


def mask(text: str) -> str:
    for s in _secrets:
        text = text.replace(s, "***MASKED***")
    return _JWT_RE.sub("***JWT***", text)


def _patch_record(record: dict) -> None:
    record["message"] = mask(record["message"])


def setup_logging(level: str = "INFO",
                  logs_dir: str | Path = "logs") -> None:
    """loguru 전역 설정: 콘솔 + 일 단위 로테이션 파일, 마스킹 적용."""
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = ("{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:8} | "
           "{name}:{function}:{line} - {message}")
    logger.remove()
    logger.configure(patcher=_patch_record)
    logger.add(sys.stderr, level=level, format=fmt)
    logger.add(logs_dir / "trader_{time:YYYY-MM-DD}.log",
               level=level, format=fmt,
               rotation="1 day", retention="30 days",
               encoding="utf-8")
