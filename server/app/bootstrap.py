"""First-run provisioning (PRD §7.1): super admin, demo tenant, license, sample policies."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .config import BASE_DIR, settings
from .models import (
    AdminUser,
    EnrollmentToken,
    License,
    LicenseEdition,
    Policy,
    Role,
    SchemaVersion,
    Tenant,
)
from .security import hash_password
from .services import license_service as lic_svc


def ensure_bootstrap(db: Session) -> None:
    if db.query(SchemaVersion).first() is None:
        db.add(SchemaVersion(version=1))

    if db.query(AdminUser).filter(AdminUser.role == Role.PLATFORM_SUPER_ADMIN).first():
        db.commit()
        return  # already bootstrapped

    super_pw = settings.default_admin_password or secrets.token_urlsafe(12)
    owner_pw = secrets.token_urlsafe(12)

    superadmin = AdminUser(
        tenant_id=None, email="superadmin@platform.local", full_name="Platform Super Admin",
        password_hash=hash_password(super_pw), role=Role.PLATFORM_SUPER_ADMIN,
    )
    db.add(superadmin)

    tenant = Tenant(company_name="Demo Company", contact_email="owner@demo.local",
                    deployment_model="on_premise")
    db.add(tenant)
    db.flush()

    lic = License(
        tenant_id=tenant.id, edition=LicenseEdition.ENTERPRISE,
        start_date=datetime.now(timezone.utc),
        expiry_date=datetime.now(timezone.utc) + timedelta(days=365),
        max_devices=500, max_admins=25, max_storage_mb=102400,
        features=lic_svc.DEFAULT_FEATURES, activated=True, activated_server_id="dev-server",
    )
    db.add(lic)
    db.flush()
    lic.signature = lic_svc.build_activation_token(lic, tenant.company_name)

    owner = AdminUser(tenant_id=tenant.id, email="owner@demo.local", full_name="Demo Owner",
                      password_hash=hash_password(owner_pw), role=Role.CUSTOMER_OWNER)
    db.add(owner)

    token = EnrollmentToken(tenant_id=tenant.id, label="default",
                            expires_at=datetime.now(timezone.utc) + timedelta(days=365))
    db.add(token)

    # sample policies (PRD §20.2)
    db.add_all([
        Policy(tenant_id=tenant.id, name="High CPU", priority=10, scope_type="tenant",
               rule={"when": {"type": "health"},
                     "if": [{"field": "cpu_percent", "op": ">", "value": 90, "for_seconds": 600}],
                     "then": [{"action": "alert", "severity": "high",
                               "message": "CPU above 90% for 10 minutes"}]}),
        Policy(tenant_id=tenant.id, name="Low Disk", priority=20, scope_type="tenant",
               rule={"when": {"type": "health"},
                     "if": [{"field": "disk_percent", "op": ">", "value": 90}],
                     "then": [{"action": "alert", "severity": "medium",
                               "message": "Disk usage above 90%"}, {"action": "ticket"}]}),
        Policy(tenant_id=tenant.id, name="Unauthorized software", priority=30, scope_type="tenant",
               rule={"when": {"type": "software"},
                     "if": [{"field": "list_status", "op": "==", "value": "block"}],
                     "then": [{"action": "alert", "severity": "high"}, {"action": "screenshot"}]}),
    ])

    db.commit()

    cred_file = BASE_DIR / "FIRST_RUN.txt"
    lines = [
        "=" * 64,
        "FIRST-RUN CREDENTIALS — change these immediately after login.",
        "=" * 64,
        "",
        "Platform License Portal (Super Admin):",
        f"  email    : superadmin@platform.local",
        f"  password : {super_pw}",
        "",
        "Demo tenant Customer Owner:",
        f"  email    : owner@demo.local",
        f"  password : {owner_pw}",
        "",
        f"Demo tenant id       : {tenant.id}",
        f"Demo license id      : {lic.id}",
        f"Demo enrollment token: {token.token}",
        "",
        "Enroll an agent with:",
        f"  python agent.py --server <URL> --license {lic.id} --token {token.token}",
        "=" * 64,
    ]
    cred_file.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
