"""Alert & notification engine (PRD §16, §23). Evaluates telemetry against policies."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import (
    Alert,
    AlertSeverity,
    CaptureReason,
    Device,
    Notification,
    Policy,
    ScreenshotJob,
    Software,
)
from . import policy_engine

# in-memory per-device breach state for sustained-threshold rules
_DEVICE_STATE: dict[str, dict] = {}


def _state(device_id: str) -> dict:
    return _DEVICE_STATE.setdefault(device_id, {})


def _raise_alert(db: Session, tenant_id: str, device: Device, policy: Policy | None,
                 rule_name: str, severity: str, message: str,
                 current_value: str | None = None, threshold: str | None = None) -> Alert:
    alert = Alert(
        tenant_id=tenant_id,
        device_id=device.id if device else None,
        policy_id=policy.id if policy else None,
        rule_name=rule_name,
        severity=AlertSeverity(severity) if severity in AlertSeverity._value2member_map_ else AlertSeverity.MEDIUM,
        message=message,
        current_value=current_value,
        threshold=threshold,
    )
    db.add(alert)
    db.add(Notification(tenant_id=tenant_id, kind="alert", title=rule_name,
                        body=message, severity=severity))
    db.flush()
    return alert


def _run_actions(db: Session, tenant_id: str, device: Device, policy: Policy,
                 actions: list[dict], sample: dict) -> None:
    for act in actions:
        kind = act.get("action", "log")
        if kind == "alert":
            alert = _raise_alert(
                db, tenant_id, device, policy, policy.name,
                act.get("severity", "medium"),
                act.get("message", policy.description or policy.name),
                current_value=str(sample.get(_primary_field(policy), "")),
                threshold=str(_primary_value(policy)),
            )
            if act.get("screenshot") or any(a.get("action") == "screenshot" for a in actions):
                alert.evidence_id = None  # linked once captured
        elif kind == "screenshot":
            db.add(ScreenshotJob(tenant_id=tenant_id, device_id=device.id,
                                 reason=CaptureReason.EVENT, requested_by="policy:" + policy.id))
        elif kind == "notify":
            db.add(Notification(tenant_id=tenant_id, kind="policy", title=policy.name,
                                body=act.get("message", policy.name), severity=act.get("severity", "info")))
        elif kind in ("block", "ticket", "log", "approval"):
            db.add(Notification(tenant_id=tenant_id, kind=kind, title=f"{policy.name} [{kind}]",
                                body=act.get("message", policy.name), severity="info"))
    db.flush()


def _primary_field(policy: Policy) -> str:
    conds = policy.rule.get("if", [])
    return conds[0].get("field", "") if conds else ""


def _primary_value(policy: Policy):
    conds = policy.rule.get("if", [])
    return conds[0].get("value") if conds else None


def process_health(db: Session, tenant_id: str, device: Device, sample: dict) -> None:
    state = _state(device.id)
    for p in policy_engine.matching_policies(db, tenant_id, "health", device):
        actions = policy_engine.evaluate(p, sample, state)
        if actions:
            _run_actions(db, tenant_id, device, p, actions, sample)


def process_activity(db: Session, tenant_id: str, device: Device, sample: dict) -> None:
    state = _state(device.id)
    for p in policy_engine.matching_policies(db, tenant_id, "activity", device):
        actions = policy_engine.evaluate(p, sample, state)
        if actions:
            _run_actions(db, tenant_id, device, p, actions, sample)


def process_file(db: Session, tenant_id: str, device: Device, sample: dict) -> None:
    state = _state(device.id)
    for p in policy_engine.matching_policies(db, tenant_id, "file", device):
        actions = policy_engine.evaluate(p, sample, state)
        if actions:
            _run_actions(db, tenant_id, device, p, actions, sample)


def process_software_change(db: Session, tenant_id: str, device: Device, sw: Software,
                            change: str) -> None:
    """change in {'installed','removed'}. Matches software policies + blocklist (PRD §13)."""
    sample = {"name": sw.name, "publisher": sw.publisher or "", "change": change,
              "list_status": sw.list_status}
    state = _state(device.id)
    matched = False
    for p in policy_engine.matching_policies(db, tenant_id, "software", device):
        actions = policy_engine.evaluate(p, sample, state)
        if actions:
            matched = True
            _run_actions(db, tenant_id, device, p, actions, sample)
    if not matched and change == "installed":
        sev = "high" if sw.list_status == "block" else "info"
        _raise_alert(db, tenant_id, device, None, "Software change",
                     sev, f"{change}: {sw.name} {sw.version or ''}".strip(),
                     current_value=sw.name)


def check_offline(db: Session, offline_after_seconds: int) -> int:
    """Mark stale devices offline and raise alerts (PRD §16 device offline)."""
    from ..models import DeviceStatus
    now = datetime.now(timezone.utc)
    count = 0
    devices = db.query(Device).filter(Device.status == DeviceStatus.ACTIVE).all()
    for d in devices:
        if not d.last_seen:
            continue
        last = d.last_seen.replace(tzinfo=timezone.utc) if d.last_seen.tzinfo is None else d.last_seen
        if (now - last).total_seconds() > offline_after_seconds:
            d.status = DeviceStatus.OFFLINE
            _raise_alert(db, d.tenant_id, d, None, "Device offline", "medium",
                         f"No heartbeat from {d.hostname} for over {offline_after_seconds}s")
            count += 1
    return count
