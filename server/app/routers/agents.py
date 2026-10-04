"""Agent-facing API: enrollment, heartbeat, telemetry, evidence upload (PRD §10, §17)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form, status
from sqlalchemy.orm import Session

from .. import audit
from ..config import EVIDENCE_DIR, settings
from ..database import get_db
from ..deps import client_ip, get_agent_device
from ..models import (
    ActivityEvent,
    CaptureReason,
    Device,
    DeviceStatus,
    EnrollmentToken,
    Evidence,
    FileEvent,
    HealthMetric,
    Policy,
    RemoteSession,
    RemoteSessionStatus,
    ScreenshotJob,
    ScreenshotStatus,
    Software,
)
from ..schemas import AgentEnrollIn, AgentEnrollOut, HeartbeatIn, HeartbeatOut
from ..security import encrypt_bytes, sha256_hex, sign_job
from ..services import alert_engine
from ..services import license_service as lic_svc

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.post("/enroll", response_model=AgentEnrollOut)
def enroll(body: AgentEnrollIn, request: Request, db: Session = Depends(get_db)):
    """Idempotent device enrollment (PRD §26.2). Validates license + enrollment token."""
    # validate token
    tok = db.query(EnrollmentToken).filter(EnrollmentToken.token == body.enroll_token).first()
    if not tok or tok.revoked:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid enrollment token")
    if tok.expires_at.replace(tzinfo=timezone.utc) < _now():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Enrollment token expired")
    if tok.max_uses and tok.uses >= tok.max_uses:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Enrollment token exhausted")

    tenant_id = tok.tenant_id
    active_lic = lic_svc.active_license(db, tenant_id)
    if not active_lic or active_lic.id != body.license_id:
        # allow match by tenant even if id differs, but prefer explicit
        if not active_lic:
            raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "No license for tenant")

    # idempotency: same machine_uid re-enrolls into the same device record
    device = db.query(Device).filter(Device.tenant_id == tenant_id,
                                     Device.machine_uid == body.machine_uid).first()
    if device and device.status != DeviceStatus.REVOKED:
        device.hostname = body.hostname
        device.os_name = body.os_name
        device.os_version = body.os_version
        device.ip_address = body.ip_address or client_ip(request)
        device.mac_address = body.mac_address
        device.agent_version = body.agent_version
        device.hardware = body.hardware or device.hardware
        device.status = DeviceStatus.ACTIVE
        device.last_seen = _now()
    else:
        try:
            lic_svc.enforce_enrollment(db, tenant_id)
        except lic_svc.LicenseError as e:
            raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, detail=f"{e.code}: {e.message}")
        device = Device(
            tenant_id=tenant_id, machine_uid=body.machine_uid, hostname=body.hostname,
            os_name=body.os_name, os_version=body.os_version,
            ip_address=body.ip_address or client_ip(request), mac_address=body.mac_address,
            agent_version=body.agent_version, hardware=body.hardware or {},
            status=DeviceStatus.ACTIVE, last_seen=_now(),
        )
        db.add(device)
        tok.uses += 1
        # Set an initial policy target so the first heartbeat pulls the active rule set
        # (agent reports version 0; a target >= current tenant max forces delivery).
        from sqlalchemy import func as _func
        has_pol = db.query(Policy).filter(Policy.tenant_id == tenant_id,
                                          Policy.enabled == True).count() > 0  # noqa: E712
        if has_pol:
            maxv = (db.query(_func.max(Device.policy_version))
                    .filter(Device.tenant_id == tenant_id).scalar()) or 0
            device.policy_version = max(maxv, 1)

    db.flush()
    audit.record(db, action="device_enroll", tenant_id=tenant_id, target_type="device",
                 target_id=device.id, new_value={"hostname": device.hostname},
                 source_ip=client_ip(request))
    db.commit()
    db.refresh(device)
    return AgentEnrollOut(
        device_id=device.id, device_cert=device.device_cert,
        heartbeat_interval=settings.heartbeat_interval_seconds,
        policy_version=device.policy_version, status=device.status.value,
    )


@router.post("/heartbeat", response_model=HeartbeatOut)
def heartbeat(body: HeartbeatIn, request: Request, device: Device = Depends(get_agent_device),
              db: Session = Depends(get_db)):
    """Primary agent sync (PRD §10.3). Ingests telemetry, returns jobs + policy."""
    tid = device.tenant_id
    if body.agent_version:
        device.agent_version = body.agent_version
    if body.ip_address:
        device.ip_address = body.ip_address

    prof = device.collection or {}

    # ---- health ----
    if body.health and prof.get("health", True):
        h = body.health
        db.add(HealthMetric(
            tenant_id=tid, device_id=device.id, cpu_percent=h.cpu_percent, ram_percent=h.ram_percent,
            disk_percent=h.disk_percent, net_up_kbps=h.net_up_kbps, net_down_kbps=h.net_down_kbps,
            battery_percent=h.battery_percent, battery_health=h.battery_health,
            uptime_seconds=h.uptime_seconds, extra=h.extra, ts=h.ts or _now(),
        ))
        alert_engine.process_health(db, tid, device, h.model_dump())

    # ---- activity (per-agent profile) ----
    if prof.get("activity", False):
        for a in body.activity:
            db.add(ActivityEvent(
                tenant_id=tid, device_id=device.id, event_type=a.event_type, application=a.application,
                domain=a.domain, category=a.category, title=a.title,
                duration_seconds=a.duration_seconds, ts=a.ts or _now(), meta=a.meta,
            ))
            alert_engine.process_activity(db, tid, device, a.model_dump())

    # ---- file / DLP events (per-agent profile) ----
    if prof.get("file_events", False):
        for f in body.file_events:
            fe = FileEvent(tenant_id=tid, device_id=device.id, action=f.action, path=f.path,
                           destination=f.destination, extension=f.extension, size_bytes=f.size_bytes,
                           classification=f.classification, ts=f.ts or _now())
            db.add(fe)
            alert_engine.process_file(db, tid, device, f.model_dump())

    # ---- software snapshot diff (per-agent profile) ----
    if body.software is not None and prof.get("software", True):
        _reconcile_software(db, tid, device, body.software)

    device.last_seen = _now()
    db.flush()

    # ---- outbound jobs (only when screenshots are enabled for this agent) ----
    jobs = db.query(ScreenshotJob).filter(
        ScreenshotJob.device_id == device.id,
        ScreenshotJob.status.in_([ScreenshotStatus.REQUESTED]),
    ).all() if prof.get("screenshots", True) else []
    job_payloads = []
    for j in jobs:
        due = (not j.scheduled_for) or j.scheduled_for.replace(tzinfo=timezone.utc) <= _now()
        if not due:
            continue
        signed = sign_job({"job_id": j.id, "device_id": device.id, "reason": j.reason.value,
                           "exp": (_now().timestamp() + 300)})
        j.signed_job = signed
        j.status = ScreenshotStatus.DISPATCHED
        job_payloads.append({"job_id": j.id, "reason": j.reason.value, "signed_job": signed})

    sessions = db.query(RemoteSession).filter(
        RemoteSession.device_id == device.id,
        RemoteSession.status.in_([RemoteSessionStatus.REQUESTED, RemoteSessionStatus.ACTIVE]),
    ).all()
    sess_payloads = [{
        "session_id": s.id, "status": s.status.value, "consent_mode": s.consent_mode,
        "session_key": s.session_key, "clipboard_allowed": s.clipboard_allowed,
        "file_transfer_allowed": s.file_transfer_allowed,
        "expires_at": s.expires_at.isoformat(),
    } for s in sessions]

    # ---- policy push on version change ----
    policies_payload = None
    if body.policy_version != device.policy_version:
        policies_payload = [_policy_dict(p) for p in
                            db.query(Policy).filter(Policy.tenant_id == tid, Policy.enabled == True).all()]  # noqa: E712

    db.commit()
    return HeartbeatOut(
        heartbeat_interval=settings.heartbeat_interval_seconds,
        policy_version=device.policy_version,
        policies=policies_payload,
        collection=device.collection or {},
        screenshot_jobs=job_payloads,
        remote_sessions=sess_payloads,
        server_time=_now(),
    )


@router.post("/screenshot/{job_id}")
async def upload_screenshot(job_id: str, request: Request, file: UploadFile = File(...),
                            device: Device = Depends(get_agent_device), db: Session = Depends(get_db)):
    """Agent uploads captured, policy-approved evidence; stored encrypted (PRD §17.3)."""
    job = db.get(ScreenshotJob, job_id)
    if not job or job.device_id != device.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    raw = await file.read()
    enc = encrypt_bytes(raw)
    ev_dir = EVIDENCE_DIR / device.tenant_id
    ev_dir.mkdir(parents=True, exist_ok=True)
    ev = Evidence(
        tenant_id=device.tenant_id, device_id=device.id, job_id=job.id, reason=job.reason,
        content_type=file.content_type or "image/png", size_bytes=len(raw), sha256=sha256_hex(raw),
        storage_path="", captured_at=_now(),
    )
    db.add(ev)
    db.flush()
    path = ev_dir / f"{ev.id}.enc"
    path.write_bytes(enc)
    ev.storage_path = str(path)
    # retention (tenant override, else global default) — PRD §17.3 / §24.2
    from datetime import timedelta
    from ..models import Tenant
    tenant = db.get(Tenant, device.tenant_id)
    retention_days = (tenant.evidence_retention_days if tenant and tenant.evidence_retention_days
                      else settings.default_evidence_retention_days)
    ev.retention_until = _now() + timedelta(days=retention_days)
    ev.watermark = f"{device.hostname} · {ev.captured_at.isoformat()} · {ev.id}"
    job.status = ScreenshotStatus.CAPTURED
    job.evidence_id = ev.id
    db.commit()
    return {"ok": True, "evidence_id": ev.id}


@router.post("/evidence")
async def upload_auto_evidence(request: Request, reason: str = Form("interval"),
                              file: UploadFile = File(...),
                              device: Device = Depends(get_agent_device), db: Session = Depends(get_db)):
    """Store an unsolicited capture (interval/on-alert) when the agent's profile enables it."""
    prof = device.collection or {}
    if not prof.get("screenshots", True):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Screenshots disabled for this agent")
    raw = await file.read()
    enc = encrypt_bytes(raw)
    ev_dir = EVIDENCE_DIR / device.tenant_id
    ev_dir.mkdir(parents=True, exist_ok=True)
    try:
        rsn = CaptureReason(reason)
    except ValueError:
        rsn = CaptureReason.INTERVAL
    ev = Evidence(tenant_id=device.tenant_id, device_id=device.id, reason=rsn,
                  content_type=file.content_type or "image/png", size_bytes=len(raw),
                  sha256=sha256_hex(raw), storage_path="", captured_at=_now())
    db.add(ev)
    db.flush()
    path = ev_dir / f"{ev.id}.enc"
    path.write_bytes(enc)
    ev.storage_path = str(path)
    from datetime import timedelta
    from ..models import Tenant
    tenant = db.get(Tenant, device.tenant_id)
    days = (tenant.evidence_retention_days if tenant and tenant.evidence_retention_days
            else settings.default_evidence_retention_days)
    ev.retention_until = _now() + timedelta(days=days)
    ev.watermark = f"{device.hostname} · {ev.captured_at.isoformat()} · {ev.id}"
    db.commit()
    return {"ok": True, "evidence_id": ev.id}


def _reconcile_software(db: Session, tid: str, device: Device, items) -> None:
    existing = {s.name.lower(): s for s in
                db.query(Software).filter(Software.device_id == device.id).all()}
    seen = set()
    for it in items:
        key = it.name.lower()
        seen.add(key)
        row = existing.get(key)
        if row:
            row.present = True
            row.last_seen = _now()
            if it.version:
                row.version = it.version
        else:
            row = Software(tenant_id=tid, device_id=device.id, name=it.name, publisher=it.publisher,
                           version=it.version, install_date=it.install_date, present=True)
            db.add(row)
            db.flush()
            alert_engine.process_software_change(db, tid, device, row, "installed")
    for key, row in existing.items():
        if key not in seen and row.present:
            row.present = False
            alert_engine.process_software_change(db, tid, device, row, "removed")


def _policy_dict(p: Policy) -> dict:
    return {"id": p.id, "name": p.name, "priority": p.priority, "scope_type": p.scope_type,
            "scope_value": p.scope_value, "rule": p.rule}
