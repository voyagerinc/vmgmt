"""Tracking profiles (which trackers run on which computers) and the tracking event log."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import audit
from ..database import get_db
from ..deps import client_ip, get_current_user, require_roles, resolve_tenant
from ..models import AdminUser, Device, Role, TrackingEvent, TrackingProfile
from ..services import tracking as trk

router = APIRouter(prefix="/api/tracking", tags=["tracking"])

_WRITE = require_roles(Role.SECURITY_ADMIN, Role.IT_ADMIN, Role.CUSTOMER_OWNER)


def _profile_out(p: TrackingProfile, devices: int) -> dict:
    return {"id": p.id, "name": p.name, "description": p.description, "is_default": p.is_default,
            "settings": trk.normalize_settings(p.settings), "devices": devices,
            "updated_at": p.updated_at}


def _get_profile(db: Session, profile_id: str, tid: str) -> TrackingProfile:
    p = db.get(TrackingProfile, profile_id)
    if not p or p.tenant_id != tid:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tracking profile not found")
    return p


@router.get("/profiles")
def list_profiles(tenant_id: str | None = Query(None), db: Session = Depends(get_db),
                  user: AdminUser = Depends(get_current_user)):
    tid = resolve_tenant(user, tenant_id)
    default = trk.default_profile(db, tid)
    db.commit()
    counts = dict(db.query(Device.tracking_profile_id, func.count(Device.id))
                  .filter(Device.tenant_id == tid).group_by(Device.tracking_profile_id).all())
    out = []
    for p in (db.query(TrackingProfile).filter(TrackingProfile.tenant_id == tid)
              .order_by(TrackingProfile.is_default.desc(), TrackingProfile.name).all()):
        n = counts.get(p.id, 0) + (counts.get(None, 0) if p.id == default.id else 0)
        out.append(_profile_out(p, n))
    return {"profiles": out, "defaults": trk.normalize_settings({}),
            "sync_choices": list(trk.SYNC_CHOICES)}


@router.post("/profiles")
def create_profile(body: dict, request: Request, tenant_id: str | None = Query(None),
                   db: Session = Depends(get_db), user: AdminUser = Depends(_WRITE)):
    tid = resolve_tenant(user, tenant_id)
    name = " ".join(str(body.get("name") or "").split())
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Profile name is required")
    if db.query(TrackingProfile).filter(TrackingProfile.tenant_id == tid,
                                        func.lower(TrackingProfile.name) == name.lower()).first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"A profile named '{name}' already exists")
    trk.default_profile(db, tid)
    p = TrackingProfile(tenant_id=tid, name=name[:120], description=body.get("description"),
                        settings=trk.normalize_settings(body.get("settings")))
    db.add(p)
    db.flush()
    audit.record(db, action="tracking_profile_create", tenant_id=tid, actor_id=user.id,
                 actor_email=user.email, target_type="tracking_profile", target_id=p.id,
                 new_value={"name": p.name, **p.settings}, source_ip=client_ip(request))
    db.commit()
    return _profile_out(p, 0)


@router.put("/profiles/{profile_id}")
def update_profile(profile_id: str, body: dict, request: Request, tenant_id: str | None = Query(None),
                   db: Session = Depends(get_db), user: AdminUser = Depends(_WRITE)):
    tid = resolve_tenant(user, tenant_id)
    p = _get_profile(db, profile_id, tid)
    old = {"name": p.name, **trk.normalize_settings(p.settings)}
    if body.get("name"):
        name = " ".join(str(body["name"]).split())[:120]
        clash = db.query(TrackingProfile).filter(TrackingProfile.tenant_id == tid, TrackingProfile.id != p.id,
                                                 func.lower(TrackingProfile.name) == name.lower()).first()
        if clash:
            raise HTTPException(status.HTTP_409_CONFLICT, f"A profile named '{name}' already exists")
        p.name = name
    if "description" in body:
        p.description = body.get("description")
    if "settings" in body:
        p.settings = trk.normalize_settings(body["settings"], base=trk.normalize_settings(p.settings))
    p.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)   # agents re-read the profile
    audit.record(db, action="tracking_profile_update", tenant_id=tid, actor_id=user.id,
                 actor_email=user.email, target_type="tracking_profile", target_id=p.id,
                 old_value=old, new_value={"name": p.name, **p.settings}, source_ip=client_ip(request))
    db.commit()
    return _profile_out(p, 0)


@router.post("/profiles/{profile_id}/default")
def make_default(profile_id: str, request: Request, tenant_id: str | None = Query(None),
                 db: Session = Depends(get_db), user: AdminUser = Depends(_WRITE)):
    tid = resolve_tenant(user, tenant_id)
    p = _get_profile(db, profile_id, tid)
    for other in db.query(TrackingProfile).filter(TrackingProfile.tenant_id == tid).all():
        other.is_default = other.id == p.id
    # computers explicitly on the new default now simply follow "the default"
    db.query(Device).filter(Device.tenant_id == tid, Device.tracking_profile_id == p.id) \
        .update({Device.tracking_profile_id: None}, synchronize_session=False)
    p.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    audit.record(db, action="tracking_profile_default", tenant_id=tid, actor_id=user.id,
                 actor_email=user.email, target_type="tracking_profile", target_id=p.id,
                 new_value={"name": p.name}, source_ip=client_ip(request))
    db.commit()
    return {"ok": True}


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: str, request: Request, tenant_id: str | None = Query(None),
                   db: Session = Depends(get_db), user: AdminUser = Depends(_WRITE)):
    tid = resolve_tenant(user, tenant_id)
    p = _get_profile(db, profile_id, tid)
    if p.is_default:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This is the company default profile. Make another profile the default first.")
    moved = db.query(Device).filter(Device.tenant_id == tid, Device.tracking_profile_id == p.id) \
        .update({Device.tracking_profile_id: None}, synchronize_session=False)
    audit.record(db, action="tracking_profile_delete", tenant_id=tid, actor_id=user.id,
                 actor_email=user.email, target_type="tracking_profile", target_id=p.id,
                 old_value={"name": p.name, "devices_moved_to_default": moved}, source_ip=client_ip(request))
    db.delete(p)
    db.commit()
    return {"ok": True, "devices_moved_to_default": moved}


@router.get("/devices")
def device_assignments(tenant_id: str | None = Query(None), db: Session = Depends(get_db),
                       user: AdminUser = Depends(get_current_user)):
    """Computers with the profile each one uses (for the assignment screen)."""
    tid = resolve_tenant(user, tenant_id)
    default = trk.default_profile(db, tid)
    db.commit()
    names = {p.id: p.name for p in db.query(TrackingProfile).filter(TrackingProfile.tenant_id == tid).all()}
    rows = db.query(Device).filter(Device.tenant_id == tid).order_by(Device.hostname).all()
    return [{"id": d.id, "hostname": d.hostname, "department": d.department, "location": d.location,
             "current_user": d.current_user, "logged_users": d.logged_users or [],
             "status": d.status.value if d.status else None, "last_seen": d.last_seen,
             "profile_id": d.tracking_profile_id or default.id,
             "profile_name": names.get(d.tracking_profile_id or default.id, default.name),
             "uses_default": not d.tracking_profile_id or d.tracking_profile_id not in names}
            for d in rows]


@router.post("/profiles/{profile_id}/assign")
def assign_profile(profile_id: str, body: dict, request: Request, tenant_id: str | None = Query(None),
                   db: Session = Depends(get_db), user: AdminUser = Depends(_WRITE)):
    """Put the given computers on this profile (the default profile = follow the company default)."""
    tid = resolve_tenant(user, tenant_id)
    p = _get_profile(db, profile_id, tid)
    ids = [str(x) for x in (body.get("device_ids") or [])]
    if not ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Select at least one computer")
    n = db.query(Device).filter(Device.tenant_id == tid, Device.id.in_(ids)) \
        .update({Device.tracking_profile_id: None if p.is_default else p.id}, synchronize_session=False)
    audit.record(db, action="tracking_profile_assign", tenant_id=tid, actor_id=user.id,
                 actor_email=user.email, target_type="tracking_profile", target_id=p.id,
                 new_value={"name": p.name, "devices": n}, source_ip=client_ip(request))
    db.commit()
    return {"ok": True, "assigned": n}


def _period(date_from, date_to, tz_offset):
    """Local-day period -> UTC bounds (accepts YYYY-MM-DD; full timestamps are cut to the day)."""
    from ..services import activity_report as ar
    if not (date_from or date_to):
        return None, None
    return ar.window(str(date_from)[:10] if date_from else None, str(date_to)[:10] if date_to else None, tz_offset)


def _event_query(db: Session, tid: str, category, device_id, event_type, q, date_from, date_to, tz_offset=0):
    date_from, date_to = _period(date_from, date_to, tz_offset)
    qry = db.query(TrackingEvent, Device.hostname).join(Device, Device.id == TrackingEvent.device_id) \
        .filter(TrackingEvent.tenant_id == tid)
    if category:
        qry = qry.filter(TrackingEvent.category == category)
    if device_id:
        qry = qry.filter(TrackingEvent.device_id == device_id)
    if event_type:
        qry = qry.filter(TrackingEvent.event_type == event_type)
    if date_from:
        qry = qry.filter(TrackingEvent.ts >= date_from)
    if date_to:
        qry = qry.filter(TrackingEvent.ts < date_to)
    if q:
        like = f"%{q.strip()}%"
        qry = qry.filter(or_(TrackingEvent.detail.ilike(like), TrackingEvent.file_name.ilike(like),
                             TrackingEvent.target.ilike(like), TrackingEvent.user.ilike(like),
                             Device.hostname.ilike(like)))
    return qry.order_by(TrackingEvent.ts.desc())


def _event_out(e: TrackingEvent, host: str) -> dict:
    return {"id": e.id, "ts": e.ts, "device_id": e.device_id, "hostname": host, "category": e.category,
            "event_type": e.event_type, "user": e.user, "detail": e.detail, "file_name": e.file_name,
            "file_ext": e.file_ext, "file_size": e.file_size, "target": e.target, "meta": e.meta}


@router.get("/events")
def list_events(tenant_id: str | None = Query(None), category: str | None = None,
                device_id: str | None = None, event_type: str | None = None, q: str | None = None,
                date_from: str | None = None, date_to: str | None = None, tz_offset: int = 0,
                limit: int = Query(200, le=1000), offset: int = 0,
                db: Session = Depends(get_db), user: AdminUser = Depends(get_current_user)):
    tid = resolve_tenant(user, tenant_id)
    qry = _event_query(db, tid, category, device_id, event_type, q, date_from, date_to, tz_offset)
    total = qry.count()
    rows = qry.offset(offset).limit(limit).all()
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    last24 = dict(db.query(TrackingEvent.category, func.count(TrackingEvent.id))
                  .filter(TrackingEvent.tenant_id == tid, TrackingEvent.ts >= since)
                  .group_by(TrackingEvent.category).all())
    return {"total": total, "events": [_event_out(e, h) for e, h in rows], "last24h": last24}


@router.get("/events.csv")
def export_events(tenant_id: str | None = Query(None), category: str | None = None,
                  device_id: str | None = None, event_type: str | None = None, q: str | None = None,
                  date_from: str | None = None, date_to: str | None = None, tz_offset: int = 0,
                  db: Session = Depends(get_db), user: AdminUser = Depends(get_current_user)):
    tid = resolve_tenant(user, tenant_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Time (UTC)", "Computer", "User", "Category", "Event", "Detail", "File", "Type",
                "Size (bytes)", "Target (USB / recipients / Wi-Fi)", "From", "To", "CC", "BCC", "Subject"])
    for e, h in _event_query(db, tid, category, device_id, event_type, q, date_from, date_to, tz_offset).limit(50000):
        w.writerow([e.ts.strftime("%Y-%m-%d %H:%M:%S") if e.ts else "", h, e.user or "", e.category,
                    e.event_type, e.detail or "", e.file_name or "", e.file_ext or "",
                    e.file_size if e.file_size is not None else "", e.target or "", *trk.mail_cols(e.meta)])
    return Response(content=buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="tracking_events.csv"'})
