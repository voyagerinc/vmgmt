"""Read endpoints for software, activity and health telemetry (PRD §13, §14, §15)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, resolve_tenant
from ..models import ActivityEvent, AdminUser, Device, HealthMetric, Software

router = APIRouter(prefix="/api", tags=["inventory"])


@router.get("/software")
def software(tenant_id: str | None = Query(None), device_id: str | None = None,
             list_status: str | None = None, q: str | None = None,
             db: Session = Depends(get_db), user: AdminUser = Depends(get_current_user)):
    tid = resolve_tenant(user, tenant_id)
    query = db.query(Software).filter(Software.tenant_id == tid)
    if device_id:
        query = query.filter(Software.device_id == device_id)
    if list_status:
        query = query.filter(Software.list_status == list_status)
    if q:
        query = query.filter(Software.name.ilike(f"%{q}%"))
    rows = query.order_by(Software.name).limit(1000).all()
    return [{"id": s.id, "device_id": s.device_id, "name": s.name, "publisher": s.publisher,
             "version": s.version, "install_date": s.install_date, "present": s.present,
             "list_status": s.list_status, "last_seen": s.last_seen} for s in rows]


@router.get("/software/summary")
def software_summary(tenant_id: str | None = Query(None), db: Session = Depends(get_db),
                     user: AdminUser = Depends(get_current_user)):
    tid = resolve_tenant(user, tenant_id)
    rows = (db.query(Software.name, func.count(Software.id))
            .filter(Software.tenant_id == tid, Software.present == True)  # noqa: E712
            .group_by(Software.name).order_by(func.count(Software.id).desc()).limit(50).all())
    return [{"name": n, "installs": c} for n, c in rows]


@router.post("/software/{software_id}/list-status")
def set_list_status(software_id: str, value: str, db: Session = Depends(get_db),
                    user: AdminUser = Depends(get_current_user)):
    s = db.get(Software, software_id)
    if s and (user.role.value == "platform_super_admin" or s.tenant_id == user.tenant_id):
        s.list_status = value  # allow/block/unknown
        db.commit()
    return {"ok": True}


@router.get("/activity")
def activity(tenant_id: str | None = Query(None), device_id: str | None = None,
             hours: int = 24, db: Session = Depends(get_db),
             user: AdminUser = Depends(get_current_user)):
    tid = resolve_tenant(user, tenant_id)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = db.query(ActivityEvent).filter(ActivityEvent.tenant_id == tid, ActivityEvent.ts >= since)
    if device_id:
        query = query.filter(ActivityEvent.device_id == device_id)
    rows = query.order_by(ActivityEvent.ts.desc()).limit(1000).all()
    return [{"ts": e.ts, "device_id": e.device_id, "type": e.event_type, "app": e.application,
             "domain": e.domain, "category": e.category, "title": e.title,
             "duration": e.duration_seconds} for e in rows]


@router.get("/activity/top")
def activity_top(tenant_id: str | None = Query(None), by: str = "application", hours: int = 24,
                 db: Session = Depends(get_db), user: AdminUser = Depends(get_current_user)):
    """Top applications/sites by usage duration (PRD §14.1)."""
    tid = resolve_tenant(user, tenant_id)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    col = ActivityEvent.domain if by == "domain" else ActivityEvent.application
    rows = (db.query(col, func.sum(ActivityEvent.duration_seconds), func.count(ActivityEvent.id))
            .filter(ActivityEvent.tenant_id == tid, ActivityEvent.ts >= since, col.isnot(None))
            .group_by(col).order_by(func.sum(ActivityEvent.duration_seconds).desc()).limit(25).all())
    return [{"name": n, "duration_seconds": int(d or 0), "events": c} for n, d, c in rows]


@router.get("/health/latest")
def health_latest(tenant_id: str | None = Query(None), db: Session = Depends(get_db),
                  user: AdminUser = Depends(get_current_user)):
    """Latest health per device (PRD §15)."""
    tid = resolve_tenant(user, tenant_id)
    devices = db.query(Device).filter(Device.tenant_id == tid).all()
    out = []
    for d in devices:
        h = (db.query(HealthMetric).filter(HealthMetric.device_id == d.id)
             .order_by(HealthMetric.ts.desc()).first())
        out.append({
            "device_id": d.id, "hostname": d.hostname, "status": d.status.value,
            "last_seen": d.last_seen,
            "cpu": h.cpu_percent if h else None, "ram": h.ram_percent if h else None,
            "disk": h.disk_percent if h else None, "battery": h.battery_percent if h else None,
            "ts": h.ts if h else None,
        })
    return out


@router.get("/health/{device_id}/series")
def health_series(device_id: str, metric: str = "cpu_percent", hours: int = 24,
                  db: Session = Depends(get_db), user: AdminUser = Depends(get_current_user)):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (db.query(HealthMetric).filter(HealthMetric.device_id == device_id,
                                          HealthMetric.ts >= since)
            .order_by(HealthMetric.ts.asc()).all())
    return [{"ts": r.ts, "value": getattr(r, metric, None)} for r in rows]
