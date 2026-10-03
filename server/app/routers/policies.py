"""Policy & rule management (PRD §20). Writes bump device policy_version for resync."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from .. import audit
from ..database import get_db
from ..deps import client_ip, get_current_user, require_roles, resolve_tenant
from ..models import AdminUser, Device, Policy, Role
from ..schemas import PolicyIn, PolicyOut

router = APIRouter(prefix="/api/policies", tags=["policies"])
_WRITE = require_roles(Role.SECURITY_ADMIN, Role.IT_ADMIN, Role.CUSTOMER_OWNER)


def _bump(db: Session, tenant_id: str) -> None:
    for d in db.query(Device).filter(Device.tenant_id == tenant_id).all():
        d.policy_version += 1


@router.get("", response_model=list[PolicyOut])
def list_policies(tenant_id: str | None = Query(None), db: Session = Depends(get_db),
                  user: AdminUser = Depends(get_current_user)):
    tid = resolve_tenant(user, tenant_id)
    return db.query(Policy).filter(Policy.tenant_id == tid).order_by(Policy.priority).all()


@router.post("", response_model=PolicyOut)
def create_policy(body: PolicyIn, request: Request, tenant_id: str | None = Query(None),
                  db: Session = Depends(get_db), user: AdminUser = Depends(_WRITE)):
    tid = resolve_tenant(user, tenant_id)
    p = Policy(tenant_id=tid, **body.model_dump())
    db.add(p)
    _bump(db, tid)
    db.flush()
    audit.record(db, action="policy_create", tenant_id=tid, actor_id=user.id, actor_email=user.email,
                 target_type="policy", target_id=p.id, new_value={"name": p.name, "rule": p.rule},
                 source_ip=client_ip(request))
    db.commit()
    db.refresh(p)
    return p


@router.put("/{policy_id}", response_model=PolicyOut)
def update_policy(policy_id: str, body: PolicyIn, request: Request, db: Session = Depends(get_db),
                  user: AdminUser = Depends(_WRITE)):
    p = _scoped(db, user, policy_id)
    old = {"name": p.name, "rule": p.rule, "enabled": p.enabled}
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    _bump(db, p.tenant_id)
    audit.record(db, action="policy_update", tenant_id=p.tenant_id, actor_id=user.id,
                 actor_email=user.email, target_type="policy", target_id=p.id, old_value=old,
                 new_value={"name": p.name, "rule": p.rule}, source_ip=client_ip(request))
    db.commit()
    db.refresh(p)
    return p


@router.delete("/{policy_id}")
def delete_policy(policy_id: str, request: Request, db: Session = Depends(get_db),
                  user: AdminUser = Depends(_WRITE)):
    p = _scoped(db, user, policy_id)
    audit.record(db, action="policy_delete", tenant_id=p.tenant_id, actor_id=user.id,
                 actor_email=user.email, target_type="policy", target_id=p.id,
                 old_value={"name": p.name}, source_ip=client_ip(request))
    db.delete(p)
    _bump(db, p.tenant_id)
    db.commit()
    return {"ok": True}


def _scoped(db: Session, user: AdminUser, policy_id: str) -> Policy:
    p = db.get(Policy, policy_id)
    if not p or (user.role != Role.PLATFORM_SUPER_ADMIN and p.tenant_id != user.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Policy not found")
    return p
