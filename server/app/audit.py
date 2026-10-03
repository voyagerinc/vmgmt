"""Audit logging helper (PRD §25). Tamper-evident via per-event correlation + append-only use."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEvent


def record(
    db: Session,
    *,
    action: str,
    tenant_id: str | None = None,
    actor_id: str | None = None,
    actor_email: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    result: str = "success",
    source_ip: str | None = None,
) -> AuditEvent:
    ev = AuditEvent(
        action=action,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_email=actor_email,
        target_type=target_type,
        target_id=target_id,
        old_value=old_value,
        new_value=new_value,
        result=result,
        source_ip=source_ip,
    )
    db.add(ev)
    db.flush()
    return ev
