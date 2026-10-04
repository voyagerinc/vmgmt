"""SQLAlchemy models — full data model (PRD §27).

Tenant isolation is enforced everywhere via `tenant_id` (PRD §9, §24). Platform-level
entities (Tenant, License, platform super-admins) sit above tenants.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- enums
class Role(str, enum.Enum):
    PLATFORM_SUPER_ADMIN = "platform_super_admin"   # PRD §4
    CUSTOMER_OWNER = "customer_owner"
    IT_ADMIN = "it_admin"
    SECURITY_ADMIN = "security_admin"
    MANAGER = "manager"
    HELPDESK = "helpdesk"
    AUDITOR = "auditor"


class LicenseEdition(str, enum.Enum):
    DEMO = "demo"
    STANDARD = "standard"
    CUSTOM = "custom"
    ENTERPRISE = "enterprise"


class LicenseStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    REVOKED = "revoked"


class LicenseType(str, enum.Enum):
    SUBSCRIPTION_MONTHLY = "subscription_monthly"   # per-computer monthly (PRD §4)
    LIFETIME = "lifetime"                           # one-time, includes 1 year support


class DeviceStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    OFFLINE = "offline"
    REVOKED = "revoked"


class AssetLifecycle(str, enum.Enum):
    PLANNED = "planned"
    IN_STOCK = "in_stock"
    ASSIGNED = "assigned"
    IN_REPAIR = "in_repair"
    RETIRED = "retired"
    DISPOSED = "disposed"


class AlertSeverity(str, enum.Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(str, enum.Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class ScreenshotStatus(str, enum.Enum):
    REQUESTED = "requested"
    DISPATCHED = "dispatched"
    CAPTURED = "captured"
    FAILED = "failed"
    EXPIRED = "expired"


class CaptureReason(str, enum.Enum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    INTERVAL = "interval"
    EVENT = "event"
    ON_ALERT = "on_alert"


class RemoteSessionStatus(str, enum.Enum):
    REQUESTED = "requested"
    ACTIVE = "active"
    ENDED = "ended"
    DENIED = "denied"
    EXPIRED = "expired"


# --------------------------------------------------------------------------- platform
class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    contact_email: Mapped[str | None] = mapped_column(String(200))
    contact_phone: Mapped[str | None] = mapped_column(String(50))
    branding: Mapped[dict] = mapped_column(JSON, default=dict)
    deployment_model: Mapped[str] = mapped_column(String(32), default="on_premise")
    status: Mapped[str] = mapped_column(String(20), default="active")
    evidence_retention_days: Mapped[int] = mapped_column(Integer, default=30)
    event_retention_days: Mapped[int] = mapped_column(Integer, default=90)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    licenses: Mapped[list["License"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class License(Base):
    __tablename__ = "licenses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    edition: Mapped[LicenseEdition] = mapped_column(Enum(LicenseEdition), default=LicenseEdition.STANDARD)
    license_type: Mapped[LicenseType] = mapped_column(
        Enum(LicenseType, values_callable=lambda e: [m.value for m in e]),
        default=LicenseType.SUBSCRIPTION_MONTHLY)
    status: Mapped[LicenseStatus] = mapped_column(Enum(LicenseStatus), default=LicenseStatus.ACTIVE)
    # cryptographic activation identity
    activation_secret: Mapped[str] = mapped_column(String(128), default=lambda: uuid.uuid4().hex + uuid.uuid4().hex)
    signature: Mapped[str | None] = mapped_column(Text)          # signed license payload
    activated: Mapped[bool] = mapped_column(Boolean, default=False)
    activated_server_id: Mapped[str | None] = mapped_column(String(64))
    start_date: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expiry_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    max_devices: Mapped[int] = mapped_column(Integer, default=25)
    max_admins: Mapped[int] = mapped_column(Integer, default=3)
    max_storage_mb: Mapped[int] = mapped_column(Integer, default=10240)
    features: Mapped[dict] = mapped_column(JSON, default=dict)     # module entitlements
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    grace_days: Mapped[int] = mapped_column(Integer, default=7)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    tenant: Mapped[Tenant] = relationship(back_populates="licenses")


class AdminUser(Base):
    __tablename__ = "admin_users"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_user_email_per_tenant"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.IT_ADMIN)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_secret: Mapped[str | None] = mapped_column(String(64))
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    last_login: Mapped[datetime | None] = mapped_column(DateTime)
    # Credential version — bumped on every password reset so client servers can pull a
    # cloud-initiated reset for their company owner (PRD §8). Only the hash is synced.
    cred_seq: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EnrollmentToken(Base):
    __tablename__ = "enrollment_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, default=lambda: uuid.uuid4().hex)
    label: Mapped[str] = mapped_column(String(120), default="default")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, default=0)       # 0 = unlimited within license seat cap
    uses: Mapped[int] = mapped_column(Integer, default=0)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# --------------------------------------------------------------------------- directory
class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (UniqueConstraint("tenant_id", "employee_code", name="uq_employee_code"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    employee_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(200))
    department: Mapped[str | None] = mapped_column(String(120))
    designation: Mapped[str | None] = mapped_column(String(120))
    location: Mapped[str | None] = mapped_column(String(120))
    manager_id: Mapped[str | None] = mapped_column(ForeignKey("employees.id"))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "machine_uid", name="uq_device_machine_uid"),
        Index("ix_device_tenant_status", "tenant_id", "status"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    machine_uid: Mapped[str] = mapped_column(String(128), nullable=False)   # stable per-device id from agent
    hostname: Mapped[str] = mapped_column(String(200), default="")
    device_cert: Mapped[str] = mapped_column(String(128), default=lambda: uuid.uuid4().hex)  # per-device token
    os_name: Mapped[str | None] = mapped_column(String(120))
    os_version: Mapped[str | None] = mapped_column(String(120))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    mac_address: Mapped[str | None] = mapped_column(String(64))
    agent_version: Mapped[str | None] = mapped_column(String(40))
    employee_id: Mapped[str | None] = mapped_column(ForeignKey("employees.id"))
    location: Mapped[str | None] = mapped_column(String(120))
    department: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[DeviceStatus] = mapped_column(Enum(DeviceStatus), default=DeviceStatus.PENDING)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    hardware: Mapped[dict] = mapped_column(JSON, default=dict)      # cpu/ram/disk snapshot
    policy_version: Mapped[int] = mapped_column(Integer, default=0)
    # Per-agent data profile (PRD §2.5): the admin chooses what to collect from THIS device.
    # Collection is opt-in and privacy-minimizing; only enabled categories are gathered/shown.
    # keystrokes/email/website/usb are capability flags honored by the agent where the platform
    # collector/enforcer exists; `screenshot_interval` is seconds (0 = on-request only).
    collection: Mapped[dict] = mapped_column(JSON, default=lambda: {
        "health": True, "software": True, "activity": False,
        "file_events": False, "screenshots": True,
        "email": False, "website": False, "keystrokes": False, "usb": False,
        "active_time": True, "screenshot_interval": 0,
    })


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    asset_tag: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(60), default="computer")   # computer/laptop/printer/...
    name: Mapped[str] = mapped_column(String(200), default="")
    serial_no: Mapped[str | None] = mapped_column(String(120))
    model: Mapped[str | None] = mapped_column(String(120))
    device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.id"))
    employee_id: Mapped[str | None] = mapped_column(ForeignKey("employees.id"))
    department: Mapped[str | None] = mapped_column(String(120))
    location: Mapped[str | None] = mapped_column(String(120))
    purchase_date: Mapped[datetime | None] = mapped_column(DateTime)
    warranty_expiry: Mapped[datetime | None] = mapped_column(DateTime)
    lifecycle: Mapped[AssetLifecycle] = mapped_column(Enum(AssetLifecycle), default=AssetLifecycle.IN_STOCK)
    custom_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AssetHistory(Base):
    __tablename__ = "asset_history"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(60))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    actor: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# --------------------------------------------------------------------------- telemetry
class Software(Base):
    __tablename__ = "software"
    __table_args__ = (Index("ix_software_device", "tenant_id", "device_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    publisher: Mapped[str | None] = mapped_column(String(200))
    version: Mapped[str | None] = mapped_column(String(80))
    install_date: Mapped[str | None] = mapped_column(String(40))
    list_status: Mapped[str] = mapped_column(String(20), default="unknown")   # allow/block/unknown
    present: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ActivityEvent(Base):
    __tablename__ = "activity_events"
    __table_args__ = (Index("ix_activity_tenant_time", "tenant_id", "ts"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(40))        # app_start/app_stop/web_visit/active_window
    application: Mapped[str | None] = mapped_column(String(300))
    domain: Mapped[str | None] = mapped_column(String(300))
    category: Mapped[str | None] = mapped_column(String(80))
    title: Mapped[str | None] = mapped_column(String(400))
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class HealthMetric(Base):
    __tablename__ = "health_metrics"
    __table_args__ = (Index("ix_health_device_time", "device_id", "ts"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    ram_percent: Mapped[float | None] = mapped_column(Float)
    disk_percent: Mapped[float | None] = mapped_column(Float)
    net_up_kbps: Mapped[float | None] = mapped_column(Float)
    net_down_kbps: Mapped[float | None] = mapped_column(Float)
    battery_percent: Mapped[float | None] = mapped_column(Float)
    battery_health: Mapped[str | None] = mapped_column(String(40))
    uptime_seconds: Mapped[int | None] = mapped_column(Integer)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class FileEvent(Base):
    """DLP / file monitoring (PRD §18) — metadata-first, policy-driven."""
    __tablename__ = "file_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(30))            # create/copy/move/rename/delete
    path: Mapped[str | None] = mapped_column(Text)
    destination: Mapped[str | None] = mapped_column(Text)
    extension: Mapped[str | None] = mapped_column(String(20))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    classification: Mapped[str | None] = mapped_column(String(60))
    rule_action: Mapped[str | None] = mapped_column(String(30))  # alert/log/quarantine/block
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


# --------------------------------------------------------------------------- policy & alerts
class Policy(Base):
    __tablename__ = "policies"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    # scope: tenant -> site -> department -> group -> device
    scope_type: Mapped[str] = mapped_column(String(20), default="tenant")
    scope_value: Mapped[str | None] = mapped_column(String(200))
    # rule body (PRD §20): {"when": {...}, "if": [...], "then": [...]}
    rule: Mapped[dict] = mapped_column(JSON, default=dict)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime)
    exceptions: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alert_tenant_status", "tenant_id", "status"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(index=True)
    device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    policy_id: Mapped[str | None] = mapped_column(ForeignKey("policies.id", ondelete="SET NULL"))
    rule_name: Mapped[str] = mapped_column(String(200))
    severity: Mapped[AlertSeverity] = mapped_column(Enum(AlertSeverity), default=AlertSeverity.MEDIUM)
    message: Mapped[str] = mapped_column(Text, default="")
    current_value: Mapped[str | None] = mapped_column(String(120))
    threshold: Mapped[str | None] = mapped_column(String(120))
    evidence_id: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[AlertStatus] = mapped_column(Enum(AlertStatus), default=AlertStatus.OPEN)
    acknowledged_by: Mapped[str | None] = mapped_column(String(120))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------- evidence & support
class ScreenshotJob(Base):
    __tablename__ = "screenshot_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    reason: Mapped[CaptureReason] = mapped_column(Enum(CaptureReason), default=CaptureReason.MANUAL)
    status: Mapped[ScreenshotStatus] = mapped_column(Enum(ScreenshotStatus), default=ScreenshotStatus.REQUESTED)
    requested_by: Mapped[str | None] = mapped_column(String(120))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime)
    signed_job: Mapped[str | None] = mapped_column(Text)
    evidence_id: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("screenshot_jobs.id"))
    reason: Mapped[CaptureReason] = mapped_column(Enum(CaptureReason), default=CaptureReason.MANUAL)
    storage_path: Mapped[str] = mapped_column(Text)          # encrypted blob on disk
    content_type: Mapped[str] = mapped_column(String(40), default="image/png")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64))
    watermark: Mapped[str | None] = mapped_column(String(300))
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime)


class RemoteSession(Base):
    __tablename__ = "remote_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    admin_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[RemoteSessionStatus] = mapped_column(Enum(RemoteSessionStatus), default=RemoteSessionStatus.REQUESTED)
    consent_mode: Mapped[str] = mapped_column(String(20), default="notify")   # notify/consent/silent-by-policy
    consent_granted: Mapped[bool] = mapped_column(Boolean, default=False)
    clipboard_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    file_transfer_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    session_key: Mapped[str] = mapped_column(String(64), default=lambda: uuid.uuid4().hex)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# --------------------------------------------------------------------------- audit & notifications
class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_tenant_time", "tenant_id", "ts"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str | None] = mapped_column(index=True)
    actor_id: Mapped[str | None] = mapped_column(String(32))
    actor_email: Mapped[str | None] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str | None] = mapped_column(String(60))
    target_id: Mapped[str | None] = mapped_column(String(64))
    old_value: Mapped[dict | None] = mapped_column(JSON)
    new_value: Mapped[dict | None] = mapped_column(JSON)
    result: Mapped[str] = mapped_column(String(20), default="success")
    source_ip: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(32), default=_uuid)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(20), default="info")
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class UpdatePackage(Base):
    __tablename__ = "update_packages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    component: Mapped[str] = mapped_column(String(20))        # agent/server
    version: Mapped[str] = mapped_column(String(40))
    signature: Mapped[str | None] = mapped_column(Text)
    rollout_state: Mapped[str] = mapped_column(String(20), default="draft")  # draft/staged/released/rolledback
    url: Mapped[str | None] = mapped_column(Text)
    min_compatible: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PasswordResetToken(Base):
    """Short-lived token backing the self-service 'forgot password' flow (PRD §24)."""
    __tablename__ = "password_reset_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=lambda: uuid.uuid4().hex)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Setting(Base):
    """Runtime key/value settings (e.g. SMTP). tenant_id NULL = platform-global (PRD §23)."""
    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_setting_scope_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str | None] = mapped_column(index=True)   # None = global/platform
    key: Mapped[str] = mapped_column(String(60), index=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
