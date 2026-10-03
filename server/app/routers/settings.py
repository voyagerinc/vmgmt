"""Email (SMTP) setup and test — platform-global (sysadmin) or per-tenant (company admin). PRD §23."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import audit
from ..database import get_db
from ..deps import client_ip, get_current_user, require_roles
from ..models import AdminUser, Role
from ..services import email_service
from ..services import settings_service as ss

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _scope_for(user: AdminUser, scope: str | None) -> str | None:
    """sysadmin -> global (None) by default; company owner -> own tenant only."""
    if user.role == Role.PLATFORM_SUPER_ADMIN:
        return None if (scope in (None, "", "global")) else scope
    return user.tenant_id


@router.get("/email")
def get_email(scope: str | None = None, db: Session = Depends(get_db),
              user: AdminUser = Depends(require_roles(Role.CUSTOMER_OWNER))):
    tid = _scope_for(user, scope)
    return {"scope": "global" if tid is None else tid, **ss.public_smtp(db, tid)}


@router.put("/email")
def put_email(body: dict, request: Request, scope: str | None = None, db: Session = Depends(get_db),
              user: AdminUser = Depends(require_roles(Role.CUSTOMER_OWNER))):
    tid = _scope_for(user, scope)
    allowed = {"host", "port", "user", "password", "use_tls", "from_addr", "from_name"}
    value = {k: v for k, v in body.items() if k in allowed and v is not None}
    if "port" in value:
        try:
            value["port"] = int(value["port"])
        except (TypeError, ValueError):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "port must be a number")
    # blank password => keep existing (do not overwrite with empty)
    if value.get("password", None) == "":
        value.pop("password")
    ss.set_setting(db, ss.SMTP_KEY, tid, value)
    audit.record(db, action="email_settings_update", tenant_id=tid, actor_id=user.id,
                 actor_email=user.email, target_type="setting", target_id="smtp",
                 new_value={k: ("***" if k == "password" else v) for k, v in value.items()},
                 source_ip=client_ip(request))
    db.commit()
    # Verify the saved credentials so the UI can show a clear success/error immediately.
    verify = email_service.verify_smtp(ss.resolve_smtp(db, tid))
    return {"ok": True, "scope": "global" if tid is None else tid,
            "verify": verify, **ss.public_smtp(db, tid)}


@router.post("/email/verify")
def verify_email(scope: str | None = None, db: Session = Depends(get_db),
                 user: AdminUser = Depends(require_roles(Role.CUSTOMER_OWNER))):
    """Check SMTP connectivity + login without sending a message."""
    tid = _scope_for(user, scope)
    return email_service.verify_smtp(ss.resolve_smtp(db, tid))


@router.get("/monitoring")
def get_monitoring(db: Session = Depends(get_db),
                   user: AdminUser = Depends(require_roles(Role.CUSTOMER_OWNER, Role.SECURITY_ADMIN))):
    tid = user.tenant_id
    cfg = ss.get_setting(db, "monitoring", tid) or {}
    return {
        "silent_access": cfg.get("silent_access", True),   # no employee notification (default on)
        "default_consent_mode": cfg.get("default_consent_mode", "silent"),
    }


@router.put("/monitoring")
def put_monitoring(body: dict, request: Request, db: Session = Depends(get_db),
                   user: AdminUser = Depends(require_roles(Role.CUSTOMER_OWNER, Role.SECURITY_ADMIN))):
    """Company monitoring/consent policy (PRD §24.2). The customer records its lawful choice here."""
    tid = user.tenant_id
    value = {}
    if "silent_access" in body:
        value["silent_access"] = bool(body["silent_access"])
    if body.get("default_consent_mode") in ("silent", "notify", "consent"):
        value["default_consent_mode"] = body["default_consent_mode"]
    ss.set_setting(db, "monitoring", tid, value)
    audit.record(db, action="monitoring_policy_update", tenant_id=tid, actor_id=user.id,
                 actor_email=user.email, target_type="setting", target_id="monitoring",
                 new_value=value, source_ip=client_ip(request))
    db.commit()
    return {"ok": True, **get_monitoring(db, user)}


@router.post("/email/test")
def test_email(body: dict, scope: str | None = None, db: Session = Depends(get_db),
               user: AdminUser = Depends(require_roles(Role.CUSTOMER_OWNER))):
    tid = _scope_for(user, scope)
    to = (body.get("to") or user.email or "").strip()
    if not to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Provide a 'to' address")
    smtp = ss.resolve_smtp(db, tid)
    res = email_service.send_email(
        to=to, subject="Endpoint Management — SMTP test",
        body="This is a test message confirming your email (SMTP) settings work.",
        smtp=smtp,
    )
    return {"delivered": res["delivered"], "via": res["via"], "detail": res["detail"]}
