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


def build_activation_token(lic: License, company_name: str) -> str:
    payload = {
        "license_id": lic.id,
        "tenant_id": lic.tenant_id,
        "company": company_name,
        "edition": lic.edition.value,
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
