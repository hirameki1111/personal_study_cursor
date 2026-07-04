"""Phase 0 골격 검증 ― 디렉토리 구조·설정 파일 스모크 테스트."""

import importlib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

PACKAGES = [
    "src",
    "src.core",
    "src.auth",
    "src.market",
    "src.account",
    "src.order",
    "src.strategy",
    "src.risk",
    "src.backtest",
    "src.monitor",
]


def test_packages_importable():
    for pkg in PACKAGES:
        importlib.import_module(pkg)


def test_settings_yaml_schema():
    settings = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    for section in ("runtime", "universe", "strategies", "limits",
                    "sizing", "alerts", "logging"):
        assert section in settings, f"settings.yaml에 {section} 섹션 누락"
    # 안전 원칙: 초기 모드는 반드시 dry_run
    assert settings["runtime"]["mode"] == "dry_run"


def test_env_example_has_required_keys():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET", "ACCOUNT_NO"):
        assert key in text


def test_env_not_tracked_by_git():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in gitignore
