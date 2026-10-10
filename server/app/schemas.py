"""Pydantic request/response models (PRD §26 API contract)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    AlertSeverity,
    AlertStatus,
    AssetLifecycle,
    CaptureReason,
    DeviceStatus,
    LicenseEdition,
    LicenseStatus,
    LicenseType,
    Role,
)


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


# --------------------------------------------------------------- auth
class LoginIn(BaseModel):
    email: str
    password: str
    tenant_id: str | None = None
    mfa_code: str | None = None


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    role: str
    tenant_id: str | None
    full_name: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


# --------------------------------------------------------------- tenants / licenses
class TenantIn(BaseModel):
    company_name: str
    address: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    deployment_model: str = "on_premise"


class TenantOut(ORM):
    id: str
    company_name: str
    status: str
    deployment_model: str
    contact_email: str | None = None
    contact_phone: str | None = None
    evidence_retention_days: int
    event_retention_days: int
    client_server_version: str | None = None
    client_server_updated_at: datetime | None = None
    last_sync_at: datetime | None = None
    created_at: datetime


class LicenseIn(BaseModel):
    branch_name: str | None = None          # required when the company already has a license
    edition: LicenseEdition = LicenseEdition.STANDARD
    license_type: LicenseType = LicenseType.SUBSCRIPTION_MONTHLY
    term_days: int = 365
    max_devices: int = 25
    max_admins: int = 3
    max_storage_mb: int = 10240
    is_demo: bool = False
    features: dict[str, bool] = Field(default_factory=dict)


class LicenseOut(ORM):
    id: str
    tenant_id: str
    edition: LicenseEdition
    license_type: LicenseType
    status: LicenseStatus
    activated: bool
    start_date: datetime
    expiry_date: datetime
    max_devices: int
    max_admins: int
    max_storage_mb: int
    features: dict
    is_demo: bool
    branch_name: str | None = None
    activated_server_id: str | None = None


class LicenseUpdateIn(BaseModel):
    """Increase / change an existing license instead of issuing a second one."""
    max_devices: int | None = None
    max_admins: int | None = None
    extend_days: int = 0
    branch_name: str | None = None


class LicensePackageOut(BaseModel):
    """What the customer feeds into the Management Server installer."""
    license_id: str
    activation_token: str
    company_name: str
    server_hint: str
    branch_name: str | None = None
    download_url: str | None = None


class ActivateIn(BaseModel):
    license_id: str
    activation_token: str
    server_id: str


class ProvisionIn(BaseModel):
    """One-shot customer provisioning: company + owner login + license + email."""
    company_name: str
    address: str | None = None
    contact_email: str                      # registered email (receives the license key)
    contact_phone: str | None = None
    deployment_model: str = "on_premise"
    branch_name: str | None = None          # optional: name of the site this first license is for
    # owner account
    owner_email: str | None = None          # defaults to contact_email
    owner_name: str = "Account Owner"
    owner_password: str | None = None        # auto-generated if omitted
    # license
    edition: LicenseEdition = LicenseEdition.STANDARD
    license_type: LicenseType = LicenseType.SUBSCRIPTION_MONTHLY
    term_days: int = 365
    max_devices: int = 25
    max_admins: int = 3
    is_demo: bool = False
    send_email: bool = True


class ProvisionOut(BaseModel):
    tenant_id: str
    company_name: str
    owner_email: str
    owner_password: str | None              # returned once so the admin can hand it over
    license_id: str
    license_key: str                        # signed activation token
    expiry: datetime
    max_devices: int
    download_url: str                       # GET endpoint for the .lic file
    email_status: str                       # delivered | outbox | skipped


# --------------------------------------------------------------- users
class UserIn(BaseModel):
    email: str
    full_name: str = ""
    password: str
    role: Role = Role.IT_ADMIN


class UserOut(ORM):
    id: str
    email: str
    full_name: str
    role: Role
    is_active: bool
    mfa_enabled: bool
    last_login: datetime | None


# --------------------------------------------------------------- enrollment
class EnrollTokenIn(BaseModel):
    label: str = "default"
    ttl_hours: int | None = None
    max_uses: int = 0


class EnrollTokenOut(ORM):
    id: str
    token: str
    label: str
    expires_at: datetime
    max_uses: int
    uses: int
    revoked: bool


class AgentEnrollIn(BaseModel):
    license_id: str
    enroll_token: str
    machine_uid: str
    hostname: str
    os_name: str | None = None
    os_version: str | None = None
    ip_address: str | None = None
    mac_address: str | None = None
    agent_version: str | None = None
    hardware: dict[str, Any] = Field(default_factory=dict)


class AgentEnrollOut(BaseModel):
    device_id: str
    device_cert: str
    heartbeat_interval: int
    policy_version: int
    status: str


# --------------------------------------------------------------- devices
class DeviceOut(ORM):
    id: str
    hostname: str
    machine_uid: str
    os_name: str | None
    os_version: str | None
    ip_address: str | None
    mac_address: str | None
    agent_version: str | None
    employee_id: str | None
    department: str | None
    location: str | None
    status: DeviceStatus
    last_seen: datetime | None
    enrolled_at: datetime
    hardware: dict
    policy_version: int
    current_user: str | None = None
    logged_users: list | None = None


class DevicePatch(BaseModel):
    employee_id: str | None = None
    department: str | None = None
    location: str | None = None


# --------------------------------------------------------------- heartbeat / telemetry
class SoftwareItem(BaseModel):
    name: str
    publisher: str | None = None
    version: str | None = None
    install_date: str | None = None


class ActivityItem(BaseModel):
    event_type: str
    application: str | None = None
    domain: str | None = None
    title: str | None = None
    category: str | None = None
    duration_seconds: int = 0
    ts: datetime | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class HealthItem(BaseModel):
    cpu_percent: float | None = None
    ram_percent: float | None = None
    disk_percent: float | None = None
    net_up_kbps: float | None = None
    net_down_kbps: float | None = None
    battery_percent: float | None = None
    battery_health: str | None = None
    uptime_seconds: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    ts: datetime | None = None


class FileEventItem(BaseModel):
    action: str
    path: str | None = None
    destination: str | None = None
    extension: str | None = None
    size_bytes: int | None = None
    classification: str | None = None
    ts: datetime | None = None


class HeartbeatIn(BaseModel):
    agent_version: str | None = None
    ip_address: str | None = None
    current_user: str | None = None                 # Windows account (DOMAIN/user) of the reporting session
    users: list[str] = Field(default_factory=list)   # all accounts signed in on the PC
    hardware: dict | None = None                      # full inventory (start + every 6 h)
    health: HealthItem | None = None
    activity: list[ActivityItem] = Field(default_factory=list)
    file_events: list[FileEventItem] = Field(default_factory=list)
    software: list[SoftwareItem] | None = None     # full snapshot when present
    policy_version: int = 0


class HeartbeatOut(BaseModel):
    ok: bool = True
    heartbeat_interval: int
    policy_version: int
    policies: list[dict] | None = None            # pushed when policy_version changed
    collection: dict = Field(default_factory=dict)  # per-agent data profile (what to collect)
    tracking: dict | None = None                   # effective tracking profile (trackers/file types/sync)
    agent_update: dict | None = None               # {version, url, sha256} when a newer agent exists
    screenshot_jobs: list[dict] = Field(default_factory=list)
    remote_sessions: list[dict] = Field(default_factory=list)
    server_time: datetime


# --------------------------------------------------------------- assets / employees
class EmployeeIn(BaseModel):
    employee_code: str
    name: str
    email: str | None = None
    department: str | None = None
    designation: str | None = None
    location: str | None = None
    manager_id: str | None = None


class EmployeeOut(ORM):
    id: str
    employee_code: str
    name: str
    email: str | None
    department: str | None
    designation: str | None
    location: str | None
    manager_id: str | None
    status: str


class AssetIn(BaseModel):
    asset_tag: str
    category: str = "computer"
    name: str = ""
    serial_no: str | None = None
    model: str | None = None
    device_id: str | None = None
    employee_id: str | None = None
    department: str | None = None
    location: str | None = None
    purchase_date: datetime | None = None
    warranty_expiry: datetime | None = None
    lifecycle: AssetLifecycle = AssetLifecycle.IN_STOCK
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class AssetOut(ORM):
    id: str
    asset_tag: str
    category: str
    name: str
    serial_no: str | None
    model: str | None
    device_id: str | None
    employee_id: str | None
    department: str | None
    location: str | None
    purchase_date: datetime | None
    warranty_expiry: datetime | None
    lifecycle: AssetLifecycle
    custom_fields: dict


# --------------------------------------------------------------- policies / alerts
class PolicyIn(BaseModel):
    name: str
    description: str | None = None
    enabled: bool = True
    priority: int = 100
    scope_type: str = "tenant"
    scope_value: str | None = None
    rule: dict[str, Any] = Field(default_factory=dict)
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    exceptions: list[Any] = Field(default_factory=list)


class PolicyOut(ORM):
    id: str
    name: str
    description: str | None
    enabled: bool
    priority: int
    scope_type: str
    scope_value: str | None
    rule: dict
    effective_from: datetime | None
    effective_to: datetime | None
    exceptions: list


class AlertOut(ORM):
    id: str
    device_id: str | None
    policy_id: str | None
    rule_name: str
    severity: AlertSeverity
    message: str
    current_value: str | None
    threshold: str | None
    evidence_id: str | None
    status: AlertStatus
    acknowledged_by: str | None
    resolution_note: str | None
    created_at: datetime


class AlertUpdate(BaseModel):
    status: AlertStatus
    resolution_note: str | None = None


# --------------------------------------------------------------- screenshots / remote
class ScreenshotRequestIn(BaseModel):
    device_id: str
    reason: CaptureReason = CaptureReason.MANUAL
    scheduled_for: datetime | None = None


class RemoteRequestIn(BaseModel):
    device_id: str
    consent_mode: str = "silent"          # silent | notify | consent (per company policy)
    duration_minutes: int = 30
    clipboard_allowed: bool = False
    file_transfer_allowed: bool = False
