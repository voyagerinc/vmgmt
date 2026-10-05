"""Authentication & session (PRD §24: lockout, MFA hook, audit)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

import secrets
import uuid

from pathlib import Path

from .. import audit
from ..config import BASE_DIR, settings
from ..database import get_db
from ..deps import client_ip, get_current_user
from ..models import AdminUser, PasswordResetToken, Role, Tenant
from ..schemas import LoginIn, PasswordChange, TokenOut, UserOut
from ..security import (
    create_token,
    decode_token,
    hash_password,
    validate_password_strength,
    verify_license,
    verify_password,
)
from ..services import email_service
from ..services import settings_service as ss

# Generic response so these public endpoints never reveal whether an account exists.
_GENERIC = {"ok": True, "message": "If a matching account exists, an email has been sent."}

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    q = db.query(AdminUser).filter(AdminUser.email == body.email.lower())
    if body.tenant_id:
        q = q.filter(AdminUser.tenant_id == body.tenant_id)
    user = q.first()

    if not user:
        audit.record(db, action="login", actor_email=body.email, result="failure",
                     source_ip=ip, new_value={"reason": "no_user"})
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    now = datetime.now(timezone.utc)
    if user.locked_until and user.locked_until.replace(tzinfo=timezone.utc) > now:
        raise HTTPException(status.HTTP_423_LOCKED, "Account temporarily locked. Try again later.")

    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")

    if not verify_password(body.password, user.password_hash):
        user.failed_logins += 1
        if user.failed_logins >= settings.max_failed_logins:
            user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
            user.failed_logins = 0
        audit.record(db, action="login", tenant_id=user.tenant_id, actor_id=user.id,
                     actor_email=user.email, result="failure", source_ip=ip)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    if user.mfa_enabled:
        if not body.mfa_code:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "MFA code required")
        if not _verify_mfa(user, body.mfa_code):
            audit.record(db, action="login", tenant_id=user.tenant_id, actor_id=user.id,
                         actor_email=user.email, result="failure", source_ip=ip,
                         new_value={"reason": "mfa"})
            db.commit()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid MFA code")

    user.failed_logins = 0
    user.locked_until = None
    user.last_login = now
    audit.record(db, action="login", tenant_id=user.tenant_id, actor_id=user.id,
                 actor_email=user.email, source_ip=ip)
    db.commit()

    return TokenOut(
        access_token=create_token(user.id, user.role.value, user.tenant_id, "access"),
        refresh_token=create_token(user.id, user.role.value, user.tenant_id, "refresh"),
        role=user.role.value,
        tenant_id=user.tenant_id,
        full_name=user.full_name,
    )


@router.post("/refresh", response_model=TokenOut)
def refresh(body: dict, db: Session = Depends(get_db)):
    try:
        payload = decode_token(body.get("refresh_token", ""))
        if payload.get("kind") != "refresh":
            raise ValueError
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    user = db.get(AdminUser, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return TokenOut(
        access_token=create_token(user.id, user.role.value, user.tenant_id, "access"),
        refresh_token=create_token(user.id, user.role.value, user.tenant_id, "refresh"),
        role=user.role.value, tenant_id=user.tenant_id, full_name=user.full_name,
    )


@router.get("/me", response_model=UserOut)
def me(user: AdminUser = Depends(get_current_user)):
    return user


@router.post("/change-password")
def change_password(body: PasswordChange, request: Request,
                    user: AdminUser = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password incorrect")
    err = validate_password_strength(body.new_password)
    if err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err)
    user.password_hash = hash_password(body.new_password)
    audit.record(db, action="password_change", tenant_id=user.tenant_id, actor_id=user.id,
                 actor_email=user.email, source_ip=client_ip(request))
    db.commit()
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, user: AdminUser = Depends(get_current_user), db: Session = Depends(get_db)):
    audit.record(db, action="logout", tenant_id=user.tenant_id, actor_id=user.id,
                 actor_email=user.email, source_ip=client_ip(request))
    db.commit()
    return {"ok": True}


@router.post("/forgot-username")
def forgot_username(body: dict, request: Request, db: Session = Depends(get_db)):
    """Email the username(s) registered to a company's contact email (PRD §9). Public."""
    email = (body.get("email") or "").strip().lower()
    tenants = db.query(Tenant).filter(Tenant.contact_email.ilike(email)).all() if email else []
    # also allow lookup where the admin's own email was given
    users = db.query(AdminUser).filter(AdminUser.email == email).all() if email else []
    names: set[str] = {u.email for u in users}
    to = email
    for t in tenants:
        for u in db.query(AdminUser).filter(AdminUser.tenant_id == t.id).all():
            names.add(u.email)
        to = t.contact_email or to
    if names:
        tid = tenants[0].id if tenants else (users[0].tenant_id if users else None)
        email_service.send_email(
            to=to, subject="Your Voyager Endpoint Management username(s)",
            body=("Hello,\n\nThe following admin username(s) are registered for your account:\n\n"
                  + "\n".join(f"  • {n}" for n in sorted(names))
                  + f"\n\nSign in at: {settings.server_public_url}\n"
                    "If you also forgot your password, use 'Forgot password' on the sign-in page.\n"),
            smtp=ss.resolve_smtp(db, tid),
        )
        audit.record(db, action="forgot_username", tenant_id=tid, actor_email=to,
                     result="success", source_ip=client_ip(request))
        db.commit()
    return _GENERIC


@router.post("/forgot-password")
def forgot_password(body: dict, request: Request, db: Session = Depends(get_db)):
    """Email a secure, time-limited password-reset link to the account's email. Public."""
    email = (body.get("email") or "").strip().lower()
    user = db.query(AdminUser).filter(AdminUser.email == email, AdminUser.is_active == True).first()  # noqa: E712
    if user:
        tok = PasswordResetToken(
            user_id=user.id, token=uuid.uuid4().hex + uuid.uuid4().hex,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        )
        db.add(tok)
        link = f"{settings.server_public_url}/#reset/{tok.token}"
        email_service.send_email(
            to=user.email, subject="Reset your Voyager Endpoint Management password",
            body=(f"Hello,\n\nA password reset was requested for {user.email}.\n\n"
                  f"Reset link (valid 2 hours):\n{link}\n\n"
                  "If you did not request this, you can ignore this email; your password is unchanged.\n"),
            smtp=ss.resolve_smtp(db, user.tenant_id),
        )
        audit.record(db, action="forgot_password", tenant_id=user.tenant_id, actor_id=user.id,
                     actor_email=user.email, result="success", source_ip=client_ip(request))
        db.commit()
    return _GENERIC


@router.post("/local-reset")
def local_reset(body: dict, request: Request, db: Session = Depends(get_db)):
    """Offline reset for clients without email: write a one-time reset code to a FILE ON THE
    SERVER (valid 4h). Only someone with access to the server can read it, which is the proof
    of authority — the code is never returned in the HTTP response. Public endpoint."""
    email = (body.get("email") or "").strip().lower()
    reset_file = BASE_DIR / "PASSWORD_RESET.txt"
    user = db.query(AdminUser).filter(AdminUser.email == email, AdminUser.is_active == True).first()  # noqa: E712
    if user:
        tok = PasswordResetToken(
            user_id=user.id, token=uuid.uuid4().hex + uuid.uuid4().hex,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=4),
        )
        db.add(tok)
        try:
            reset_file.write_text(
                "Voyager Management Server - Password Reset\n"
                "==========================================\n\n"
                f"Account     : {user.email}\n"
                f"Reset code  : {tok.token}\n"
                f"Valid until : {tok.expires_at:%Y-%m-%d %H:%M} UTC  (4 hours, single use)\n\n"
                "To finish: on the sign-in page click 'Reset password' -> 'I have a reset code',\n"
                "paste the code above and set a new password. This code is one-time and expires.\n",
                encoding="utf-8")
        except Exception:
            pass
        audit.record(db, action="local_reset_issued", tenant_id=user.tenant_id, actor_id=user.id,
                     actor_email=user.email, result="success", source_ip=client_ip(request))
        db.commit()
    return {"ok": True, "file": str(reset_file),
            "message": ("If the account exists, a one-time reset code was written on THIS SERVER at:\n"
                        f"{reset_file}\n(valid 4 hours). Open that file on the server, then use "
                        "'I have a reset code' to set a new password.")}


@router.post("/reset-password")
def reset_password_with_token(body: dict, request: Request, db: Session = Depends(get_db)):
    """Consume a reset token or imported .txt key file payload and set a new password."""
    token = (body.get("token") or "").strip()
    new_pw = body.get("new_password") or ""

    # Extract embedded payload if full text file content was pasted or uploaded
    if "--- BEGIN RESET PAYLOAD ---" in token:
        try:
            token = token.split("--- BEGIN RESET PAYLOAD ---")[1].split("--- END RESET PAYLOAD ---")[0].strip()
        except Exception:
            pass

    err = validate_password_strength(new_pw)
    if err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err)

    user: AdminUser | None = None
    row = db.query(PasswordResetToken).filter(PasswordResetToken.token == token).first()

    if row and not row.used:
        exp_dt = row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at.tzinfo is None else row.expires_at
        if exp_dt >= datetime.now(timezone.utc):
            user = db.get(AdminUser, row.user_id)
            row.used = True

    if not user:
        # Fallback to HMAC signed license token verification (for reset files from License Server)
        payload = verify_license(token)
        if payload and payload.get("kind") == "pw_reset":
            exp_str = payload.get("exp")
            exp_dt = datetime.fromisoformat(exp_str) if exp_str else None
            if exp_dt and exp_dt >= datetime.now(timezone.utc):
                uid = payload.get("user_id")
                email = payload.get("email")
                if uid:
                    user = db.get(AdminUser, uid)
                if not user and email:
                    user = db.query(AdminUser).filter(AdminUser.email == email).first()

    if not user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired password reset key / file.")

    user.password_hash = hash_password(new_pw)
    user.failed_logins = 0
    user.locked_until = None
    user.cred_seq = (user.cred_seq or 0) + 1
    try:
        (BASE_DIR / "PASSWORD_RESET.txt").unlink(missing_ok=True)
    except Exception:
        pass
    audit.record(db, action="password_reset_complete", tenant_id=user.tenant_id, actor_id=user.id,
                 actor_email=user.email, result="success", source_ip=client_ip(request))
    db.commit()
    return {"ok": True, "email": user.email}


def _verify_mfa(user: AdminUser, code: str) -> bool:
    """TOTP verification stub. Accepts a stored static backup if TOTP lib unavailable."""
    try:
        import pyotp  # optional dependency
        return pyotp.TOTP(user.mfa_secret).verify(code, valid_window=1)
    except ImportError:
        return bool(user.mfa_secret) and code == user.mfa_secret[:6]
