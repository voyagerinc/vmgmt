"""Tracking profiles + event ingestion: logins, network/Wi-Fi, USB file copies, email files.

A profile is a named set of trackers, tracked file types and the event sync interval. Each
computer uses its assigned profile, else the company's default profile (created on demand).
Events are metadata only (names, sizes, times, drive/recipient/Wi-Fi names) — never file
contents or email bodies.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Alert, AlertSeverity, Device, TrackingEvent, TrackingProfile

DEFAULT_FILE_TYPES = [".xlsx", ".xls", ".csv", ".docx", ".doc", ".pdf", ".pptx", ".zip", ".rar",
                      ".7z", ".sql", ".bak", ".dwg", ".pst"]

DEFAULT_SETTINGS = {
    "logins": True,          # logon/logoff/lock/unlock/startup/shutdown/sleep/wake
    "network": True,         # adapter up/down, IP change, internet lost/restored
    "wifi": True,            # Wi-Fi connect/disconnect/network (SSID) change + signal
    "usb_files": False,      # tracked files copied to / from USB drives (+ insert/remove)
    "email_files": False,    # tracked files sent from Outlook / attached in webmail (best effort)
    "file_types": DEFAULT_FILE_TYPES,
    "sync_interval": 300,    # seconds between event uploads
    "alert_on_transfer": True,   # raise an alert for tracked files leaving via USB/email
}

CATEGORIES = ("login", "network", "usb", "email")
SYNC_CHOICES = (60, 120, 300, 600, 900, 1800, 3600)
_TRANSFER_EVENTS = {"copied_to_usb": "USB", "email_sent": "email", "email_attached": "webmail"}


def normalize_settings(raw: dict | None, base: dict | None = None) -> dict:
    """Merge + validate profile settings (unknown keys dropped)."""
    out = dict(base or DEFAULT_SETTINGS)
    raw = raw or {}
    for k in ("logins", "network", "wifi", "usb_files", "email_files", "alert_on_transfer"):
        if k in raw:
            out[k] = bool(raw[k])
    if "file_types" in raw:
        items = raw["file_types"]
        if isinstance(items, str):
            items = items.replace(";", ",").replace(" ", ",").split(",")
        exts = []
        for x in items or []:
            x = str(x).strip().lower()
            if not x or x in ("*", "."):
                continue
            x = x if x.startswith(".") else "." + x
            if len(x) <= 15 and x not in exts:
                exts.append(x)
        out["file_types"] = exts[:100]
    if "sync_interval" in raw:
        try:
            v = int(raw["sync_interval"])
        except (TypeError, ValueError):
            v = DEFAULT_SETTINGS["sync_interval"]
        out["sync_interval"] = min(max(v, 60), 3600)
    return out


def default_profile(db: Session, tenant_id: str) -> TrackingProfile:
    p = (db.query(TrackingProfile)
         .filter(TrackingProfile.tenant_id == tenant_id, TrackingProfile.is_default == True)  # noqa: E712
         .first())
    if p:
        return p
    p = TrackingProfile(tenant_id=tenant_id, name="Standard", is_default=True,
                        description="Company default: logins, network and Wi-Fi every 5 minutes.",
                        settings=normalize_settings({}))
    db.add(p)
    db.flush()
    return p


def effective_profile(db: Session, device: Device) -> TrackingProfile:
    if device.tracking_profile_id:
        p = db.get(TrackingProfile, device.tracking_profile_id)
        if p and p.tenant_id == device.tenant_id:
            return p
    return default_profile(db, device.tenant_id)


def agent_payload(db: Session, device: Device) -> dict:
    """What the agent receives in each heartbeat: the effective tracker settings."""
    p = effective_profile(db, device)
    s = normalize_settings(p.settings)
    return {"profile_id": p.id, "profile_name": p.name,
            "version": int(p.updated_at.timestamp()) if p.updated_at else 0, **s}


def _ts(v) -> datetime:
    if isinstance(v, datetime):
        dt = v
    else:
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except Exception:
            return datetime.now(timezone.utc).replace(tzinfo=None)
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _clip(v, n):
    return None if v is None else str(v)[:n]


def record_events(db: Session, device: Device, events: list[dict]) -> int:
    """Store a batch of agent events; raise alerts for tracked files leaving the PC."""
    prof = normalize_settings(effective_profile(db, device).settings)
    n = 0
    for e in events[:2000]:
        cat = str(e.get("category") or "").lower()
        etype = _clip(e.get("event_type"), 40)
        if cat not in CATEGORIES or not etype:
            continue
        size = e.get("file_size")
        try:
            size = int(size) if size is not None else None
        except (TypeError, ValueError):
            size = None
        meta = e.get("meta") if isinstance(e.get("meta"), dict) else {}
        ev = TrackingEvent(
            tenant_id=device.tenant_id, device_id=device.id, category=cat, event_type=etype,
            user=_clip(e.get("user"), 120), detail=_clip(e.get("detail"), 2000),
            file_name=_clip(e.get("file_name"), 400), file_ext=_clip((e.get("file_ext") or "").lower() or None, 20),
            file_size=size, target=_clip(e.get("target"), 400), meta=meta, ts=_ts(e.get("ts")),
        )
        db.add(ev)
        n += 1
        if prof.get("alert_on_transfer") and etype in _TRANSFER_EVENTS:
            via = _TRANSFER_EVENTS[etype]
            db.add(Alert(
                tenant_id=device.tenant_id, device_id=device.id,
                rule_name=f"Tracked file sent via {via}",
                severity=AlertSeverity.HIGH if etype == "copied_to_usb" else AlertSeverity.MEDIUM,
                message=(f"{device.hostname}: {ev.user or 'user'} — {ev.file_name or 'file'}"
                         f" → {ev.target or via}" + (" (attached in webmail; send not confirmed)"
                                                     if etype == "email_attached" else "")),
                current_value=_clip(ev.file_name, 120), threshold=_clip(ev.file_ext, 120),
            ))
    return n
