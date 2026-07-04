"""일일 리포트 (Phase 8, Step 8-5).

장 종료 후 체결·손익·거부·오류 통계를 생성해 알림 발송.
G3~G5 게이트 판정 증적으로 활용된다.
"""

from datetime import date

from src.monitor.db import EventStore


def format_daily_report(summary: dict, daily_pnl: float | None = None,
                        mode: str = "?") -> str:
    if "error" in summary:
        return (f"📋 일일 리포트({summary['date']}) 생성 실패: "
                f"{summary['error']}")
    lines = [
        f"📋 일일 리포트 {summary['date']} (mode={mode})",
        f"주문 {summary['orders']}건 / 체결 {summary['fills']}건",
        f"체결금액 {summary['fill_amount']:,.0f} / "
        f"비용(수수료+세금) {summary['fees']:,.2f}",
        f"리스크 거부 {summary['rejects']}건"
        + (f" ({', '.join(summary['reject_reasons'])})"
           if summary["reject_reasons"] else ""),
        f"오류 로그 {summary['errors']}건",
    ]
    if daily_pnl is not None:
        lines.insert(1, f"실현손익 {daily_pnl:+,.0f}")
    return "\n".join(lines)


def send_daily_report(store: EventStore, notifier,
                      daily_pnl: float | None = None,
                      mode: str = "?", day: date | None = None) -> str:
    """리포트 생성·발송. 실패해도 예외 전파 없음 (거래 격리)."""
    text = format_daily_report(store.daily_summary(day), daily_pnl, mode)
    try:
        notifier.send(text)
    except Exception:
        pass
    return text
