"""Domain deployment & remote repair from the client panel (Windows client server only)."""
from __future__ import annotations

import platform
import threading

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from .. import audit
from ..config import BASE_DIR, settings
from ..database import SessionLocal, get_db
from ..deps import client_ip, get_current_user, require_roles, resolve_tenant
from ..models import AdminUser, DeployJob, Device, Role
from ..services import domain_deploy as dd

router = APIRouter(prefix="/api/deploy", tags=["deploy"])
_WRITE = require_roles(Role.IT_ADMIN, Role.CUSTOMER_OWNER)
AGENT_EXE = BASE_DIR / "agent_dist" / "VoyagerAgent.exe"
IS_WIN = platform.system() == "Windows"


def _tid(user: AdminUser) -> str:
    return resolve_tenant(user, None if user.role == Role.PLATFORM_SUPER_ADMIN else user.tenant_id)


@router.get("/capability")
def capability(user: AdminUser = Depends(get_current_user)):
    """Whether this server can push to the domain (Windows + agent built) and the domain name."""
    domain = None
    if IS_WIN:
        import os
        domain = os.environ.get("USERDNSDOMAIN") or os.environ.get("USERDOMAIN")
    return {"supported": IS_WIN, "has_agent": AGENT_EXE.exists(), "domain": domain,
            "reason": None if IS_WIN else "Domain deployment runs from a Windows client server. This server is not Windows.",
            "server_url": settings.server_public_url}


@router.get("/computers")
def computers(db: Session = Depends(get_db), user: AdminUser = Depends(_WRITE)):
    """Domain computers (Active Directory) + which already run the agent, so the admin can pick."""
    tid = _tid(user)
    found = dd.discover_domain_computers() if IS_WIN else []
    have = {}
    for d in db.query(Device).filter(Device.tenant_id == tid).all():
        if d.hostname:
            have[d.hostname.lower()] = {"status": d.status.value, "agent_version": d.agent_version,
                                        "last_seen": d.last_seen}
    rows = [{"host": h, "enrolled": h.lower() in have, **(have.get(h.lower(), {}))} for h in found]
    seen = {h.lower() for h in found}
    for d in db.query(Device).filter(Device.tenant_id == tid).all():
        if d.hostname and d.hostname.lower() not in seen:
            rows.append({"host": d.hostname, "enrolled": True, "status": d.status.value,
                         "agent_version": d.agent_version, "last_seen": d.last_seen})
    return {"computers": rows, "discovered": len(found)}


@router.post("")
def start_deploy(body: dict, request: Request, db: Session = Depends(get_db), user: AdminUser = Depends(_WRITE)):
    """Install or repair the agent on the chosen computers, as a background job.

    body: {hosts, action: install|repair, admin_user: "DOMAIN\\admin", admin_password}.
    The password is used only for this job and is never stored."""
    if not IS_WIN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Domain deployment runs from a Windows client server.")
    if not AGENT_EXE.exists():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "VoyagerAgent.exe is not on this server yet (Settings → Agent program).")
    tid = _tid(user)
    hosts = [str(h).strip() for h in (body.get("hosts") or []) if str(h).strip()][:2000]
    action = "repair" if body.get("action") == "repair" else "install"
    admin_user = (body.get("admin_user") or "").strip()
    admin_pw = body.get("admin_password") or ""
    if not hosts:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Select at least one computer")
    if not admin_user or not admin_pw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter the domain admin username and password")
    job = DeployJob(tenant_id=tid, action=action, status="running", created_by=user.email,
                    total=len(hosts), targets=[])
    db.add(job)
    db.flush()
    audit.record(db, action="domain_deploy", tenant_id=tid, actor_id=user.id, actor_email=user.email,
                 target_type="deploy_job", target_id=job.id,
                 new_value={"action": action, "computers": len(hosts), "admin_user": admin_user},
                 source_ip=client_ip(request))
    db.commit()
    jid = job.id
    threading.Thread(target=dd.run_job, args=(SessionLocal, jid, tid, hosts, admin_user, admin_pw,
                                              AGENT_EXE, settings.server_public_url, action),
                     name=f"deploy-{jid[:8]}", daemon=True).start()
    return {"ok": True, "job_id": jid, "total": len(hosts)}


@router.get("/jobs")
def jobs(limit: int = Query(20, le=100), db: Session = Depends(get_db), user: AdminUser = Depends(_WRITE)):
    tid = _tid(user)
    rows = (db.query(DeployJob).filter(DeployJob.tenant_id == tid)
            .order_by(DeployJob.created_at.desc()).limit(limit).all())
    return [{"id": j.id, "action": j.action, "status": j.status, "created_by": j.created_by,
             "total": j.total, "succeeded": j.succeeded, "failed": j.failed, "created_at": j.created_at,
             "finished_at": j.finished_at, "targets": j.targets or []} for j in rows]


@router.get("/jobs/{job_id}")
def job_detail(job_id: str, db: Session = Depends(get_db), user: AdminUser = Depends(_WRITE)):
    tid = _tid(user)
    j = db.get(DeployJob, job_id)
    if not j or j.tenant_id != tid:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return {"id": j.id, "action": j.action, "status": j.status, "total": j.total,
            "succeeded": j.succeeded, "failed": j.failed, "targets": j.targets or [],
            "created_at": j.created_at, "finished_at": j.finished_at}
