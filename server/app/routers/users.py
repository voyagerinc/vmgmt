"""Admin user & RBAC management (PRD §4, §24)."""
from __future__ import annotations

import secrets

from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..database import get_db
from ..deps import client_ip, get_current_user, require_roles, resolve_tenant
from ..models import AdminUser, PasswordResetToken, Role, Tenant
from ..schemas import UserIn, UserOut
from ..security import hash_password, sign_license, validate_password_strength
from ..services import email_service
from ..services import license_service as lic_svc
from ..services import settings_service as ss

router = APIRouter(prefix="/api/users", tags=["users"])

_MANAGE = require_roles(Role.CUSTOMER_OWNER)


@router.get("", response_model=list[UserOut])
def list_users(tenant_id: str | None = Query(None), db: Session = Depends(get_db),
               user: AdminUser = Depends(get_current_user)):
    tid = resolve_tenant(user, tenant_id)
    return db.query(AdminUser).filter(AdminUser.tenant_id == tid).all()


@router.post("/{user_id}/download-reset-key")
@router.get("/{user_id}/download-reset-key")
def download_reset_key(user_id: str, request: Request = None, db: Session = Depends(get_db),
                       actor: AdminUser = Depends(_MANAGE)):
    """Generate and stream a signed Password Reset Key File (.txt) from the License Server."""
    u = db.get(AdminUser, user_id)
    if not u or (actor.role != Role.PLATFORM_SUPER_ADMIN and u.tenant_id != actor.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    tenant = db.get(Tenant, u.tenant_id)
    company_name = tenant.company_name if tenant else "Default"

    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=7)

    payload = {
        "user_id": u.id,
        "email": u.email,
        "tenant_id": u.tenant_id,
        "kind": "pw_reset",
        "iat": now.isoformat(),
        "exp": exp.isoformat()
    }
    signed_token = sign_license(payload)

    tok = PasswordResetToken(user_id=u.id, token=signed_token, expires_at=exp)
    db.add(tok)
    u.cred_seq = (u.cred_seq or 0) + 1
    audit.record(db, action="download_reset_key", tenant_id=u.tenant_id, actor_id=actor.id,
                 actor_email=actor.email, target_type="user", target_id=u.id,
                 new_value={"email": u.email}, source_ip=client_ip(request) if request else None)
    db.commit()

    txt_content = (
        "================================================================\n"
        "VOYAGER INC - LICENSE SERVER PASSWORD RESET KEY FILE\n"
        "================================================================\n"
        f"Company     : {company_name}\n"
        f"Username    : {u.email}\n"
        f"Tenant ID   : {u.tenant_id}\n"
        f"Issued At   : {now:%Y-%m-%d %H:%M:%S} UTC\n"
        f"Expires At  : {exp:%Y-%m-%d %H:%M:%S} UTC (7 Days)\n"
        "================================================================\n"
        "INSTRUCTIONS FOR CLIENT MANAGEMENT SERVER:\n"
        "  1. Open your Client Management Server login console.\n"
        "  2. Click 'Forgot password?'.\n"
        "  3. Select 'Import Reset Key File (.txt)' and choose this file.\n"
        "  4. Set your new password to immediately restore admin access.\n"
        "================================================================\n"
        "--- BEGIN RESET PAYLOAD ---\n"
        f"{signed_token}\n"
        "--- END RESET PAYLOAD ---\n"
    )

    safe_company = "".join(c for c in company_name if c.isalnum() or c in " _-").strip() or "client"
    filename = f"RESET_KEY_{safe_company}_{u.email}.txt"
    return Response(content=txt_content, media_type="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("", response_model=UserOut)
def create_user(body: UserIn, request: Request, tenant_id: str | None = Query(None),
                db: Session = Depends(get_db), actor: AdminUser = Depends(_MANAGE)):
    tid = resolve_tenant(actor, tenant_id)
    if body.role == Role.PLATFORM_SUPER_ADMIN and actor.role != Role.PLATFORM_SUPER_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot create platform super admin")
    err = validate_password_strength(body.password)
    if err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err)
    exists = db.query(AdminUser).filter(AdminUser.tenant_id == tid,
                                        AdminUser.email == body.email.lower()).first()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already in use for this tenant")
    try:
        lic_svc.enforce_admin_limit(db, tid)
    except lic_svc.LicenseError as e:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, e.message)
    u = AdminUser(tenant_id=tid, email=body.email.lower(), full_name=body.full_name,
                  password_hash=hash_password(body.password), role=body.role)
    db.add(u)
    db.flush()
    audit.record(db, action="user_create", tenant_id=tid, actor_id=actor.id, actor_email=actor.email,
                 target_type="user", target_id=u.id, new_value={"email": u.email, "role": u.role.value},
                 source_ip=client_ip(request))
    db.commit()
    db.refresh(u)
    return u


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: str, request: Request, role: Role | None = None,
                is_active: bool | None = None, db: Session = Depends(get_db),
                actor: AdminUser = Depends(_MANAGE)):
    u = db.get(AdminUser, user_id)
    if not u or (actor.role != Role.PLATFORM_SUPER_ADMIN and u.tenant_id != actor.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    old = {"role": u.role.value, "is_active": u.is_active}
    if role is not None:
        u.role = role
    if is_active is not None:
        u.is_active = is_active
    audit.record(db, action="user_update", tenant_id=u.tenant_id, actor_id=actor.id,
                 actor_email=actor.email, target_type="user", target_id=u.id, old_value=old,
                 new_value={"role": u.role.value, "is_active": u.is_active}, source_ip=client_ip(request))
    db.commit()
    db.refresh(u)
    return u


@router.post("/{user_id}/reset-password")
def reset_password(user_id: str, body: dict | None = None, request: Request = None,
                   db: Session = Depends(get_db), actor: AdminUser = Depends(_MANAGE)):
    """Reset a user's password (sysadmin, or company owner for their tenant).

    Returns the new password once. Original passwords are argon2-hashed and cannot be shown.
    Optionally emails the new password to the tenant's registered address.
    """
    body = body or {}
    u = db.get(AdminUser, user_id)
    if not u or (actor.role != Role.PLATFORM_SUPER_ADMIN and u.tenant_id != actor.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    new_pw = (body.get("new_password") or "").strip() or secrets.token_urlsafe(10)
    if body.get("new_password"):
        err = validate_password_strength(new_pw)
        if err:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, err)
    u.password_hash = hash_password(new_pw)
    u.failed_logins = 0
    u.locked_until = None
    u.cred_seq = (u.cred_seq or 0) + 1      # lets client servers pull this reset
    audit.record(db, action="user_password_reset", tenant_id=u.tenant_id, actor_id=actor.id,
                 actor_email=actor.email, target_type="user", target_id=u.id,
                 new_value={"email": u.email}, source_ip=client_ip(request) if request else None)

    emailed = "skipped"
    if body.get("email_it"):
        tenant = db.get(Tenant, u.tenant_id)
        to = (tenant.contact_email if tenant else None) or u.email
        res = email_service.send_email(
            to=to,
            subject="Your Voyager Endpoint Management password was reset",
            body=(f"Hello,\n\nThe password for {u.email} has been reset.\n\n"
                  f"Username : {u.email}\nPassword : {new_pw}\n"
                  f"Console  : {settings.server_public_url}\n\n"
                  "Please sign in and change it from Settings.\n"),
            smtp=ss.resolve_smtp(db, u.tenant_id),
        )
        emailed = res["via"] if res["delivered"] else f"outbox ({res['via']})"
    db.commit()
    return {"ok": True, "email": u.email, "new_password": new_pw, "emailed": emailed}


@router.delete("/{user_id}")
def delete_user(user_id: str, request: Request, db: Session = Depends(get_db),
                actor: AdminUser = Depends(_MANAGE)):
    u = db.get(AdminUser, user_id)
    if not u or (actor.role != Role.PLATFORM_SUPER_ADMIN and u.tenant_id != actor.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if u.id == actor.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot delete yourself")
    audit.record(db, action="user_delete", tenant_id=u.tenant_id, actor_id=actor.id,
                 actor_email=actor.email, target_type="user", target_id=u.id,
                 old_value={"email": u.email}, source_ip=client_ip(request))
    db.delete(u)
    db.commit()
    return {"ok": True}
