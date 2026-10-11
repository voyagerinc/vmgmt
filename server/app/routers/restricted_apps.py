"""Application restriction management router (PRD §20).
Allows restricting any app on any device, with strict blocking or administrator password challenge.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from .. import audit
from ..database import get_db
from ..deps import client_ip, get_current_user, require_roles, resolve_tenant
from ..models import AdminUser, Device, RestrictedApp, Role
from ..schemas import RestrictedAppIn, RestrictedAppOut

router = APIRouter(prefix="/api", tags=["restricted-apps"])
_WRITE = require_roles(Role.SECURITY_ADMIN, Role.IT_ADMIN, Role.CUSTOMER_OWNER)


def _clean_process_name(name: str) -> str:
    s = (name or "").strip().lower()
    if s and not s.endswith(".exe"):
        s += ".exe"
    return s


def _bump_devices(db: Session, tenant_id: str, device_id: str | None = None) -> None:
    query = db.query(Device).filter(Device.tenant_id == tenant_id)
    if device_id:
        query = query.filter(Device.id == device_id)
    for d in query.all():
        d.policy_version += 1


@router.get("/restricted-apps", response_model=list[RestrictedAppOut])
def list_restricted_apps(tenant_id: str | None = Query(None), device_id: str | None = None,
                         db: Session = Depends(get_db), user: AdminUser = Depends(get_current_user)):
    tid = resolve_tenant(user, tenant_id)
    query = db.query(RestrictedApp).filter(RestrictedApp.tenant_id == tid)
    if device_id:
        query = query.filter((RestrictedApp.device_id == device_id) | (RestrictedApp.device_id == None))
    rows = query.order_by(RestrictedApp.app_name).all()

    hosts = dict(db.query(Device.id, Device.hostname).filter(Device.tenant_id == tid).all())
    out = []
    for r in rows:
        item = RestrictedAppOut(
            id=r.id,
            tenant_id=r.tenant_id,
            device_id=r.device_id,
            hostname=hosts.get(r.device_id) if r.device_id else "All Devices",
            app_name=r.app_name,
            process_name=r.process_name,
            require_admin_password=r.require_admin_password,
            has_password=bool(r.password_hash and r.password_salt),
            description=r.description,
            enabled=r.enabled,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        out.append(item)
    return out


@router.get("/devices/{device_id}/restricted-apps", response_model=list[RestrictedAppOut])
def device_restricted_apps(device_id: str, db: Session = Depends(get_db),
                           user: AdminUser = Depends(get_current_user)):
    dev = db.get(Device, device_id)
    if not dev or (user.role != Role.PLATFORM_SUPER_ADMIN and dev.tenant_id != user.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    return list_restricted_apps(tenant_id=dev.tenant_id, device_id=device_id, db=db, user=user)


@router.post("/restricted-apps", response_model=RestrictedAppOut)
def create_restricted_app(body: RestrictedAppIn, request: Request, tenant_id: str | None = Query(None),
                          db: Session = Depends(get_db), user: AdminUser = Depends(_WRITE)):
    tid = resolve_tenant(user, tenant_id)

    dev = None
    if body.device_id:
        dev = db.get(Device, body.device_id)
        if not dev or dev.tenant_id != tid:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid device ID for this tenant")

    proc = _clean_process_name(body.process_name)
    if not proc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Process / executable name is required")

    salt, p_hash = None, None
    if body.require_admin_password:
        if body.admin_password and body.admin_password.strip():
            salt = secrets.token_hex(8)
            p_hash = hashlib.sha256((salt + body.admin_password.strip()).encode("utf-8")).hexdigest()
        else:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Administrator password is required when require_admin_password is enabled")

    rule = RestrictedApp(
        tenant_id=tid,
        device_id=body.device_id or None,
        app_name=body.app_name.strip(),
        process_name=proc,
        require_admin_password=body.require_admin_password,
        password_hash=p_hash,
        password_salt=salt,
        description=body.description,
        enabled=body.enabled,
    )
    db.add(rule)
    _bump_devices(db, tid, body.device_id)
    db.flush()

    audit.record(
        db, action="restricted_app_create", tenant_id=tid, actor_id=user.id, actor_email=user.email,
        target_type="restricted_app", target_id=rule.id,
        new_value={"app_name": rule.app_name, "process_name": rule.process_name,
                   "device_id": rule.device_id, "require_admin_password": rule.require_admin_password},
        source_ip=client_ip(request),
    )
    db.commit()
    db.refresh(rule)

    hostname = dev.hostname if dev else "All Devices"
    return RestrictedAppOut(
        id=rule.id,
        tenant_id=rule.tenant_id,
        device_id=rule.device_id,
        hostname=hostname,
        app_name=rule.app_name,
        process_name=rule.process_name,
        require_admin_password=rule.require_admin_password,
        has_password=bool(rule.password_hash and rule.password_salt),
        description=rule.description,
        enabled=rule.enabled,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


@router.put("/restricted-apps/{rule_id}", response_model=RestrictedAppOut)
def update_restricted_app(rule_id: str, body: RestrictedAppIn, request: Request,
                          db: Session = Depends(get_db), user: AdminUser = Depends(_WRITE)):
    rule = db.get(RestrictedApp, rule_id)
    if not rule or (user.role != Role.PLATFORM_SUPER_ADMIN and rule.tenant_id != user.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Restricted app rule not found")

    old_device_id = rule.device_id
    if body.device_id:
        dev = db.get(Device, body.device_id)
        if not dev or dev.tenant_id != rule.tenant_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid device ID for this tenant")
        rule.device_id = body.device_id
    else:
        rule.device_id = None

    rule.app_name = body.app_name.strip()
    rule.process_name = _clean_process_name(body.process_name)
    rule.require_admin_password = body.require_admin_password
    rule.description = body.description
    rule.enabled = body.enabled

    if body.require_admin_password:
        if body.admin_password and body.admin_password.strip():
            salt = secrets.token_hex(8)
            rule.password_salt = salt
            rule.password_hash = hashlib.sha256((salt + body.admin_password.strip()).encode("utf-8")).hexdigest()
        elif not rule.password_hash:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Administrator password is required for password unlock")
    else:
        rule.password_hash = None
        rule.password_salt = None

    rule.updated_at = datetime.now(timezone.utc)
    _bump_devices(db, rule.tenant_id, old_device_id)
    if rule.device_id != old_device_id:
        _bump_devices(db, rule.tenant_id, rule.device_id)

    audit.record(
        db, action="restricted_app_update", tenant_id=rule.tenant_id, actor_id=user.id, actor_email=user.email,
        target_type="restricted_app", target_id=rule.id,
        new_value={"app_name": rule.app_name, "process_name": rule.process_name,
                   "device_id": rule.device_id, "require_admin_password": rule.require_admin_password},
        source_ip=client_ip(request),
    )
    db.commit()
    db.refresh(rule)

    hosts = dict(db.query(Device.id, Device.hostname).filter(Device.tenant_id == rule.tenant_id).all())
    return RestrictedAppOut(
        id=rule.id,
        tenant_id=rule.tenant_id,
        device_id=rule.device_id,
        hostname=hosts.get(rule.device_id) if rule.device_id else "All Devices",
        app_name=rule.app_name,
        process_name=rule.process_name,
        require_admin_password=rule.require_admin_password,
        has_password=bool(rule.password_hash and rule.password_salt),
        description=rule.description,
        enabled=rule.enabled,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


@router.delete("/restricted-apps/{rule_id}")
def delete_restricted_app(rule_id: str, request: Request, db: Session = Depends(get_db),
                          user: AdminUser = Depends(_WRITE)):
    rule = db.get(RestrictedApp, rule_id)
    if not rule or (user.role != Role.PLATFORM_SUPER_ADMIN and rule.tenant_id != user.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Restricted app rule not found")

    tid = rule.tenant_id
    dev_id = rule.device_id
    name = rule.app_name
    proc = rule.process_name

    db.delete(rule)
    _bump_devices(db, tid, dev_id)
    audit.record(
        db, action="restricted_app_delete", tenant_id=tid, actor_id=user.id, actor_email=user.email,
        target_type="restricted_app", target_id=rule_id,
        old_value={"app_name": name, "process_name": proc},
        source_ip=client_ip(request),
    )
    db.commit()
    return {"ok": True}
