"""Lightweight audit logging for sensitive actions (Phase 6)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AuditLog


def log_audit(
    session: Session, *, actor: str, action: str, agent_id: int | None = None,
    target_type: str = "", target_id: str = "", detail: str = "", commit: bool = True,
) -> AuditLog:
    entry = AuditLog(
        agent_id=agent_id, actor=actor or "owner", action=action,
        target_type=target_type, target_id=str(target_id), detail=detail[:1000],
    )
    session.add(entry)
    if commit:
        session.commit()
    return entry
