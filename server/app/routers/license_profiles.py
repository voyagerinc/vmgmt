"""License types/profiles managed on the license server (platform super admin)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import audit
from ..database import get_db
from ..deps import client_ip, get_current_user, platform_admin
from ..models import AdminUser, License, LicenseProfile
from ..services import license_service as lic_svc

router = APIRouter(prefix="/api/license-profiles", tags=["license-profiles"])


def _out(p: LicenseProfile) -> dict:
    return {"id": p.id, "name": p.name, "description": p.description, "edition": p.edition,
            "features": lic_svc.full_features(p.features), "default_max_devices": p.default_max_devices,
            "default_max_admins": p.default_max_admins, "default_term_days": p.default_term_days,
            "is_system": p.is_system, "active": p.active}


@router.get("")
def list_profiles(db: Session = Depends(get_db), user: AdminUser = Depends(get_current_user)):
    """All license types + the feature catalogue (labels). Any signed-in admin may read."""
    lic_svc.ensure_system_profiles(db)
    db.commit()
    rows = db.query(LicenseProfile).order_by(LicenseProfile.is_system.desc(), LicenseProfile.name).all()
    return {"profiles": [_out(p) for p in rows], "feature_labels": lic_svc.FEATURE_LABELS}


@router.post("", dependencies=[Depends(platform_admin)])
def create_profile(body: dict, request: Request, db: Session = Depends(get_db),
                   admin: AdminUser = Depends(platform_admin)):
    name = " ".join(str(body.get("name") or "").split())
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "License type name is required")
    if db.query(LicenseProfile).filter(func.lower(LicenseProfile.name) == name.lower()).first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"A license type named '{name}' already exists")
    p = LicenseProfile(name=name[:80], description=body.get("description"),
                       edition=body.get("edition") or "custom",
                       features=lic_svc.full_features(body.get("features")),
                       default_max_devices=int(body.get("default_max_devices") or 25),
                       default_max_admins=int(body.get("default_max_admins") or 3),
                       default_term_days=int(body.get("default_term_days") or 365))
    db.add(p)
    db.flush()
    audit.record(db, action="license_profile_create", actor_id=admin.id, actor_email=admin.email,
                 target_type="license_profile", target_id=p.id, new_value={"name": p.name},
                 source_ip=client_ip(request))
    db.commit()
    return _out(p)


@router.put("/{profile_id}", dependencies=[Depends(platform_admin)])
def update_profile(profile_id: str, body: dict, request: Request, db: Session = Depends(get_db),
                   admin: AdminUser = Depends(platform_admin)):
    p = db.get(LicenseProfile, profile_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "License type not found")
    if body.get("name") and not p.is_system:
        name = " ".join(str(body["name"]).split())[:80]
        clash = db.query(LicenseProfile).filter(func.lower(LicenseProfile.name) == name.lower(),
                                                LicenseProfile.id != p.id).first()
        if clash:
            raise HTTPException(status.HTTP_409_CONFLICT, f"A license type named '{name}' already exists")
        p.name = name
    if "description" in body:
        p.description = body.get("description")
    if "edition" in body and not p.is_system:
        p.edition = body.get("edition") or "custom"
    if "features" in body:
        p.features = lic_svc.full_features(body["features"])
    for k in ("default_max_devices", "default_max_admins", "default_term_days"):
        if k in body:
            try:
                setattr(p, k, max(1, int(body[k])))
            except (TypeError, ValueError):
                pass
    if "active" in body and not p.is_system:
        p.active = bool(body["active"])
    audit.record(db, action="license_profile_update", actor_id=admin.id, actor_email=admin.email,
                 target_type="license_profile", target_id=p.id, new_value={"name": p.name},
                 source_ip=client_ip(request))
    db.commit()
    return _out(p)


@router.get("/{profile_id}")
def get_profile(profile_id: str, db: Session = Depends(get_db), user: AdminUser = Depends(get_current_user)):
    p = db.get(LicenseProfile, profile_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "License type not found")
    return _out(p)


@router.delete("/{profile_id}", dependencies=[Depends(platform_admin)])
def delete_profile(profile_id: str, request: Request, db: Session = Depends(get_db),
                   admin: AdminUser = Depends(platform_admin)):
    p = db.get(LicenseProfile, profile_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "License type not found")
    if p.is_system:
        raise HTTPException(status.HTTP_409_CONFLICT, "Built-in license types cannot be deleted (you can edit them).")
    in_use = db.query(License).filter(License.profile_name == p.name).count()
    if in_use:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Cannot delete license type '{p.name}' because {in_use} license(s) are currently using it. "
                            "Change those licenses to another type first.")
    audit.record(db, action="license_profile_delete", actor_id=admin.id, actor_email=admin.email,
                 target_type="license_profile", target_id=p.id, old_value={"name": p.name},
                 source_ip=client_ip(request))
    db.delete(p)
    db.commit()
    return {"ok": True}

