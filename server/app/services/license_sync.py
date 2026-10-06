"""Client-server sync: pull owner-credential resets + entitlement changes from the cloud
license server (PRD §8). Only hashes are transferred, never plaintext passwords."""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..config import settings
from ..models import AdminUser, License, LicenseStatus, Role
from . import license_service as lic_svc


def is_client_server() -> bool:
    return bool(settings.license_server)


def run_client_sync(db: Session) -> dict:
    """Returns {"synced": bool, "changed": [...], "detail": str}."""
    if not is_client_server():
        return {"synced": False, "changed": [], "detail": "Not a client server (no license server set)"}
    server = settings.license_server.rstrip("/")

    # pick the local activated license to sync
    lic = (db.query(License).filter(License.activated == True)  # noqa: E712
           .order_by(License.created_at.desc()).first())
    if not lic or not lic.signature:
        return {"synced": False, "changed": [], "detail": "No activated license to sync"}

    from .. import __version__, get_build
    from ..config import BASE_DIR
    bid = get_build()
    v_display = f"v{__version__}" + (f" ({bid})" if bid else "")

    mtime = None
    try:
        main_py = BASE_DIR / "app" / "main.py"
        if main_py.exists():
            mtime = datetime.fromtimestamp(main_py.stat().st_mtime, tz=timezone.utc).isoformat()
    except Exception:
        pass

    payload = json.dumps({
        "license_id": lic.id,
        "license_key": lic.signature,
        "client_version": v_display,
        "client_updated_at": mtime,
    }).encode()
    req = urllib.request.Request(f"{server}/api/license/sync", data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            info = json.loads(resp.read().decode())
    except Exception as e:
        return {"synced": False, "changed": [], "detail": f"Cloud unreachable: {e}"}

    changed = []
    # entitlements
    try:
        exp = datetime.fromisoformat(info["expiry"])
        if lic.expiry_date.replace(tzinfo=None) != exp.replace(tzinfo=None):
            lic.expiry_date = exp
            changed.append("license_expiry")
        if info.get("max_devices") and lic.max_devices != info["max_devices"]:
            lic.max_devices = info["max_devices"]
            changed.append("max_devices")
        if info.get("features"):
            lic.features = info["features"]
        new_status = info.get("status")
        if new_status == "revoked":
            lic.status = LicenseStatus.REVOKED
            lic.activated = False
            changed.append("license_revoked")
        elif new_status == "active" and lic.status != LicenseStatus.ACTIVE:
            lic.status = LicenseStatus.ACTIVE
    except Exception:
        pass

    # owner credential reset (only the hash moves)
    owner_email = (info.get("owner_email") or "").lower()
    remote_seq = int(info.get("cred_seq") or 0)
    remote_hash = info.get("owner_password_hash")
    if owner_email and remote_hash:
        owner = db.query(AdminUser).filter(AdminUser.tenant_id == lic.tenant_id,
                                           AdminUser.email == owner_email).first()
        if owner and remote_seq > (owner.cred_seq or 0) and owner.password_hash != remote_hash:
            owner.password_hash = remote_hash
            owner.cred_seq = remote_seq
            owner.failed_logins = 0
            owner.locked_until = None
            changed.append("owner_password")

    db.commit()
    return {"synced": True, "changed": changed,
            "detail": ("Applied: " + ", ".join(changed)) if changed else "Already up to date"}
