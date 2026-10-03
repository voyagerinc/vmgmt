"""Request dependencies: authentication, RBAC and agent device auth (PRD §4, §24)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import AdminUser, Device, DeviceStatus, Role
from .security import decode_token

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

# Role capability matrix (PRD §4). Higher-privilege roles inherit via explicit sets below.
ROLE_RANK = {
    Role.AUDITOR: 0,
    Role.HELPDESK: 1,
    Role.MANAGER: 1,
    Role.SECURITY_ADMIN: 2,
    Role.IT_ADMIN: 2,
    Role.CUSTOMER_OWNER: 3,
    Role.PLATFORM_SUPER_ADMIN: 4,
}


def get_current_user(
    token: str | None = Depends(oauth2),
    db: Session = Depends(get_db),
) -> AdminUser:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_token(token)
        if payload.get("kind") != "access":
            raise ValueError("wrong token kind")
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = db.get(AdminUser, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


def require_roles(*roles: Role):
    allowed = set(roles)

    def _dep(user: AdminUser = Depends(get_current_user)) -> AdminUser:
        if user.role == Role.PLATFORM_SUPER_ADMIN or user.role in allowed:
            return user
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role for this action")

    return _dep


def require_min_rank(role: Role):
    threshold = ROLE_RANK[role]

    def _dep(user: AdminUser = Depends(get_current_user)) -> AdminUser:
        if ROLE_RANK.get(user.role, 0) >= threshold:
            return user
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient privilege")

    return _dep


def platform_admin(user: AdminUser = Depends(get_current_user)) -> AdminUser:
    if user.role != Role.PLATFORM_SUPER_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform super admin only")
    return user


def tenant_scope(user: AdminUser = Depends(get_current_user)) -> str:
    """Return the tenant id the request is scoped to (PRD §9 tenant isolation)."""
    if user.tenant_id is None and user.role != Role.PLATFORM_SUPER_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User has no tenant")
    return user.tenant_id  # may be None for platform admin (must pass ?tenant_id=)


def resolve_tenant(user: AdminUser, tenant_id: str | None) -> str:
    """Platform admin may act on any tenant via explicit id; others are pinned to their own."""
    if user.role == Role.PLATFORM_SUPER_ADMIN:
        if not tenant_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "tenant_id required for platform admin")
        return tenant_id
    if tenant_id and tenant_id != user.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-tenant access denied")
    return user.tenant_id


# ----------------------------------------------------------------- agent auth
def get_agent_device(
    x_device_id: str = Header(..., alias="X-Device-Id"),
    x_device_cert: str = Header(..., alias="X-Device-Cert"),
    db: Session = Depends(get_db),
) -> Device:
    """Authenticate an endpoint agent by per-device identity (PRD §10.3)."""
    device = db.get(Device, x_device_id)
    if not device or device.device_cert != x_device_cert:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Device authentication failed")
    if device.status == DeviceStatus.REVOKED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Device revoked")
    device.last_seen = datetime.now(timezone.utc)
    if device.status in (DeviceStatus.PENDING, DeviceStatus.OFFLINE):
        device.status = DeviceStatus.ACTIVE
    return device


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
