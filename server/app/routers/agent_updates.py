"""Agent updates (console): which agent version each computer runs, and approving updates."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..database import get_db
from ..deps import client_ip, get_current_user, require_roles, resolve_tenant
from ..models import AdminUser, Device, Role
from ..services import agent_update as au

router = APIRouter(prefix="/api/agent-updates", tags=["agent-updates"])
_WRITE = require_roles(Role.IT_ADMIN, Role.CUSTOMER_OWNER)


def _state(db: Session, tid: str) -> dict:
    if settings.license_server:
        au.sync_from_license_server()
    info = au.exe_info() or {}
    ver = info.get("version")
    pol = au.get_policy(db, tid)
    devs = []
    for d in db.query(Device).filter(Device.tenant_id == tid).order_by(Device.hostname).all():
        outdated = au.needs_update(d, ver)
        devs.append({"id": d.id, "hostname": d.hostname, "agent_version": d.agent_version,
                     "last_seen": d.last_seen, "status": d.status.value if d.status else None,
                     "up_to_date": bool(ver) and not outdated,
                     "pending": outdated and au.approved(db, d, ver)})
    return {"available_version": ver, "auto": pol["auto"], "approved_version": pol["approved_version"],
            "devices": devs, "outdated": sum(1 for d in devs if ver and not d["up_to_date"]),
            "note": None if ver else "No agent build with a version is available on this server yet."}


@router.get("")
def get_updates(tenant_id: str | None = Query(None), db: Session = Depends(get_db),
                user: AdminUser = Depends(get_current_user)):
    return _state(db, resolve_tenant(user, tenant_id))


@router.put("")
def set_auto(body: dict, request: Request, tenant_id: str | None = Query(None),
             db: Session = Depends(get_db), user: AdminUser = Depends(_WRITE)):
    tid = resolve_tenant(user, tenant_id)
    auto = bool(body.get("auto"))
    au.set_policy(db, tid, auto=auto)
    audit.record(db, action="agent_auto_update", tenant_id=tid, actor_id=user.id, actor_email=user.email,
                 target_type="setting", target_id=au.KEY, new_value={"auto": auto}, source_ip=client_ip(request))
    db.commit()
    return _state(db, tid)


@router.post("/approve")
def approve(body: dict, request: Request, tenant_id: str | None = Query(None),
            db: Session = Depends(get_db), user: AdminUser = Depends(_WRITE)):
    """{"all": true} = update every computer to the available version;
    {"device_ids": [...]} = update just these computers."""
    tid = resolve_tenant(user, tenant_id)
    ver = (au.exe_info() or {}).get("version")
    if not ver:
        raise HTTPException(status.HTTP_409_CONFLICT, "No agent build with a version is available on this server.")
    if body.get("all"):
        au.set_policy(db, tid, approved_version=ver)
        target = "all"
    else:
        ids = [str(x) for x in (body.get("device_ids") or [])]
        own = {d.id for d in db.query(Device.id).filter(Device.tenant_id == tid, Device.id.in_(ids))}
        if not own:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Select at least one computer")
        pol = au.get_policy(db, tid)
        au.set_policy(db, tid, devices=sorted(set(pol["devices"]) | own))
        target = f"{len(own)} computer(s)"
    audit.record(db, action="agent_update_approve", tenant_id=tid, actor_id=user.id, actor_email=user.email,
                 target_type="agent", target_id=ver, new_value={"version": ver, "target": target},
                 source_ip=client_ip(request))
    db.commit()
    return _state(db, tid)
