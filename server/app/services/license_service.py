"""License lifecycle & enforcement (PRD §8, §33). Signed, server-validated, device-bound."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import AdminUser, Device, DeviceStatus, License, LicenseStatus
from ..security import sign_license, verify_license

DEFAULT_FEATURES = {
    "assets": True,
    "software_inventory": True,
    "web_app_monitoring": True,
    "health_monitoring": True,
    "alerts": True,
    "screenshots": True,
    "file_dlp": True,
    "remote_support": True,
    "reports": True,
}

# Every feature the platform can include in a license type, with a readable label for the UI.
FEATURE_LABELS = {
    "assets": "Asset inventory",
    "software_inventory": "Software inventory",
    "web_app_monitoring": "Web / application activity",
    "health_monitoring": "Resource health (CPU/RAM/disk)",
    "alerts": "Alerts & policies",
    "screenshots": "Screenshots & live screen",
    "file_dlp": "USB / file (DLP) tracking",
    "email_tracking": "Email tracking (Outlook)",
    "login_network_tracking": "Login / network / Wi-Fi tracking",
    "remote_support": "Remote support",
    "reports": "Reports & exports",
}

# Feature sets for the seeded system license types.
_STD = {k: True for k in ("assets", "software_inventory", "web_app_monitoring", "health_monitoring",
                          "alerts", "reports", "login_network_tracking")}
_PRO = {**_STD, "screenshots": True, "file_dlp": True, "email_tracking": True}
_ENT = {k: True for k in FEATURE_LABELS}

SYSTEM_PROFILES = [
    {"name": "Demo", "edition": "demo", "features": _PRO, "default_max_devices": 5,
     "default_max_admins": 2, "default_term_days": 15,
     "description": "15-day trial with most features."},
    {"name": "Standard", "edition": "standard", "features": _STD, "default_max_devices": 25,
     "default_max_admins": 3, "default_term_days": 365,
     "description": "Inventory, activity, health, alerts, reports, login/network tracking."},
    {"name": "Professional", "edition": "custom", "features": _PRO, "default_max_devices": 100,
     "default_max_admins": 5, "default_term_days": 365,
     "description": "Standard plus screenshots/live screen, USB/file tracking and email tracking."},
    {"name": "Enterprise", "edition": "enterprise", "features": _ENT, "default_max_devices": 500,
     "default_max_admins": 25, "default_term_days": 365,
     "description": "Every feature, including remote support."},
]


def full_features(features: dict | None) -> dict:
    """Normalize a feature set to all known keys (missing = off)."""
    base = {k: False for k in FEATURE_LABELS}
    base.update({k: bool(v) for k, v in (features or {}).items() if k in FEATURE_LABELS})
    return base


def ensure_system_profiles(db) -> None:
    """Seed the built-in license types once (license server)."""
    from ..models import LicenseProfile
    existing = {p.name.lower() for p in db.query(LicenseProfile).all()}
    for sp in SYSTEM_PROFILES:
        if sp["name"].lower() not in existing:
            db.add(LicenseProfile(name=sp["name"], edition=sp["edition"],
                                  features=full_features(sp["features"]),
                                  default_max_devices=sp["default_max_devices"],
                                  default_max_admins=sp["default_max_admins"],
                                  default_term_days=sp["default_term_days"],
                                  description=sp["description"], is_system=True))
    db.flush()


def build_activation_token(lic: License, company_name: str) -> str:
    payload = {
        "license_id": lic.id,
        "tenant_id": lic.tenant_id,
        "company": company_name,
        "edition": lic.edition.value,
        "profile_name": lic.profile_name,
        "expiry": lic.expiry_date.replace(tzinfo=timezone.utc).isoformat(),
        "max_devices": lic.max_devices,
        "secret": lic.activation_secret,
    }
    return sign_license(payload)


def verify_activation(token: str, license_id: str, activation_secret: str) -> bool:
    data = verify_license(token)
    if not data:
        return False
    return data.get("license_id") == license_id and data.get("secret") == activation_secret


def effective_status(lic: License) -> LicenseStatus:
    """Compute live status accounting for expiry + grace (PRD §8.2)."""
    if lic.status in (LicenseStatus.REVOKED, LicenseStatus.SUSPENDED):
        return lic.status
    now = datetime.now(timezone.utc)
    exp = lic.expiry_date.replace(tzinfo=timezone.utc) if lic.expiry_date.tzinfo is None else lic.expiry_date
    if now > exp:
        return LicenseStatus.EXPIRED
    return LicenseStatus.ACTIVE


def activation_label(lic: License) -> str:
    """UI-facing state: a valid license that has not been attached to a server is 'inactive'."""
    st = effective_status(lic)
    if st == LicenseStatus.ACTIVE and not lic.activated:
        return "pending_activation"
    return st.value


def is_usable(lic: License | None) -> bool:
    """Usable only when status is ACTIVE *and* the license has been activated on a server."""
    return bool(lic and lic.activated and effective_status(lic) == LicenseStatus.ACTIVE)


def active_license(db: Session, tenant_id: str) -> License | None:
    lic = (
        db.query(License)
        .filter(License.tenant_id == tenant_id)
        .order_by(License.expiry_date.desc())
        .first()
    )
    return lic


class LicenseError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def enforce_enrollment(db: Session, tenant_id: str) -> License:
    """Raise LicenseError if enrollment is not permitted (expiry + seat limits, PRD §33)."""
    lic = active_license(db, tenant_id)
    if not lic:
        raise LicenseError("NO_LICENSE", "No license found for tenant")
    status = effective_status(lic)
    if status != LicenseStatus.ACTIVE:
        raise LicenseError("LICENSE_" + status.value.upper(), f"License is {status.value}")
    if not lic.activated:
        raise LicenseError("LICENSE_INACTIVE",
                           "License is inactive — attach the license key on the server to activate it")
    active_devices = (
        db.query(func.count(Device.id))
        .filter(Device.tenant_id == tenant_id, Device.status != DeviceStatus.REVOKED)
        .scalar()
    )
    if active_devices >= lic.max_devices:
        raise LicenseError("DEVICE_LIMIT", f"Device limit reached ({lic.max_devices})")
    return lic


def enforce_admin_limit(db: Session, tenant_id: str) -> None:
    lic = active_license(db, tenant_id)
    if not lic:
        return
    count = db.query(func.count(AdminUser.id)).filter(AdminUser.tenant_id == tenant_id).scalar()
    if count >= lic.max_admins:
        raise LicenseError("ADMIN_LIMIT", f"Admin limit reached ({lic.max_admins})")
