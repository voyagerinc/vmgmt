"""Cloud License Portal — platform super-admin: customers, licenses, activation (PRD §8)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..database import get_db
from ..deps import client_ip, get_current_user, platform_admin
from ..models import AdminUser, License, LicenseEdition, LicenseStatus, Role, Tenant
from ..schemas import (
    ActivateIn,
    LicenseIn,
    LicenseOut,
    LicensePackageOut,
    ProvisionIn,
    ProvisionOut,
    TenantIn,
    TenantOut,
)
from ..security import hash_password, validate_password_strength
from ..services import email_service
from ..services import license_service as lic_svc


def _license_key_file(tenant: Tenant, lic: License) -> bytes:
    payload = {
        "product": "Voyager Endpoint Management Platform",
        "company": tenant.company_name,
        "tenant_id": tenant.id,
        "license_id": lic.id,
        "license_key": lic.signature,              # signed activation token
        "edition": lic.edition.value,
        "license_type": lic.license_type.value,
        "expiry": lic.expiry_date.isoformat(),
        "max_devices": lic.max_devices,
        "max_admins": lic.max_admins,
        "server_url": settings.server_public_url,
        "instructions": "Enter License ID and License Key in Server_Setup.exe first-run wizard.",
    }
    return json.dumps(payload, indent=2).encode()

router = APIRouter(prefix="/api", tags=["license-portal"])


# ---------------------------------------------------------------- tenants (customers)
@router.post("/tenants", response_model=TenantOut, dependencies=[Depends(platform_admin)])
def create_tenant(body: TenantIn, request: Request, db: Session = Depends(get_db),
                  admin: AdminUser = Depends(platform_admin)):
    t = Tenant(
        company_name=body.company_name, address=body.address,
        contact_email=body.contact_email, contact_phone=body.contact_phone,
        deployment_model=body.deployment_model,
    )
    db.add(t)
    db.flush()
    audit.record(db, action="tenant_create", tenant_id=t.id, actor_id=admin.id,
                 actor_email=admin.email, target_type="tenant", target_id=t.id,
                 new_value={"company": t.company_name}, source_ip=client_ip(request))
    db.commit()
    db.refresh(t)
    return t


@router.get("/tenants", response_model=list[TenantOut])
def list_tenants(db: Session = Depends(get_db), user: AdminUser = Depends(get_current_user)):
    if user.role == Role.PLATFORM_SUPER_ADMIN:
        return db.query(Tenant).order_by(Tenant.created_at.desc()).all()
    return db.query(Tenant).filter(Tenant.id == user.tenant_id).all()


@router.get("/tenants/{tenant_id}", response_model=TenantOut)
def get_tenant(tenant_id: str, db: Session = Depends(get_db), user: AdminUser = Depends(get_current_user)):
    if user.role != Role.PLATFORM_SUPER_ADMIN and user.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-tenant access denied")
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    return t


# ---------------------------------------------------------------- licenses
@router.post("/tenants/{tenant_id}/licenses", response_model=LicensePackageOut,
             dependencies=[Depends(platform_admin)])
def create_license(tenant_id: str, body: LicenseIn, request: Request,
                   db: Session = Depends(get_db), admin: AdminUser = Depends(platform_admin)):
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    from ..models import LicenseType
    if body.is_demo:
        term = 15
    elif body.license_type == LicenseType.LIFETIME:
        term = 365 * 100           # lifetime: effectively non-expiring (support = 1 year, tracked separately)
    else:
        term = body.term_days
    features = {**lic_svc.DEFAULT_FEATURES, **(body.features or {})}
    lic = License(
        tenant_id=tenant_id,
        edition=body.edition,
        license_type=body.license_type,
        start_date=datetime.now(timezone.utc),
        expiry_date=datetime.now(timezone.utc) + timedelta(days=term),
        max_devices=body.max_devices,
        max_admins=body.max_admins,
        max_storage_mb=body.max_storage_mb,
        is_demo=body.is_demo or body.edition == LicenseEdition.DEMO,
        features=features,
    )
    db.add(lic)
    db.flush()
    token = lic_svc.build_activation_token(lic, t.company_name)
    lic.signature = token
    audit.record(db, action="license_create", tenant_id=tenant_id, actor_id=admin.id,
                 actor_email=admin.email, target_type="license", target_id=lic.id,
                 new_value={"edition": lic.edition.value, "expiry": lic.expiry_date.isoformat()},
                 source_ip=client_ip(request))
    db.commit()
    return LicensePackageOut(
        license_id=lic.id, activation_token=token,
        company_name=t.company_name, server_hint="Enter these in Server_Setup.exe first-run wizard",
    )


@router.post("/tenants/provision", response_model=ProvisionOut, dependencies=[Depends(platform_admin)])
def provision_customer(body: ProvisionIn, request: Request, db: Session = Depends(get_db),
                       admin: AdminUser = Depends(platform_admin)):
    """Create company + owner login + license key in one step, and email the key (PRD §8.3, §9).

    Returns the owner password once (so the platform admin can hand it over) and a download
    URL for the .lic key file. The key is also emailed to the registered contact address.
    """
    # 1. company / tenant
    tenant = Tenant(company_name=body.company_name, address=body.address,
                    contact_email=body.contact_email, contact_phone=body.contact_phone,
                    deployment_model=body.deployment_model)
    db.add(tenant)
    db.flush()

    # 2. owner login
    owner_email = (body.owner_email or body.contact_email).lower()
    owner_pw = body.owner_password or secrets.token_urlsafe(10)
    err = validate_password_strength(owner_pw)
    if err and body.owner_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err)
    owner = AdminUser(tenant_id=tenant.id, email=owner_email, full_name=body.owner_name,
                      password_hash=hash_password(owner_pw), role=Role.CUSTOMER_OWNER)
    db.add(owner)

    # 3. license + key
    from ..models import LicenseType
    if body.is_demo or body.edition == LicenseEdition.DEMO:
        term = 15
    elif body.license_type == LicenseType.LIFETIME:
        term = 365 * 100
    else:
        term = body.term_days
    lic = License(
        tenant_id=tenant.id, edition=body.edition, license_type=body.license_type,
        start_date=datetime.now(timezone.utc),
        expiry_date=datetime.now(timezone.utc) + timedelta(days=term),
        max_devices=body.max_devices, max_admins=body.max_admins,
        is_demo=body.is_demo or body.edition == LicenseEdition.DEMO,
        features=lic_svc.DEFAULT_FEATURES,
    )
    db.add(lic)
    db.flush()
    lic.signature = lic_svc.build_activation_token(lic, tenant.company_name)

    audit.record(db, action="tenant_provision", tenant_id=tenant.id, actor_id=admin.id,
                 actor_email=admin.email, target_type="tenant", target_id=tenant.id,
                 new_value={"company": tenant.company_name, "owner": owner_email,
                            "license_id": lic.id}, source_ip=client_ip(request))

    # 4. email the key to the registered address
    email_status = "skipped"
    if body.send_email:
        keyfile = _license_key_file(tenant, lic)
        bodytext = (
            f"Dear {body.owner_name},\n\n"
            f"Your Voyager Endpoint Management account for {tenant.company_name} is ready.\n\n"
            f"Admin console : {settings.server_public_url}\n"
            f"Username      : {owner_email}\n"
            f"Password      : {owner_pw}\n\n"
            f"License ID    : {lic.id}\n"
            f"License Key   : {lic.signature}\n"
            f"Edition       : {lic.edition.value}\n"
            f"Devices       : up to {lic.max_devices}\n"
            f"Valid until   : {lic.expiry_date:%Y-%m-%d}\n\n"
            "ACTIVATION REQUIRED: this license is INACTIVE until it is attached to your server.\n"
            "Sign in with the username/password above, then paste the License ID and License Key\n"
            "on the activation screen (or in the Server_Setup.exe first-run wizard). Agents cannot\n"
            "enroll until the license is activated. Please also change your password after signing in.\n"
        )
        from ..services import settings_service as _ss
        res = email_service.send_email(
            to=body.contact_email,
            subject=f"Your Voyager Endpoint Management license — {tenant.company_name}",
            body=bodytext,
            attachments=[(f"{tenant.company_name}.lic", keyfile, "json")],
            smtp=_ss.resolve_smtp(db, None),
        )
        email_status = res["via"] if res["delivered"] else f"outbox ({res['via']})"
        audit.record(db, action="license_email", tenant_id=tenant.id, actor_id=admin.id,
                     actor_email=admin.email, target_type="license", target_id=lic.id,
                     new_value={"to": body.contact_email, "delivered": res["delivered"]})

    db.commit()
    return ProvisionOut(
        tenant_id=tenant.id, company_name=tenant.company_name,
        owner_email=owner_email, owner_password=owner_pw,
        license_id=lic.id, license_key=lic.signature, expiry=lic.expiry_date,
        max_devices=lic.max_devices,
        download_url=f"/api/licenses/{lic.id}/key", email_status=email_status,
    )


@router.get("/licenses/{license_id}/key")
def download_license_key(license_id: str, db: Session = Depends(get_db),
                         user: AdminUser = Depends(get_current_user)):
    """Download the .lic license-key file (platform admin, or the owning tenant)."""
    lic = db.get(License, license_id)
    if not lic:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "License not found")
    if user.role != Role.PLATFORM_SUPER_ADMIN and user.tenant_id != lic.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-tenant access denied")
    tenant = db.get(Tenant, lic.tenant_id)
    data = _license_key_file(tenant, lic)
    safe = "".join(c for c in tenant.company_name if c.isalnum() or c in " _-").strip() or "license"
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{safe}.lic"'})


@router.post("/licenses/provision-info")
def provision_info(body: dict, request: Request, db: Session = Depends(get_db)):
    """Cloud license server: validate a license key and return the company + owner so an
    on-premise Management Server can create the local admin account (PRD §8.3). Public —
    the license key is the proof of authorization."""
    license_id = (body.get("license_id") or "").strip()
    key = (body.get("license_key") or "").strip()
    server_id = (body.get("server_id") or "onprem").strip()
    lic = db.get(License, license_id)
    if not lic or not lic_svc.verify_activation(key, lic.id, lic.activation_secret):
        audit.record(db, action="license_activate", target_type="license", target_id=license_id,
                     result="failure", source_ip=client_ip(request),
                     new_value={"via": "provision-info"})
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid license id or key")
    st = lic_svc.effective_status(lic)
    if st != LicenseStatus.ACTIVE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"License is {st.value}")
    lic.activated = True
    lic.activated_server_id = server_id
    tenant = db.get(Tenant, lic.tenant_id)
    owner = (db.query(AdminUser)
             .filter(AdminUser.tenant_id == tenant.id, AdminUser.role == Role.CUSTOMER_OWNER)
             .order_by(AdminUser.created_at.asc()).first())
    audit.record(db, action="license_activate", tenant_id=tenant.id, target_type="license",
                 target_id=lic.id, new_value={"server_id": server_id, "via": "provision-info"},
                 source_ip=client_ip(request))
    db.commit()
    return {
        "company_name": tenant.company_name,
        "tenant_id": tenant.id,
        "contact_email": tenant.contact_email,
        "owner_email": owner.email if owner else None,
        "owner_name": owner.full_name if owner else "Account Owner",
        "edition": lic.edition.value,
        "license_type": lic.license_type.value,
        "expiry": lic.expiry_date.isoformat(),
        "max_devices": lic.max_devices,
        "max_admins": lic.max_admins,
        "features": lic.features,
        "activation_secret": lic.activation_secret,   # so the on-prem server can re-sign locally
    }


@router.post("/license/sync")
def license_sync(body: dict, request: Request, db: Session = Depends(get_db)):
    """Cloud: a client server refreshes its company owner credentials + entitlements.

    Returns the owner's email, password HASH (never plaintext) and credential version, so a
    cloud-initiated password reset propagates to the on-prem client (PRD §8). Public — the
    license key is the proof of authorization."""
    license_id = (body.get("license_id") or "").strip()
    key = (body.get("license_key") or "").strip()
    lic = db.get(License, license_id)
    if not lic or not lic_svc.verify_activation(key, lic.id, lic.activation_secret):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid license id or key")
    tenant = db.get(Tenant, lic.tenant_id)
    if tenant:
        c_ver = body.get("client_version")
        c_upd = body.get("client_updated_at")
        if c_ver:
            tenant.client_server_version = c_ver
        if c_upd:
            try:
                tenant.client_server_updated_at = datetime.fromisoformat(c_upd)
            except Exception:
                pass
        tenant.last_sync_at = datetime.now(timezone.utc)
        db.commit()

    owner = (db.query(AdminUser)
             .filter(AdminUser.tenant_id == lic.tenant_id, AdminUser.role == Role.CUSTOMER_OWNER)
             .order_by(AdminUser.created_at.asc()).first())
    return {
        "owner_email": owner.email if owner else None,
        "owner_password_hash": owner.password_hash if owner else None,
        "cred_seq": owner.cred_seq if owner else 0,
        "status": lic_svc.effective_status(lic).value,
        "expiry": lic.expiry_date.isoformat(),
        "max_devices": lic.max_devices,
        "features": lic.features,
    }


@router.get("/license/status")
def my_license_status(db: Session = Depends(get_db), user: AdminUser = Depends(get_current_user)):
    """Current tenant's license state for the console (activation gating)."""
    if user.role == Role.PLATFORM_SUPER_ADMIN:
        return {"role": "platform", "usable": True, "activated": True, "label": "platform"}
    lic = lic_svc.active_license(db, user.tenant_id)
    if not lic:
        return {"usable": False, "activated": False, "label": "no_license", "license_id": None}
    return {
        "usable": lic_svc.is_usable(lic), "activated": lic.activated,
        "label": lic_svc.activation_label(lic), "license_id": lic.id,
        "edition": lic.edition.value, "expiry": lic.expiry_date,
        "max_devices": lic.max_devices,
    }


@router.post("/license/activate-here")
def activate_here(body: dict, request: Request, db: Session = Depends(get_db),
                  user: AdminUser = Depends(get_current_user)):
    """Attach a license key to this server for the signed-in company admin (PRD §7.1, §8.3).

    Mirrors the Server_Setup.exe first-run step: the admin signs in with the id/password the
    platform issued and pastes the license key. Until this succeeds the license is inactive.
    """
    if user.role not in (Role.CUSTOMER_OWNER, Role.PLATFORM_SUPER_ADMIN):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the company owner can activate the license")
    license_id = (body.get("license_id") or "").strip()
    license_key = (body.get("license_key") or "").strip()
    lic = db.get(License, license_id)
    if not lic:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "License not found")
    if user.role != Role.PLATFORM_SUPER_ADMIN and lic.tenant_id != user.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "License belongs to another company")
    if not lic_svc.verify_activation(license_key, lic.id, lic.activation_secret):
        audit.record(db, action="license_activate", tenant_id=lic.tenant_id, actor_id=user.id,
                     actor_email=user.email, target_type="license", target_id=lic.id,
                     result="failure", source_ip=client_ip(request))
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid license key")
    st = lic_svc.effective_status(lic)
    if st == LicenseStatus.EXPIRED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "License has expired")
    lic.activated = True
    lic.activated_server_id = settings.server_public_url
    audit.record(db, action="license_activate", tenant_id=lic.tenant_id, actor_id=user.id,
                 actor_email=user.email, target_type="license", target_id=lic.id,
                 new_value={"server": settings.server_public_url}, source_ip=client_ip(request))
    db.commit()
    return {"ok": True, "activated": True, "expiry": lic.expiry_date, "max_devices": lic.max_devices}


@router.post("/license/activate-online")
def activate_online(body: dict, request: Request, db: Session = Depends(get_db),
                    user: AdminUser = Depends(get_current_user)):
    """On-prem server: activate against the cloud license server, then create the local
    company + license + company-admin so the cloud-issued admin works here (PRD §8.3)."""
    import json
    import urllib.request
    if user.role != Role.PLATFORM_SUPER_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the local administrator can activate the server")
    license_id = (body.get("license_id") or "").strip()
    key = (body.get("license_key") or "").strip()
    owner_pw = (body.get("owner_password") or "").strip()
    server = (body.get("license_server") or settings.license_server or "").strip().rstrip("/")
    if not (license_id and key):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "License ID and License Key are required")
    if not server:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "No license server configured (set EMP_LICENSE_SERVER or pass license_server)")
    if owner_pw:
        err = validate_password_strength(owner_pw)
        if err:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, err)

    # ---- call the cloud license server ----
    payload = json.dumps({"license_id": license_id, "license_key": key,
                          "server_id": settings.server_public_url}).encode()
    req = urllib.request.Request(f"{server}/api/licenses/provision-info", data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            info = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"License server rejected activation: {detail}")
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not reach license server {server}: {e}")

    # ---- create/update local tenant, license and company owner ----
    tenant = db.get(Tenant, info["tenant_id"])
    if not tenant:
        tenant = Tenant(id=info["tenant_id"], company_name=info["company_name"],
                        contact_email=info.get("contact_email"), deployment_model="on_premise")
        db.add(tenant)
        db.flush()

    lic = db.get(License, license_id)
    exp = datetime.fromisoformat(info["expiry"])
    from ..models import LicenseType
    if not lic:
        lic = License(id=license_id, tenant_id=tenant.id,
                      edition=LicenseEdition(info["edition"]),
                      license_type=LicenseType(info.get("license_type", "subscription_monthly")),
                      expiry_date=exp, max_devices=info["max_devices"], max_admins=info["max_admins"],
                      features=info.get("features") or {},
                      activation_secret=info.get("activation_secret") or secrets.token_hex(32),
                      activated=True, activated_server_id=settings.server_public_url)
        db.add(lic)
    else:
        lic.activated = True
        lic.expiry_date = exp
        lic.max_devices = info["max_devices"]
    lic.status = LicenseStatus.ACTIVE
    lic.signature = key
    db.flush()

    owner_email = (info.get("owner_email") or info.get("contact_email") or "").lower()
    created_pw = None
    if owner_email:
        owner = db.query(AdminUser).filter(AdminUser.tenant_id == tenant.id,
                                           AdminUser.email == owner_email).first()
        if not owner:
            created_pw = owner_pw or secrets.token_urlsafe(10)
            owner = AdminUser(tenant_id=tenant.id, email=owner_email,
                              full_name=info.get("owner_name", "Account Owner"),
                              password_hash=hash_password(created_pw), role=Role.CUSTOMER_OWNER)
            db.add(owner)
        elif owner_pw:
            owner.password_hash = hash_password(owner_pw)
            created_pw = owner_pw

    audit.record(db, action="license_activate", tenant_id=tenant.id, actor_id=user.id,
                 actor_email=user.email, target_type="license", target_id=lic.id,
                 new_value={"via": "online", "server": server}, source_ip=client_ip(request))
    db.commit()
    return {"ok": True, "company_name": tenant.company_name, "owner_email": owner_email,
            "owner_password": created_pw,
            "message": "License activated. Sign in with the company admin account below."}


@router.post("/license/sync-now")
def license_sync_now(db: Session = Depends(get_db), user: AdminUser = Depends(get_current_user)):
    """Client server: pull owner-credential resets + entitlements from the license server now."""
    from ..services import license_sync
    if user.role not in (Role.PLATFORM_SUPER_ADMIN, Role.CUSTOMER_OWNER):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not permitted")
    return license_sync.run_client_sync(db)


@router.get("/tenants/{tenant_id}/licenses", response_model=list[LicenseOut])
def list_licenses(tenant_id: str, db: Session = Depends(get_db),
                  user: AdminUser = Depends(get_current_user)):
    if user.role != Role.PLATFORM_SUPER_ADMIN and user.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-tenant access denied")
    rows = db.query(License).filter(License.tenant_id == tenant_id).all()
    for r in rows:                      # reflect live expiry
        r.status = lic_svc.effective_status(r)
    return rows


@router.post("/licenses/{license_id}/revoke", dependencies=[Depends(platform_admin)])
def revoke_license(license_id: str, request: Request, db: Session = Depends(get_db),
                   admin: AdminUser = Depends(platform_admin)):
    lic = db.get(License, license_id)
    if not lic:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "License not found")
    old = lic.status.value
    lic.status = LicenseStatus.REVOKED
    audit.record(db, action="license_revoke", tenant_id=lic.tenant_id, actor_id=admin.id,
                 actor_email=admin.email, target_type="license", target_id=lic.id,
                 old_value={"status": old}, new_value={"status": "revoked"},
                 source_ip=client_ip(request))
    db.commit()
    return {"ok": True}


@router.post("/licenses/{license_id}/renew", dependencies=[Depends(platform_admin)])
def renew_license(license_id: str, extra_days: int, request: Request, db: Session = Depends(get_db),
                  admin: AdminUser = Depends(platform_admin)):
    """Renewal updates entitlement without reinstalling software (PRD §8.3)."""
    lic = db.get(License, license_id)
    if not lic:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "License not found")
    base = max(lic.expiry_date.replace(tzinfo=timezone.utc), datetime.now(timezone.utc))
    lic.expiry_date = base + timedelta(days=extra_days)
    if lic.status in (LicenseStatus.EXPIRED, LicenseStatus.SUSPENDED):
        lic.status = LicenseStatus.ACTIVE
    audit.record(db, action="license_renew", tenant_id=lic.tenant_id, actor_id=admin.id,
                 actor_email=admin.email, target_type="license", target_id=lic.id,
                 new_value={"expiry": lic.expiry_date.isoformat()}, source_ip=client_ip(request))
    db.commit()
    return {"ok": True, "expiry": lic.expiry_date}


# ---------------------------------------------------------------- activation (server-side, no auth)
@router.post("/licenses/activate")
def activate(body: ActivateIn, request: Request, db: Session = Depends(get_db)):
    """Called by a Management Server at install time (PRD §7.1, §8.3). Public endpoint."""
    lic = db.get(License, body.license_id)
    if not lic:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "License not found")
    if not lic_svc.verify_activation(body.activation_token, lic.id, lic.activation_secret):
        audit.record(db, action="license_activate", tenant_id=lic.tenant_id, result="failure",
                     target_type="license", target_id=lic.id, source_ip=client_ip(request))
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid activation token")
    status_now = lic_svc.effective_status(lic)
    if status_now != LicenseStatus.ACTIVE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"License is {status_now.value}")
    if lic.activated and lic.activated_server_id and lic.activated_server_id != body.server_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "License already activated on another server")
    lic.activated = True
    lic.activated_server_id = body.server_id
    audit.record(db, action="license_activate", tenant_id=lic.tenant_id, target_type="license",
                 target_id=lic.id, new_value={"server_id": body.server_id}, source_ip=client_ip(request))
    db.commit()
    return {
        "ok": True, "tenant_id": lic.tenant_id, "edition": lic.edition.value,
        "expiry": lic.expiry_date, "max_devices": lic.max_devices,
        "max_admins": lic.max_admins, "features": lic.features,
    }
