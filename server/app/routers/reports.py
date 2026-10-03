"""Dashboard, reports & exports, audit log viewer (PRD §22, §25)."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_roles, resolve_tenant
from ..models import (
    AdminUser,
    Alert,
    AlertStatus,
    Asset,
    AuditEvent,
    Device,
    DeviceStatus,
    Employee,
    Evidence,
    License,
    Role,
    Software,
)
from ..services import license_service as lic_svc

router = APIRouter(prefix="/api", tags=["reports"])


@router.get("/dashboard")
def dashboard(tenant_id: str | None = Query(None), db: Session = Depends(get_db),
              user: AdminUser = Depends(get_current_user)):
    tid = resolve_tenant(user, tenant_id)
    dev_q = db.query(Device).filter(Device.tenant_id == tid)
    total = dev_q.count()
    online = dev_q.filter(Device.status == DeviceStatus.ACTIVE).count()
    offline = dev_q.filter(Device.status == DeviceStatus.OFFLINE).count()
    pending = dev_q.filter(Device.status == DeviceStatus.PENDING).count()
    open_alerts = db.query(Alert).filter(Alert.tenant_id == tid,
                                         Alert.status == AlertStatus.OPEN).count()
    sev_rows = (db.query(Alert.severity, func.count(Alert.id))
                .filter(Alert.tenant_id == tid, Alert.status == AlertStatus.OPEN)
                .group_by(Alert.severity).all())
    lic = lic_svc.active_license(db, tid)
    lic_info = None
    if lic:
        lic_info = {"edition": lic.edition.value, "status": lic_svc.effective_status(lic).value,
                    "expiry": lic.expiry_date, "max_devices": lic.max_devices,
                    "used_devices": total,
                    "days_left": (lic.expiry_date.replace(tzinfo=timezone.utc)
                                  - datetime.now(timezone.utc)).days}
    return {
        "devices": {"total": total, "online": online, "offline": offline, "pending": pending},
        "alerts_open": open_alerts,
        "alerts_by_severity": {s.value if hasattr(s, "value") else s: c for s, c in sev_rows},
        "employees": db.query(Employee).filter(Employee.tenant_id == tid).count(),
        "assets": db.query(Asset).filter(Asset.tenant_id == tid).count(),
        "evidence": db.query(Evidence).filter(Evidence.tenant_id == tid).count(),
        "software_distinct": db.query(func.count(func.distinct(Software.name)))
                               .filter(Software.tenant_id == tid).scalar(),
        "license": lic_info,
    }


# ----------------------------------------------------------------- report data
def _report_rows(db: Session, tid: str, kind: str) -> tuple[list[str], list[list]]:
    if kind == "assets":
        rows = db.query(Asset).filter(Asset.tenant_id == tid).all()
        return (["tag", "category", "name", "serial", "lifecycle", "warranty_expiry"],
                [[a.asset_tag, a.category, a.name, a.serial_no or "", a.lifecycle.value,
                  str(a.warranty_expiry or "")] for a in rows])
    if kind == "devices":
        rows = db.query(Device).filter(Device.tenant_id == tid).all()
        return (["hostname", "os", "status", "ip", "last_seen", "agent_version"],
                [[d.hostname, f"{d.os_name or ''} {d.os_version or ''}".strip(), d.status.value,
                  d.ip_address or "", str(d.last_seen or ""), d.agent_version or ""] for d in rows])
    if kind == "software":
        rows = db.query(Software).filter(Software.tenant_id == tid, Software.present == True).all()  # noqa
        return (["name", "publisher", "version", "device_id", "list_status"],
                [[s.name, s.publisher or "", s.version or "", s.device_id, s.list_status] for s in rows])
    if kind == "alerts":
        rows = db.query(Alert).filter(Alert.tenant_id == tid).all()
        return (["rule", "severity", "status", "device_id", "created_at", "message"],
                [[a.rule_name, a.severity.value, a.status.value, a.device_id or "",
                  str(a.created_at), a.message] for a in rows])
    if kind == "license":
        rows = db.query(License).filter(License.tenant_id == tid).all()
        return (["edition", "status", "expiry", "max_devices", "max_admins"],
                [[l.edition.value, lic_svc.effective_status(l).value, str(l.expiry_date),
                  l.max_devices, l.max_admins] for l in rows])
    # employees default
    rows = db.query(Employee).filter(Employee.tenant_id == tid).all()
    return (["code", "name", "department", "designation", "status"],
            [[e.employee_code, e.name, e.department or "", e.designation or "", e.status] for e in rows])


@router.get("/reports/{kind}")
def report(kind: str, fmt: str = Query("json", pattern="^(json|csv|xlsx|pdf)$"),
           tenant_id: str | None = Query(None), db: Session = Depends(get_db),
           user: AdminUser = Depends(get_current_user)):
    tid = resolve_tenant(user, tenant_id)
    headers, rows = _report_rows(db, tid, kind)

    if fmt == "json":
        return {"kind": kind, "columns": headers, "rows": rows}
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(headers)
        w.writerows(rows)
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'})
    if fmt == "xlsx":
        return _xlsx(kind, headers, rows)
    return _pdf(kind, headers, rows)


def _xlsx(kind: str, headers: list[str], rows: list[list]) -> Response:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = kind[:31]
    ws.append(headers)
    for r in rows:
        ws.append([str(c) for c in r])
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{kind}.xlsx"'})


def _pdf(kind: str, headers: list[str], rows: list[list]) -> Response:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), title=f"{kind} report")
    styles = getSampleStyleSheet()
    data = [headers] + [[str(c)[:40] for c in r] for r in rows[:500]]
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2a44")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f2f7")]),
    ]))
    elems = [Paragraph(f"{kind.title()} Report — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}",
                       styles["Title"]), table]
    doc.build(elems)
    return Response(buf.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{kind}.pdf"'})


# ----------------------------------------------------------------- audit log
@router.get("/audit")
def audit_log(tenant_id: str | None = Query(None), action: str | None = None,
              limit: int = Query(200, le=2000), offset: int = 0, db: Session = Depends(get_db),
              user: AdminUser = Depends(require_roles(Role.SECURITY_ADMIN, Role.CUSTOMER_OWNER,
                                                      Role.AUDITOR))):
    q = db.query(AuditEvent)
    if user.role != Role.PLATFORM_SUPER_ADMIN:
        q = q.filter(AuditEvent.tenant_id == user.tenant_id)
    elif tenant_id:
        q = q.filter(AuditEvent.tenant_id == tenant_id)
    if action:
        q = q.filter(AuditEvent.action == action)
    rows = q.order_by(AuditEvent.ts.desc()).offset(offset).limit(limit).all()
    return [{"ts": e.ts, "action": e.action, "actor": e.actor_email, "target_type": e.target_type,
             "target_id": e.target_id, "result": e.result, "source_ip": e.source_ip,
             "old": e.old_value, "new": e.new_value, "correlation_id": e.correlation_id} for e in rows]
