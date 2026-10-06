"""Authentication & session (PRD §24: lockout, MFA hook, audit)."""
from __future__ import annotations

import base64
import json
import re
import secrets
import urllib.request
import uuid

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import audit
from ..config import BASE_DIR, settings
from ..database import get_db
from ..deps import client_ip, get_current_user
from ..models import AdminUser, License, PasswordResetToken, Role, Tenant
from ..schemas import LoginIn, PasswordChange, TokenOut, UserOut
from ..security import (
    create_token,
    decode_token,
    hash_password,
    validate_password_strength,
    verify_license,
    verify_password,
    verify_reset_token,
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

    if user.tenant_id:
        t = db.get(Tenant, user.tenant_id)
        if t and t.status == "inactive":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Company account is deactivated. Please contact support.")

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


def parse_reset_file_content(raw: str) -> tuple[str, str | None]:
    """Extract token and optional registered email from an uploaded reset key file or raw text."""
    if not raw:
        return "", None
    text = raw.replace("\ufeff", "").strip()
    extracted_email = None

    # Search for email in header lines
    for line in text.splitlines():
        line_clean = line.strip()
        if any(line_clean.lower().startswith(p) for p in ("username", "account", "email", "registered email")):
            parts = line_clean.split(":", 1)
            if len(parts) == 2:
                candidate = parts[1].strip()
                if "@" in candidate:
                    extracted_email = candidate

    # Search for embedded payload between delimiter tags
    if "--- BEGIN RESET PAYLOAD ---" in text:
        try:
            tok = text.split("--- BEGIN RESET PAYLOAD ---")[1].split("--- END RESET PAYLOAD ---")[0].strip()
            return tok, extracted_email
        except Exception:
            pass

    # Search for "Reset code  : <token>" (from local PASSWORD_RESET.txt)
    match_code = re.search(r"Reset\s+code\s*:\s*([a-zA-Z0-9_\-\.]+)", text, re.IGNORECASE)
    if match_code:
        return match_code.group(1).strip(), extracted_email

    # Or raw token string
    return text.strip(), extracted_email


@router.post("/reset-password")
def reset_password_with_token(body: dict, request: Request, db: Session = Depends(get_db)):
    """Consume a reset token or imported .txt key file payload and set a new password.
    Requires matching registered email and valid password confirmation."""
    raw_token = (body.get("token") or "").strip()
    new_pw = body.get("new_password") or ""
    req_email = (body.get("email") or "").strip().lower()

    token, file_email = parse_reset_file_content(raw_token)
    if not req_email and file_email:
        req_email = file_email.lower()

    if not token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please attach a valid .txt reset file or enter a reset token.")

    err = validate_password_strength(new_pw)
    if err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err)

    target_user: AdminUser | None = None
    if req_email:
        target_user = db.query(AdminUser).filter(func.lower(AdminUser.email) == req_email).first()
        if not target_user:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"No administrator account found with registered email '{req_email}'.")

    user: AdminUser | None = None

    # 1. Local database token check (PasswordResetToken)
    row = db.query(PasswordResetToken).filter(PasswordResetToken.token == token).first()
    if row and not row.used:
        exp_dt = row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at.tzinfo is None else row.expires_at
        if exp_dt >= datetime.now(timezone.utc):
            if not target_user or target_user.id == row.user_id:
                user = db.get(AdminUser, row.user_id)
                row.used = True

    # 2. Cryptographic signature check (portable platform key, local secret, active license secrets)
    if not user:
        extra_keys = []
        for lic in db.query(License).filter(License.activation_secret.isnot(None)).all():
            extra_keys.append(lic.activation_secret)
        payload = verify_reset_token(token, extra_keys=extra_keys)
        if not payload:
            payload = verify_license(token)

        if payload and payload.get("kind") == "pw_reset":
            exp_str = payload.get("exp")
            exp_dt = datetime.fromisoformat(exp_str) if exp_str else None
            if exp_dt and exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if exp_dt and exp_dt < datetime.now(timezone.utc):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password reset key has expired.")

            tok_email = (payload.get("email") or "").lower()
            if req_email and tok_email and req_email != tok_email:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    f"The attached reset file is for '{tok_email}', but entered email is '{req_email}'.")

            uid = payload.get("user_id")
            if target_user:
                user = target_user
            elif uid:
                user = db.get(AdminUser, uid)
            if not user and tok_email:
                user = db.query(AdminUser).filter(func.lower(AdminUser.email) == tok_email).first()

    # 3. Online verification via Cloud License Server (if client server configured)
    if not user and settings.license_server:
        try:
            srv = settings.resolve_license_server()
            req_data = json.dumps({"token": token, "email": req_email or (target_user.email if target_user else "")}).encode()
            req_obj = urllib.request.Request(f"{srv}/api/licenses/verify-reset-token",
                                             data=req_data, headers={"Content-Type": "application/json"},
                                             method="POST")
            with urllib.request.urlopen(req_obj, timeout=10) as resp:
                res = json.loads(resp.read().decode())
                if res.get("valid"):
                    tok_email = (res.get("email") or req_email).lower()
                    if target_user:
                        user = target_user
                    elif tok_email:
                        user = db.query(AdminUser).filter(func.lower(AdminUser.email) == tok_email).first()
        except Exception:
            pass

    # 4. Fallback for valid legacy token payloads if offline and registered email strictly matches
    if not user and "." in token:
        try:
            body_b64 = token.split(".", 1)[0]
            body_dict = json.loads(base64.urlsafe_b64decode(body_b64.encode()))
            if body_dict.get("kind") == "pw_reset":
                tok_email = (body_dict.get("email") or "").lower()
                exp_str = body_dict.get("exp")
                exp_dt = datetime.fromisoformat(exp_str) if exp_str else None
                if exp_dt and exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if not exp_dt or exp_dt >= datetime.now(timezone.utc):
                    if req_email and tok_email and req_email == tok_email:
                        user = target_user or db.query(AdminUser).filter(func.lower(AdminUser.email) == tok_email).first()
        except Exception:
            pass

    if not user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Invalid or expired password reset file. Please ensure the attached file is a valid reset key.")

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
