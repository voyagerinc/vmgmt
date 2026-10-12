"""Quotation support: the seller ('your company') details and the quote-number sequence.

The quotation document itself is built and printed in the browser (License Server panel); this
only stores the seller profile and hands out sequential quote numbers.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from .. import audit
from ..database import get_db
from ..deps import client_ip, get_current_user, platform_admin
from ..models import AdminUser
from ..services import settings_service as ss

router = APIRouter(prefix="/api/quotation", tags=["quotation"])
COMPANY_KEY = "quote_company"
SEQ_KEY = "quote_seq"
_FIELDS = ("company_name", "address", "gst_no", "phone", "email", "terms", "bank_details")


def _fy(now: datetime) -> str:
    """Indian financial year label, e.g. 2026-27 (Apr-Mar)."""
    y = now.year if now.month >= 4 else now.year - 1
    return f"{y}-{str(y + 1)[-2:]}"


def _peek_number(db: Session) -> str:
    seq = ss.get_setting(db, SEQ_KEY, None) or {}
    fy = _fy(datetime.now(timezone.utc))
    return f"Q/{fy}/{int(seq.get(fy, 0)) + 1:04d}"


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), user: AdminUser = Depends(get_current_user)):
    """Seller company details + the next suggested quote number."""
    company = ss.get_setting(db, COMPANY_KEY, None) or {}
    return {"company": {k: company.get(k, "") for k in _FIELDS}, "next_number": _peek_number(db)}


@router.put("/company", dependencies=[Depends(platform_admin)])
def set_company(body: dict, request: Request, db: Session = Depends(get_db),
                admin: AdminUser = Depends(platform_admin)):
    value = {k: (str(body.get(k) or "")[:1000]) for k in _FIELDS}
    ss.set_setting(db, COMPANY_KEY, None, value)
    audit.record(db, action="quote_company_update", actor_id=admin.id, actor_email=admin.email,
                 target_type="setting", target_id=COMPANY_KEY, source_ip=client_ip(request))
    db.commit()
    return {"ok": True, "company": value}


@router.post("/number", dependencies=[Depends(platform_admin)])
def next_number(db: Session = Depends(get_db), admin: AdminUser = Depends(platform_admin)):
    """Reserve and return the next quote number (increments the per-financial-year counter)."""
    seq = dict(ss.get_setting(db, SEQ_KEY, None) or {})
    fy = _fy(datetime.now(timezone.utc))
    n = int(seq.get(fy, 0)) + 1
    seq[fy] = n
    ss.set_setting(db, SEQ_KEY, None, seq)
    db.commit()
    return {"number": f"Q/{fy}/{n:04d}"}
