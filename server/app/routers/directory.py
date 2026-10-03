"""Employees & Assets (PRD §11, §12)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from .. import audit
from ..database import get_db
from ..deps import client_ip, get_current_user, require_roles, resolve_tenant
from ..models import AdminUser, Asset, AssetHistory, Employee, Role
from ..schemas import AssetIn, AssetOut, EmployeeIn, EmployeeOut

router = APIRouter(prefix="/api", tags=["directory"])
_WRITE = require_roles(Role.IT_ADMIN, Role.CUSTOMER_OWNER, Role.SECURITY_ADMIN)


# ----------------------------------------------------------------- employees
@router.get("/employees", response_model=list[EmployeeOut])
def list_employees(tenant_id: str | None = Query(None), q: str | None = None,
                   db: Session = Depends(get_db), user: AdminUser = Depends(get_current_user)):
    tid = resolve_tenant(user, tenant_id)
    query = db.query(Employee).filter(Employee.tenant_id == tid)
    if q:
        query = query.filter(Employee.name.ilike(f"%{q}%"))
    return query.order_by(Employee.name).all()


@router.post("/employees", response_model=EmployeeOut)
def create_employee(body: EmployeeIn, request: Request, tenant_id: str | None = Query(None),
                    db: Session = Depends(get_db), user: AdminUser = Depends(_WRITE)):
    tid = resolve_tenant(user, tenant_id)
    if db.query(Employee).filter(Employee.tenant_id == tid,
                                 Employee.employee_code == body.employee_code).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Employee code already exists")
    e = Employee(tenant_id=tid, **body.model_dump())
    db.add(e)
    db.flush()
    audit.record(db, action="employee_create", tenant_id=tid, actor_id=user.id,
                 actor_email=user.email, target_type="employee", target_id=e.id,
                 source_ip=client_ip(request))
    db.commit()
    db.refresh(e)
    return e


@router.patch("/employees/{emp_id}", response_model=EmployeeOut)
def update_employee(emp_id: str, body: EmployeeIn, db: Session = Depends(get_db),
                    user: AdminUser = Depends(_WRITE)):
    e = db.get(Employee, emp_id)
    if not e or (user.role != Role.PLATFORM_SUPER_ADMIN and e.tenant_id != user.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found")
    for k, v in body.model_dump().items():
        setattr(e, k, v)
    db.commit()
    db.refresh(e)
    return e


# ----------------------------------------------------------------- assets
@router.get("/assets", response_model=list[AssetOut])
def list_assets(tenant_id: str | None = Query(None), lifecycle: str | None = None,
                category: str | None = None, db: Session = Depends(get_db),
                user: AdminUser = Depends(get_current_user)):
    tid = resolve_tenant(user, tenant_id)
    query = db.query(Asset).filter(Asset.tenant_id == tid)
    if lifecycle:
        query = query.filter(Asset.lifecycle == lifecycle)
    if category:
        query = query.filter(Asset.category == category)
    return query.order_by(Asset.asset_tag).all()


@router.post("/assets", response_model=AssetOut)
def create_asset(body: AssetIn, request: Request, tenant_id: str | None = Query(None),
                 db: Session = Depends(get_db), user: AdminUser = Depends(_WRITE)):
    tid = resolve_tenant(user, tenant_id)
    a = Asset(tenant_id=tid, **body.model_dump())
    db.add(a)
    db.flush()
    db.add(AssetHistory(tenant_id=tid, asset_id=a.id, action="created",
                        detail={"lifecycle": a.lifecycle.value}, actor=user.email))
    audit.record(db, action="asset_create", tenant_id=tid, actor_id=user.id, actor_email=user.email,
                 target_type="asset", target_id=a.id, source_ip=client_ip(request))
    db.commit()
    db.refresh(a)
    return a


@router.patch("/assets/{asset_id}", response_model=AssetOut)
def update_asset(asset_id: str, body: AssetIn, db: Session = Depends(get_db),
                 user: AdminUser = Depends(_WRITE)):
    a = db.get(Asset, asset_id)
    if not a or (user.role != Role.PLATFORM_SUPER_ADMIN and a.tenant_id != user.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset not found")
    old_lifecycle = a.lifecycle.value
    for k, v in body.model_dump().items():
        setattr(a, k, v)
    if a.lifecycle.value != old_lifecycle:
        db.add(AssetHistory(tenant_id=a.tenant_id, asset_id=a.id, action="lifecycle_change",
                            detail={"from": old_lifecycle, "to": a.lifecycle.value}, actor=user.email))
    db.commit()
    db.refresh(a)
    return a


@router.get("/assets/{asset_id}/history")
def asset_history(asset_id: str, db: Session = Depends(get_db),
                  user: AdminUser = Depends(get_current_user)):
    a = db.get(Asset, asset_id)
    if not a or (user.role != Role.PLATFORM_SUPER_ADMIN and a.tenant_id != user.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset not found")
    rows = (db.query(AssetHistory).filter(AssetHistory.asset_id == asset_id)
            .order_by(AssetHistory.created_at.desc()).all())
    return [{"action": r.action, "detail": r.detail, "actor": r.actor, "at": r.created_at}
            for r in rows]
