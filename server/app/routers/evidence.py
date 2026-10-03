"""Screenshot requests, evidence access, and remote support sessions (PRD §17, §19).

Every view/download/delete is RBAC-gated and audited; evidence is stored encrypted and
served decrypted only to authorized roles.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from .. import audit
from ..database import get_db
from ..deps import client_ip, get_current_user, require_roles, resolve_tenant
from ..models import (
    AdminUser,
    CaptureReason,
    Device,
    Evidence,
    RemoteSession,
    RemoteSessionStatus,
    Role,
    ScreenshotJob,
    ScreenshotStatus,
)
from ..schemas import RemoteRequestIn, ScreenshotRequestIn
from ..security import decrypt_bytes

router = APIRouter(prefix="/api", tags=["evidence"])

_EVIDENCE_VIEW = require_roles(Role.SECURITY_ADMIN, Role.IT_ADMIN, Role.CUSTOMER_OWNER, Role.AUDITOR)
_SCREENSHOT_REQ = require_roles(Role.SECURITY_ADMIN, Role.IT_ADMIN, Role.CUSTOMER_OWNER)
_REMOTE = require_roles(Role.HELPDESK, Role.IT_ADMIN, Role.CUSTOMER_OWNER)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ----------------------------------------------------------------- screenshots
@router.post("/screenshots/request")
def request_screenshot(body: ScreenshotRequestIn, request: Request, db: Session = Depends(get_db),
                       user: AdminUser = Depends(_SCREENSHOT_REQ)):
    d = db.get(Device, body.device_id)
    if not d or (user.role != Role.PLATFORM_SUPER_ADMIN and d.tenant_id != user.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    job = ScreenshotJob(tenant_id=d.tenant_id, device_id=d.id, reason=body.reason,
                        requested_by=user.email, scheduled_for=body.scheduled_for,
                        status=ScreenshotStatus.REQUESTED)
    db.add(job)
    db.flush()
    audit.record(db, action="screenshot_request", tenant_id=d.tenant_id, actor_id=user.id,
                 actor_email=user.email, target_type="device", target_id=d.id,
                 new_value={"reason": body.reason.value, "job_id": job.id},
                 source_ip=client_ip(request))
    db.commit()
    return {"ok": True, "job_id": job.id, "status": job.status.value}


@router.get("/screenshots")
def list_evidence(tenant_id: str | None = Query(None), device_id: str | None = None,
                  db: Session = Depends(get_db), user: AdminUser = Depends(_EVIDENCE_VIEW)):
    tid = resolve_tenant(user, tenant_id)
    q = db.query(Evidence).filter(Evidence.tenant_id == tid)
    if device_id:
        q = q.filter(Evidence.device_id == device_id)
    rows = q.order_by(Evidence.captured_at.desc()).limit(500).all()
    return [{"id": e.id, "device_id": e.device_id, "reason": e.reason.value,
             "captured_at": e.captured_at, "size_bytes": e.size_bytes, "sha256": e.sha256,
             "watermark": e.watermark, "retention_until": e.retention_until} for e in rows]


@router.get("/screenshots/{evidence_id}/image")
def view_evidence(evidence_id: str, request: Request, db: Session = Depends(get_db),
                  user: AdminUser = Depends(_EVIDENCE_VIEW)):
    e = _scoped_evidence(db, user, evidence_id)
    audit.record(db, action="evidence_view", tenant_id=e.tenant_id, actor_id=user.id,
                 actor_email=user.email, target_type="evidence", target_id=e.id,
                 source_ip=client_ip(request))
    db.commit()
    try:
        data = decrypt_bytes(Path(e.storage_path).read_bytes())
    except Exception:
        raise HTTPException(status.HTTP_410_GONE, "Evidence blob unavailable")
    return Response(content=data, media_type=e.content_type)


@router.get("/screenshots/{evidence_id}/download")
def download_evidence(evidence_id: str, request: Request, db: Session = Depends(get_db),
                      user: AdminUser = Depends(_EVIDENCE_VIEW)):
    e = _scoped_evidence(db, user, evidence_id)
    audit.record(db, action="evidence_download", tenant_id=e.tenant_id, actor_id=user.id,
                 actor_email=user.email, target_type="evidence", target_id=e.id,
                 source_ip=client_ip(request))
    db.commit()
    data = decrypt_bytes(Path(e.storage_path).read_bytes())
    ext = "png" if "png" in e.content_type else "jpg"
    headers = {"Content-Disposition": f'attachment; filename="evidence_{e.id}.{ext}"'}
    return Response(content=data, media_type=e.content_type, headers=headers)


@router.delete("/screenshots/{evidence_id}")
def delete_evidence(evidence_id: str, request: Request, db: Session = Depends(get_db),
                    user: AdminUser = Depends(require_roles(Role.SECURITY_ADMIN, Role.CUSTOMER_OWNER))):
    e = _scoped_evidence(db, user, evidence_id)
    audit.record(db, action="evidence_delete", tenant_id=e.tenant_id, actor_id=user.id,
                 actor_email=user.email, target_type="evidence", target_id=e.id,
                 source_ip=client_ip(request))
    Path(e.storage_path).unlink(missing_ok=True)
    db.delete(e)
    db.commit()
    return {"ok": True}


# ----------------------------------------------------------------- remote support
@router.post("/remote/request")
def request_remote(body: RemoteRequestIn, request: Request, db: Session = Depends(get_db),
                   user: AdminUser = Depends(_REMOTE)):
    d = db.get(Device, body.device_id)
    if not d or (user.role != Role.PLATFORM_SUPER_ADMIN and d.tenant_id != user.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    s = RemoteSession(
        tenant_id=d.tenant_id, device_id=d.id, admin_id=user.id, consent_mode=body.consent_mode,
        clipboard_allowed=body.clipboard_allowed, file_transfer_allowed=body.file_transfer_allowed,
        expires_at=_now() + timedelta(minutes=body.duration_minutes),
        status=RemoteSessionStatus.REQUESTED,
    )
    db.add(s)
    db.flush()
    audit.record(db, action="remote_request", tenant_id=d.tenant_id, actor_id=user.id,
                 actor_email=user.email, target_type="device", target_id=d.id,
                 new_value={"session_id": s.id, "consent_mode": s.consent_mode},
                 source_ip=client_ip(request))
    db.commit()
    return {"ok": True, "session_id": s.id, "session_key": s.session_key,
            "expires_at": s.expires_at}


@router.post("/remote/{session_id}/end")
def end_remote(session_id: str, request: Request, db: Session = Depends(get_db),
               user: AdminUser = Depends(_REMOTE)):
    s = db.get(RemoteSession, session_id)
    if not s or (user.role != Role.PLATFORM_SUPER_ADMIN and s.tenant_id != user.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    s.status = RemoteSessionStatus.ENDED
    s.ended_at = _now()
    audit.record(db, action="remote_end", tenant_id=s.tenant_id, actor_id=user.id,
                 actor_email=user.email, target_type="remote_session", target_id=s.id,
                 source_ip=client_ip(request))
    db.commit()
    return {"ok": True}


@router.get("/remote")
def list_remote(tenant_id: str | None = Query(None), db: Session = Depends(get_db),
                user: AdminUser = Depends(_REMOTE)):
    tid = resolve_tenant(user, tenant_id)
    rows = (db.query(RemoteSession).filter(RemoteSession.tenant_id == tid)
            .order_by(RemoteSession.created_at.desc()).limit(200).all())
    return [{"id": s.id, "device_id": s.device_id, "status": s.status.value,
             "consent_mode": s.consent_mode, "started_at": s.started_at, "ended_at": s.ended_at,
             "expires_at": s.expires_at} for s in rows]


def _scoped_evidence(db: Session, user: AdminUser, evidence_id: str) -> Evidence:
    e = db.get(Evidence, evidence_id)
    if not e or (user.role != Role.PLATFORM_SUPER_ADMIN and e.tenant_id != user.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence not found")
    return e
