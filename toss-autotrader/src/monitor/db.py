"""이벤트 영구 적재 (Phase 8, Step 8-2 ― 부록 B DDL 기반).

원칙: 적재 실패가 거래 로직을 중단시키지 않는다 ―
모든 record_* 메서드는 예외를 삼키고 로컬 파일(jsonl) 폴백에 기록한다.
"""

import json
from datetime import datetime, date
from pathlib import Path

from loguru import logger
from sqlalchemy import (Column, DateTime, ForeignKey, Integer, Numeric,
                        Text, create_engine, func, select)
from sqlalchemy.orm import Session, declarative_base

Base = declarative_base()


class SignalRow(Base):
    __tablename__ = "signals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=datetime.now)
    code = Column(Text)
    side = Column(Text)
    qty = Column(Numeric)
    price_type = Column(Text)
    strategy_id = Column(Text)
    reason = Column(Text)


class OrderRow(Base):
    __tablename__ = "orders"
    order_id = Column(Text, primary_key=True)
    client_oid = Column(Text, unique=True)
    code = Column(Text, index=True)
    side = Column(Text)
    qty = Column(Numeric)
    price = Column(Numeric, nullable=True)
    status = Column(Text, index=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now,
                        onupdate=datetime.now)
    signal_id = Column(Integer, ForeignKey("signals.id"), nullable=True)


class ExecutionRow(Base):
    __tablename__ = "executions"
    exec_id = Column(Text, primary_key=True)
    order_id = Column(Text, ForeignKey("orders.order_id"), index=True)
    fill_price = Column(Numeric)
    fill_qty = Column(Numeric)
    filled_at = Column(DateTime, default=datetime.now)
    fee = Column(Numeric, default=0)
    tax = Column(Numeric, default=0)


class RiskEventRow(Base):
    __tablename__ = "risk_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=datetime.now, index=True)
    type = Column(Text)
    target = Column(Text)
    reason = Column(Text)
    ref_id = Column(Text, nullable=True)


class SystemLogRow(Base):
    __tablename__ = "system_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=datetime.now)
    level = Column(Text)
    component = Column(Text)
    message = Column(Text)
    error_code = Column(Text, nullable=True)


class EventStore:
    """전 이벤트 적재 창구. 실패 시 jsonl 폴백 ― 절대 예외를 전파하지 않음."""

    def __init__(self, db_url: str,
                 fallback_dir: str | Path = "logs") -> None:
        self._fallback = Path(fallback_dir) / "db_fallback.jsonl"
        self._engine = create_engine(db_url)
        Base.metadata.create_all(self._engine)

    def _fallback_write(self, table: str, payload: dict) -> None:
        try:
            self._fallback.parent.mkdir(parents=True, exist_ok=True)
            with self._fallback.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"table": table, "ts": str(datetime.now()),
                                    **payload}, ensure_ascii=False,
                                   default=str) + "\n")
        except Exception as e:               # 폴백조차 실패해도 거래는 지속
            logger.error(f"DB 폴백 기록 실패: {e}")

    def _save(self, row, table: str, payload: dict) -> None:
        try:
            with Session(self._engine) as s:
                s.add(row)
                s.commit()
        except Exception as e:
            logger.error(f"DB 적재 실패({table}) ― 파일 폴백: {e}")
            self._fallback_write(table, payload)

    # ── 적재 API ──────────────────────────────────────────

    def record_signal(self, code: str, side: str, qty: float,
                      price_type: str, strategy_id: str,
                      reason: str) -> None:
        p = dict(code=code, side=side, qty=qty, price_type=price_type,
                 strategy_id=strategy_id, reason=reason)
        self._save(SignalRow(**p), "signals", p)

    def record_order(self, order_id: str, client_oid: str, code: str,
                     side: str, qty: float, price: float | None,
                     status: str) -> None:
        p = dict(order_id=order_id, client_oid=client_oid, code=code,
                 side=side, qty=qty, price=price, status=status)
        self._save(OrderRow(**p), "orders", p)

    def update_order_status(self, order_id: str, status: str) -> None:
        try:
            with Session(self._engine) as s:
                row = s.get(OrderRow, order_id)
                if row:
                    row.status = status
                    row.updated_at = datetime.now()
                    s.commit()
        except Exception as e:
            logger.error(f"주문 상태 갱신 실패 ― 파일 폴백: {e}")
            self._fallback_write("orders_status",
                                 {"order_id": order_id, "status": status})

    def record_execution(self, exec_id: str, order_id: str,
                         fill_price: float, fill_qty: float,
                         fee: float = 0, tax: float = 0) -> None:
        p = dict(exec_id=exec_id, order_id=order_id, fill_price=fill_price,
                 fill_qty=fill_qty, fee=fee, tax=tax)
        self._save(ExecutionRow(**p), "executions", p)

    def record_risk_event(self, type_: str, target: str, reason: str,
                          ref_id: str | None = None) -> None:
        p = dict(type=type_, target=target, reason=reason, ref_id=ref_id)
        self._save(RiskEventRow(**p), "risk_events", p)

    def record_log(self, level: str, component: str, message: str,
                   error_code: str | None = None) -> None:
        p = dict(level=level, component=component, message=message,
                 error_code=error_code)
        self._save(SystemLogRow(**p), "system_logs", p)

    # ── 일일 리포트용 집계 (Step 8-5) ─────────────────────

    def daily_summary(self, day: date | None = None) -> dict:
        day = day or date.today()
        try:
            with Session(self._engine) as s:
                def _day(col):
                    return func.date(col) == day.isoformat()

                orders = s.scalars(select(OrderRow)
                                   .where(_day(OrderRow.created_at))).all()
                execs = s.scalars(select(ExecutionRow)
                                  .where(_day(ExecutionRow.filled_at))).all()
                rejects = s.scalars(select(RiskEventRow)
                                    .where(_day(RiskEventRow.ts))).all()
                errors = s.scalars(
                    select(SystemLogRow)
                    .where(_day(SystemLogRow.ts))
                    .where(SystemLogRow.level.in_(("ERROR", "CRITICAL")))
                ).all()
                return {
                    "date": day.isoformat(),
                    "orders": len(orders),
                    "fills": len(execs),
                    "fill_amount": float(sum(
                        e.fill_price * e.fill_qty for e in execs)),
                    "fees": float(sum(e.fee + e.tax for e in execs)),
                    "rejects": len(rejects),
                    "reject_reasons": sorted(
                        {r.reason for r in rejects}),
                    "errors": len(errors),
                }
        except Exception as e:
            logger.error(f"일일 집계 실패: {e}")
            return {"date": day.isoformat(), "error": str(e)}
