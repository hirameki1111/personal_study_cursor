"""Phase 8 모니터링·알림 테스트 (제안서 8.4 테스트 플랜).

- 알림 채널 장애 주입: 거래 미중단(격리)
- DB 적재 실패 주입: 파일 폴백·거래 미중단
- 헬스체크: 실패 카운트·알림·연속 실패 시 종료 판단·성공 시 리셋
- 일일 요약: 체결·손익·거부·오류 통계 정확
- 마스킹: 시크릿·JWT 로그 치환
"""

from unittest.mock import MagicMock, patch

import pytest

from src.monitor.db import EventStore
from src.monitor.healthcheck import HealthChecker
from src.monitor.logging_setup import mask, register_secret
from src.monitor.notifier import TelegramNotifier
from src.monitor.report import format_daily_report, send_daily_report


@pytest.fixture
def store(tmp_path):
    return EventStore("sqlite:///:memory:", fallback_dir=tmp_path)


# ── 알림 격리 (Step 8-3) ──────────────────────────────────

def test_notifier_failure_does_not_raise():
    n = TelegramNotifier("tok", "chat")
    with patch("src.monitor.notifier.httpx.post",
               side_effect=RuntimeError("down")):
        assert n.send("msg") is False          # 예외 전파 없음


def test_notifier_success():
    n = TelegramNotifier("tok", "chat")
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    with patch("src.monitor.notifier.httpx.post", return_value=resp):
        assert n.send("msg") is True


# ── DB 적재·폴백 (Step 8-2) ───────────────────────────────

def test_event_store_records_and_summarizes(store):
    store.record_signal("005930", "BUY", 1, "MARKET", "sma_cross", "golden")
    store.record_order("OID1", "c1", "005930", "BUY", 1, 70000, "PENDING")
    store.update_order_status("OID1", "FILLED")
    store.record_execution("E1", "OID1", 70000, 1, fee=10.5, tax=0)
    store.record_risk_event("reject", "005930", "over_order_limit")
    store.record_log("ERROR", "market", "시세 조회 실패")

    s = store.daily_summary()
    assert s["orders"] == 1 and s["fills"] == 1
    assert s["fill_amount"] == 70000.0 and s["fees"] == 10.5
    assert s["rejects"] == 1 and s["reject_reasons"] == ["over_order_limit"]
    assert s["errors"] == 1


def test_db_failure_falls_back_to_file(store, tmp_path):
    with patch("src.monitor.db.Session",
               side_effect=RuntimeError("db down")):
        store.record_log("INFO", "x", "msg")   # 예외 전파 없어야 함
    fallback = tmp_path / "db_fallback.jsonl"
    assert fallback.exists()
    assert "system_logs" in fallback.read_text(encoding="utf-8")


# ── 헬스체크 (Step 8-4) ───────────────────────────────────

def test_healthcheck_failure_counts_and_notifies():
    auth, market, notifier = MagicMock(), MagicMock(), MagicMock()
    market.get_price.side_effect = RuntimeError("down")
    hc = HealthChecker(auth, market, notifier, shutdown_after=3)
    assert hc.check() is False and hc.fail_count == 1
    hc.check(); hc.check()
    assert hc.should_shutdown() is True
    assert notifier.send.call_count == 3


def test_healthcheck_success_resets_count():
    auth, market, notifier = MagicMock(), MagicMock(), MagicMock()
    hc = HealthChecker(auth, market, notifier)
    hc.fail_count = 2
    assert hc.check() is True
    assert hc.fail_count == 0 and hc.should_shutdown() is False


def test_healthcheck_notifier_failure_isolated():
    auth, market, notifier = MagicMock(), MagicMock(), MagicMock()
    market.get_price.side_effect = RuntimeError("down")
    notifier.send.side_effect = RuntimeError("notify down")
    hc = HealthChecker(auth, market, notifier)
    assert hc.check() is False                 # 알림 실패도 격리


# ── 일일 리포트 (Step 8-5) ────────────────────────────────

def test_daily_report_format(store):
    store.record_order("OID1", "c1", "005930", "BUY", 1, 70000, "FILLED")
    store.record_execution("E1", "OID1", 70000, 1, fee=10.5)
    text = format_daily_report(store.daily_summary(),
                               daily_pnl=-1234, mode="paper")
    assert "체결 1건" in text and "실현손익 -1,234" in text
    assert "mode=paper" in text


def test_send_daily_report_isolated(store):
    notifier = MagicMock()
    notifier.send.side_effect = RuntimeError("down")
    text = send_daily_report(store, notifier, mode="dry_run")
    assert "일일 리포트" in text               # 발송 실패해도 생성은 완료


# ── 마스킹 (Step 8-1) ─────────────────────────────────────

def test_mask_registered_secret_and_jwt():
    register_secret("SUPER-SECRET-VALUE")
    out = mask("secret=SUPER-SECRET-VALUE token=eyJraWQiOiIyMDI2.abc123")
    assert "SUPER-SECRET-VALUE" not in out
    assert "eyJraWQ" not in out
    assert "***MASKED***" in out and "***JWT***" in out
