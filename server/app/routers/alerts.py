"""Alerts & notifications (PRD §16, §23)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from .. import audit
from ..database import get_db
from ..deps import client_ip, get_current_user, resolve_tenant
from ..models import AdminUser, Alert, AlertStatus, Notification, Role
from ..schemas import AlertOut, AlertUpdate

router = APIRouter(prefix="/api", tags=["alerts"])


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(tenant_id: str | None = Query(None), status_filter: str | None = None,
                severity: str | None = None, db: Session = Depends(get_db),
                user: AdminUser = Depends(get_current_user)):
    tid = resolve_tenant(user, tenant_id)
    q = db.query(Alert).filter(Alert.tenant_id == tid)
    if status_filter:
        q = q.filter(Alert.status == status_filter)
    if severity:
        q = q.filter(Alert.severity == severity)
    return q.order_by(Alert.created_at.desc()).limit(500).all()


@router.patch("/alerts/{alert_id}", response_model=AlertOut)
def update_alert(alert_id: str, body: AlertUpdate, request: Request, db: Session = Depends(get_db),
                 user: AdminUser = Depends(get_current_user)):
    a = db.get(Alert, alert_id)
    if not a or (user.role != Role.PLATFORM_SUPER_ADMIN and a.tenant_id != user.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    a.status = body.status
    if body.status == AlertStatus.ACKNOWLEDGED:
        a.acknowledged_by = user.email
    if body.resolution_note:
        a.resolution_note = body.resolution_note
    audit.record(db, action="alert_update", tenant_id=a.tenant_id, actor_id=user.id,
                 actor_email=user.email, target_type="alert", target_id=a.id,
                 new_value={"status": body.status.value}, source_ip=client_ip(request))
    db.commit()
    db.refresh(a)
    return a


@router.get("/notifications")
def list_notifications(tenant_id: str | None = Query(None), unread_only: bool = False,
                       db: Session = Depends(get_db), user: AdminUser = Depends(get_current_user)):
    tid = resolve_tenant(user, tenant_id)
    q = db.query(Notification).filter(Notification.tenant_id == tid)
    if unread_only:
        q = q.filter(Notification.read == False)  # noqa: E712
    rows = q.order_by(Notification.created_at.desc()).limit(200).all()
    return [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body,
             "severity": n.severity, "read": n.read, "created_at": n.created_at} for n in rows]


@router.post("/notifications/{notif_id}/read")
def mark_read(notif_id: str, db: Session = Depends(get_db),
              user: AdminUser = Depends(get_current_user)):
    n = db.get(Notification, notif_id)
    if n and (user.role == Role.PLATFORM_SUPER_ADMIN or n.tenant_id == user.tenant_id):
        n.read = True
        db.commit()
    return {"ok": True}
