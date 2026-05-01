from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.emailing import build_queue_for_day, send_ready_queue


def run_daily_queue_build(session: Session, queue_day: date | None = None, limit: int | None = None) -> int:
    return build_queue_for_day(session, queue_day or date.today(), limit=limit)


def run_daily_queue_send(session: Session, queue_day: date | None = None, limit: int | None = None) -> int:
    return send_ready_queue(session, queue_day or date.today(), limit=limit)
