"""Device registry, detail page and enrollment-token management (PRD §12, §21.2)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..database import get_db
from ..deps import client_ip, get_current_user, require_roles, resolve_tenant
from ..models import (
    Alert,
    Device,
    DeviceStatus,
    EnrollmentToken,
    Evidence,
    HealthMetric,
    Role,
    Software,
    ActivityEvent,
)
from ..schemas import DeviceOut, DevicePatch, EnrollTokenIn, EnrollTokenOut

router = APIRouter(prefix="/api", tags=["devices"])


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(tenant_id: str | None = Query(None), status_filter: str | None = Query(None),
                 q: str | None = Query(None), limit: int = Query(200, le=1000), offset: int = 0,
                 db: Session = Depends(get_db), user: "AdminUser" = Depends(get_current_user)):
    tid = resolve_tenant(user, tenant_id)
    query = db.query(Device).filter(Device.tenant_id == tid)
    if status_filter:
        query = query.filter(Device.status == status_filter)
    if q:
        query = query.filter(Device.hostname.ilike(f"%{q}%"))
    return query.order_by(Device.last_seen.desc().nullslast()).offset(offset).limit(limit).all()


@router.get("/devices/{device_id}", response_model=DeviceOut)
def get_device(device_id: str, db: Session = Depends(get_db),
               user: "AdminUser" = Depends(get_current_user)):
    d = _get_scoped(db, user, device_id)
    return d


@router.get("/devices/{device_id}/detail")
def device_detail(device_id: str, db: Session = Depends(get_db),
                  user: "AdminUser" = Depends(get_current_user)):
    """Single-view device detail page (PRD §21.2)."""
    d = _get_scoped(db, user, device_id)
    recent_health = (db.query(HealthMetric).filter(HealthMetric.device_id == d.id)
                     .order_by(HealthMetric.ts.desc()).limit(50).all())
    software = db.query(Software).filter(Software.device_id == d.id, Software.present == True).all()  # noqa
    events = (db.query(ActivityEvent).filter(ActivityEvent.device_id == d.id)
              .order_by(ActivityEvent.ts.desc()).limit(50).all())
    alerts = (db.query(Alert).filter(Alert.device_id == d.id)
              .order_by(Alert.created_at.desc()).limit(25).all())
    evidence = (db.query(Evidence).filter(Evidence.device_id == d.id)
                .order_by(Evidence.captured_at.desc()).limit(25).all())
    return {
        "device": DeviceOut.model_validate(d).model_dump(),
        "collection": d.collection or {},
        "health": [{"ts": h.ts, "cpu": h.cpu_percent, "ram": h.ram_percent,
                    "disk": h.disk_percent, "battery": h.battery_percent} for h in recent_health],
        "software": [{"name": s.name, "version": s.version, "publisher": s.publisher,
                      "list_status": s.list_status} for s in software],
        "activity": [{"ts": e.ts, "type": e.event_type, "app": e.application,
                      "domain": e.domain, "title": e.title, "duration": e.duration_seconds}
                     for e in events],
        "alerts": [{"id": a.id, "severity": a.severity.value, "rule": a.rule_name,
                    "status": a.status.value, "created_at": a.created_at} for a in alerts],
        "evidence": [{"id": e.id, "captured_at": e.captured_at, "reason": e.reason.value}
                     for e in evidence],
    }


@router.patch("/devices/{device_id}", response_model=DeviceOut)
def patch_device(device_id: str, body: DevicePatch, request: Request, db: Session = Depends(get_db),
                 user: "AdminUser" = Depends(require_roles(Role.IT_ADMIN, Role.SECURITY_ADMIN,
                                                           Role.CUSTOMER_OWNER))):
    d = _get_scoped(db, user, device_id)
    old = {"employee_id": d.employee_id, "department": d.department, "location": d.location}
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(d, k, v)
    audit.record(db, action="device_update", tenant_id=d.tenant_id, actor_id=user.id,
                 actor_email=user.email, target_type="device", target_id=d.id, old_value=old,
                 new_value=body.model_dump(exclude_unset=True), source_ip=client_ip(request))
    db.commit()
    db.refresh(d)
    return d


@router.post("/devices/{device_id}/revoke")
def revoke_device(device_id: str, request: Request, db: Session = Depends(get_db),
                  user: "AdminUser" = Depends(require_roles(Role.IT_ADMIN, Role.SECURITY_ADMIN,
                                                           Role.CUSTOMER_OWNER))):
    d = _get_scoped(db, user, device_id)
    d.status = DeviceStatus.REVOKED
    audit.record(db, action="device_revoke", tenant_id=d.tenant_id, actor_id=user.id,
                 actor_email=user.email, target_type="device", target_id=d.id,
                 source_ip=client_ip(request))
    db.commit()
    return {"ok": True}


@router.put("/devices/{device_id}/collection")
def set_collection(device_id: str, body: dict, request: Request, db: Session = Depends(get_db),
                   user: "AdminUser" = Depends(require_roles(Role.IT_ADMIN, Role.SECURITY_ADMIN,
                                                            Role.CUSTOMER_OWNER))):
    """Set the per-agent data profile (PRD §20). Only enabled categories are collected/shown."""
    d = _get_scoped(db, user, device_id)
    bool_keys = {"health", "software", "activity", "file_events", "screenshots",
                 "email", "website", "keystrokes", "usb", "active_time"}
    cur = dict(d.collection or {})
    for k, v in body.items():
        if k in bool_keys:
            cur[k] = bool(v)
        elif k == "screenshot_interval":
            try:
                cur[k] = max(0, int(v))     # seconds; 0 = on-request only
            except (TypeError, ValueError):
                pass
    d.collection = cur
    d.policy_version += 1          # force the agent to re-sync its profile
    audit.record(db, action="device_collection_update", tenant_id=d.tenant_id, actor_id=user.id,
                 actor_email=user.email, target_type="device", target_id=d.id,
                 new_value=cur, source_ip=client_ip(request))
    db.commit()
    return {"ok": True, "collection": cur, "policy_version": d.policy_version}


@router.post("/devices/{device_id}/resync")
def bump_policy(device_id: str, db: Session = Depends(get_db),
                user: "AdminUser" = Depends(require_roles(Role.IT_ADMIN, Role.SECURITY_ADMIN,
                                                         Role.CUSTOMER_OWNER))):
    d = _get_scoped(db, user, device_id)
    d.policy_version += 1
    db.commit()
    return {"ok": True, "policy_version": d.policy_version}


# ----------------------------------------------------------------- enrollment tokens
@router.post("/enrollment-tokens", response_model=EnrollTokenOut)
def create_enroll_token(body: EnrollTokenIn, tenant_id: str | None = Query(None),
                        db: Session = Depends(get_db),
                        user: "AdminUser" = Depends(require_roles(Role.IT_ADMIN, Role.CUSTOMER_OWNER))):
    tid = resolve_tenant(user, tenant_id)
    ttl = body.ttl_hours or settings.enroll_token_ttl_hours
    tok = EnrollmentToken(tenant_id=tid, label=body.label, max_uses=body.max_uses,
                          expires_at=datetime.now(timezone.utc) + timedelta(hours=ttl),
                          created_by=user.id)
    db.add(tok)
    db.flush()
    audit.record(db, action="enroll_token_create", tenant_id=tid, actor_id=user.id,
                 actor_email=user.email, target_type="enroll_token", target_id=tok.id)
    db.commit()
    db.refresh(tok)
    return tok


@router.get("/enrollment-tokens", response_model=list[EnrollTokenOut])
def list_enroll_tokens(tenant_id: str | None = Query(None), db: Session = Depends(get_db),
                       user: "AdminUser" = Depends(get_current_user)):
    tid = resolve_tenant(user, tenant_id)
    return db.query(EnrollmentToken).filter(EnrollmentToken.tenant_id == tid).all()


@router.post("/enrollment-tokens/{token_id}/revoke")
def revoke_enroll_token(token_id: str, db: Session = Depends(get_db),
                        user: "AdminUser" = Depends(require_roles(Role.IT_ADMIN, Role.CUSTOMER_OWNER))):
    tok = db.get(EnrollmentToken, token_id)
    if not tok or (user.role != Role.PLATFORM_SUPER_ADMIN and tok.tenant_id != user.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token not found")
    tok.revoked = True
    db.commit()
    return {"ok": True}


# ----------------------------------------------------------------- helpers
def _get_scoped(db: Session, user, device_id: str) -> Device:
    d = db.get(Device, device_id)
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    if user.role != Role.PLATFORM_SUPER_ADMIN and d.tenant_id != user.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-tenant access denied")
    return d


from ..models import AdminUser  # noqa: E402  (late import to satisfy annotations)
