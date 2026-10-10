"""Retention & maintenance jobs (PRD §17.3, §24.2, §28, §31)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import ActivityEvent, Evidence, HealthMetric, Tenant, TrackingEvent


def purge_expired_evidence(db: Session) -> int:
    now = datetime.now(timezone.utc)
    rows = db.query(Evidence).filter(Evidence.retention_until.isnot(None),
                                     Evidence.retention_until < now).all()
    n = 0
    for e in rows:
        try:
            Path(e.storage_path).unlink(missing_ok=True)
        except Exception:
            pass
        db.delete(e)
        n += 1
    return n


def purge_old_events(db: Session) -> int:
    now = datetime.now(timezone.utc)
    total = 0
    for t in db.query(Tenant).all():
        cutoff = now - timedelta(days=t.event_retention_days or 90)
        total += db.query(ActivityEvent).filter(
            ActivityEvent.tenant_id == t.id, ActivityEvent.ts < cutoff
        ).delete(synchronize_session=False)
        total += db.query(HealthMetric).filter(
            HealthMetric.tenant_id == t.id, HealthMetric.ts < cutoff
        ).delete(synchronize_session=False)
        total += db.query(TrackingEvent).filter(
            TrackingEvent.tenant_id == t.id, TrackingEvent.ts < cutoff
        ).delete(synchronize_session=False)
    return total
