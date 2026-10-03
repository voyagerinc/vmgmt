"""Security primitives (PRD §24): password hashing, JWT, license signing, evidence crypto."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.fernet import Fernet
from passlib.context import CryptContext

from .config import settings

_pwd = CryptContext(schemes=["argon2"], deprecated="auto")
_SECRET = settings.resolve_secret()
_ALGO = "HS256"
_fernet = Fernet(settings.resolve_evidence_key())


# --------------------------------------------------------------------- passwords
def hash_password(raw: str) -> str:
    return _pwd.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return _pwd.verify(raw, hashed)
    except Exception:
        return False


def validate_password_strength(raw: str) -> str | None:
    if len(raw) < settings.password_min_length:
        return f"Password must be at least {settings.password_min_length} characters."
    classes = sum(bool(any(c in grp for c in raw)) for grp in (
        "abcdefghijklmnopqrstuvwxyz",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "0123456789",
        "!@#$%^&*()-_=+[]{};:,.<>?/",
    ))
    if classes < 3:
        return "Password must mix upper, lower, digits and symbols (at least 3 of 4)."
    return None


# --------------------------------------------------------------------- JWT
def create_token(sub: str, role: str, tenant_id: str | None, kind: str = "access") -> str:
    now = datetime.now(timezone.utc)
    if kind == "refresh":
        exp = now + timedelta(days=settings.refresh_token_days)
    else:
        exp = now + timedelta(minutes=settings.access_token_minutes)
    payload = {"sub": sub, "role": role, "tenant": tenant_id, "kind": kind,
               "iat": now, "exp": exp}
    return jwt.encode(payload, _SECRET, algorithm=_ALGO)


def decode_token(token: str) -> dict:
    return jwt.decode(token, _SECRET, algorithms=[_ALGO])


# --------------------------------------------------------------------- license signing
def sign_license(payload: dict) -> str:
    """Deterministic HMAC signature over a canonical license payload (PRD §8, §35 license abuse)."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(body).decode() + "." + sig
    return token


def verify_license(token: str) -> dict | None:
    try:
        body_b64, sig = token.split(".", 1)
        body = base64.urlsafe_b64decode(body_b64.encode())
        expected = hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        return json.loads(body)
    except Exception:
        return None


# --------------------------------------------------------------------- signed agent jobs
def sign_job(job: dict) -> str:
    return sign_license(job)   # same HMAC construction; distinct payload shape


def verify_job(token: str) -> dict | None:
    return verify_license(token)


# --------------------------------------------------------------------- evidence crypto (PRD §17.3)
def encrypt_bytes(data: bytes) -> bytes:
    return _fernet.encrypt(data)


def decrypt_bytes(blob: bytes) -> bytes:
    return _fernet.decrypt(blob)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
