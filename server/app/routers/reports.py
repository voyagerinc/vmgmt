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
def _hm(secs) -> str:
    secs = int(secs or 0)
    return f"{secs // 3600}h {secs % 3600 // 60:02d}m"


def _t(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


REPORT_TITLES = {
    "computer_summary": "Computer-wise summary", "activity": "Activity (detailed)",
    "app_usage": "Application usage by computer", "web_usage": "Website usage by computer",
    "tracking": "Logins, network, USB & email events", "devices": "Device inventory",
    "software": "Software", "alerts": "Alerts", "assets": "Assets", "license": "License",
    "employees": "Employees",
}


def _report_rows(db: Session, tid: str, kind: str, device_id: str | None = None, start=None, end=None,
                 tz_offset: int = 0) -> tuple[list[str], list[list]]:
    """Report rows; device_id limits to one computer, start/end (UTC) to a period."""
    from ..models import TrackingEvent
    from ..services import activity_report as ar
    names = ar.device_names(db, tid)
    host = lambda dev: (names.get(dev) or {}).get("hostname") or (dev or "")      # noqa: E731
    local = timedelta(minutes=-int(tz_offset or 0))                                 # UTC -> viewer's time
    lt = lambda dt: _t(dt + local) if dt else ""                                    # noqa: E731

    def in_period(q, col):
        if start:
            q = q.filter(col >= start)
        if end:
            q = q.filter(col < end)
        return q

    if kind == "computer_summary":
        act = {c["device_id"]: c for c in ar.computer_summary(db, tid, start, end, device_id)}
        tq = in_period(db.query(TrackingEvent.device_id, TrackingEvent.event_type, func.count(TrackingEvent.id))
                       .filter(TrackingEvent.tenant_id == tid), TrackingEvent.ts)
        if device_id:
            tq = tq.filter(TrackingEvent.device_id == device_id)
        tr: dict = {}
        for dev, et, n in tq.group_by(TrackingEvent.device_id, TrackingEvent.event_type).all():
            tr.setdefault(dev, {})[et] = n
        aq = in_period(db.query(Alert.device_id, func.count(Alert.id)).filter(Alert.tenant_id == tid), Alert.created_at)
        alerts = dict(aq.group_by(Alert.device_id).all())
        dq = db.query(Device).filter(Device.tenant_id == tid)
        if device_id:
            dq = dq.filter(Device.id == device_id)
        rows = []
        for d in dq.order_by(Device.hostname).all():
            a, t = act.get(d.id, {}), tr.get(d.id, {})
            rows.append([d.hostname, (names.get(d.id) or {}).get("employee") or "", d.department or "",
                         _hm(a.get("app_seconds")), _hm(a.get("web_seconds")), _hm(a.get("idle_seconds")),
                         a.get("top_app") or "", a.get("top_site") or "", a.get("web_visits", 0),
                         t.get("logon", 0), t.get("copied_to_usb", 0) + t.get("copied_from_usb", 0),
                         t.get("email_sent", 0) + t.get("email_attached", 0), alerts.get(d.id, 0),
                         lt(d.last_seen), d.agent_version or ""])
        return (["computer", "employee", "department", "app time", "web time", "idle time", "top app",
                 "top website", "web visits", "logons", "USB file copies", "email files", "alerts",
                 "last seen", "agent"], rows)
    if kind == "activity":
        _, ev = ar.events(db, tid, start, end, device_id, None, None, limit=50000)
        return (["time", "computer", "employee", "type", "application", "website", "title", "duration", "url"],
                [[lt(e["ts"]), e["hostname"], e["employee"] or "", e["type"], e["app"] or "", e["domain"] or "",
                  e["title"] or "", _hm(e["seconds"]), e["url"] or ""] for e in ev])
    if kind in ("app_usage", "web_usage"):
        rows = ar.usage(db, tid, "domain" if kind == "web_usage" else "application", start, end, device_id)
        return (["computer", "employee", "website" if kind == "web_usage" else "application", "time",
                 "events", "first", "last"],
                [[r["hostname"], r["employee"] or "", r["name"], _hm(r["seconds"]), r["events"],
                  lt(r["first"]), lt(r["last"])] for r in rows])
    if kind == "tracking":
        q = in_period(db.query(TrackingEvent).filter(TrackingEvent.tenant_id == tid), TrackingEvent.ts)
        if device_id:
            q = q.filter(TrackingEvent.device_id == device_id)
        return (["time", "computer", "user", "category", "event", "detail", "file", "size", "target"],
                [[lt(e.ts), host(e.device_id), e.user or "", e.category, e.event_type, e.detail or "",
                  e.file_name or "", e.file_size if e.file_size is not None else "", e.target or ""]
                 for e in q.order_by(TrackingEvent.ts.desc()).limit(50000)])
    if kind == "assets":
        rows = db.query(Asset).filter(Asset.tenant_id == tid).all()
        return (["tag", "category", "name", "serial", "lifecycle", "warranty_expiry"],
                [[a.asset_tag, a.category, a.name, a.serial_no or "", a.lifecycle.value,
                  str(a.warranty_expiry or "")] for a in rows])
    if kind == "devices":
        q = db.query(Device).filter(Device.tenant_id == tid)
        if device_id:
            q = q.filter(Device.id == device_id)
        return (["computer", "employee", "department", "os", "status", "ip", "last_seen", "agent_version"],
                [[d.hostname, (names.get(d.id) or {}).get("employee") or "", d.department or "",
                  f"{d.os_name or ''} {d.os_version or ''}".strip(), d.status.value,
                  d.ip_address or "", lt(d.last_seen), d.agent_version or ""] for d in q.order_by(Device.hostname)])
    if kind == "software":
        q = db.query(Software).filter(Software.tenant_id == tid, Software.present == True)  # noqa: E712
        if device_id:
            q = q.filter(Software.device_id == device_id)
        return (["computer", "name", "publisher", "version", "list_status"],
                [[host(s.device_id), s.name, s.publisher or "", s.version or "", s.list_status]
                 for s in q.order_by(Software.device_id, Software.name)])
    if kind == "alerts":
        q = in_period(db.query(Alert).filter(Alert.tenant_id == tid), Alert.created_at)
        if device_id:
            q = q.filter(Alert.device_id == device_id)
        return (["created", "computer", "rule", "severity", "status", "message"],
                [[lt(a.created_at), host(a.device_id), a.rule_name, a.severity.value, a.status.value, a.message]
                 for a in q.order_by(Alert.created_at.desc())])
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
           tenant_id: str | None = Query(None), device_id: str | None = None,
           date_from: str | None = None, date_to: str | None = None, tz_offset: int = 0,
           db: Session = Depends(get_db), user: AdminUser = Depends(get_current_user)):
    """Reports for the whole company or one computer (device_id), optionally for a period
    (local dates date_from/date_to + the browser's tz_offset)."""
    from ..services import activity_report as ar
    tid = resolve_tenant(user, tenant_id)
    start = end = None
    if date_from or date_to:
        start, end = ar.window(date_from, date_to, tz_offset)
    elif kind in ("activity", "app_usage", "web_usage", "computer_summary", "tracking"):
        start, end = ar.window(None, None, tz_offset, hours=24 * 7)       # default: last 7 days
    headers, rows = _report_rows(db, tid, kind, device_id, start, end, tz_offset)
    title = REPORT_TITLES.get(kind, kind)
    if device_id:
        title += f" - {(ar.device_names(db, tid).get(device_id) or {}).get('hostname', device_id)}"
    if date_from or date_to:
        title += f" ({date_from or '...'} to {date_to or '...'})"

    if fmt == "json":
        return {"kind": kind, "columns": headers, "rows": rows}
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(headers)
        w.writerows(rows)
        return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{_fname(title)}.csv"'})
    if fmt == "xlsx":
        return _xlsx(_fname(title), headers, rows)
    return _pdf(title, headers, rows)


def _fname(title: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in title).strip("_")[:120] or "report"


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
    elems = [Paragraph(f"{kind} — generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}",
                       styles["Title"]), table]
    doc.build(elems)
    return Response(buf.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{_fname(kind)}.pdf"'})


# ----------------------------------------------------------------- audit log
@router.post("/reports/summary/run")
def run_summary(period: str = Query("weekly", pattern="^(weekly|monthly)$"),
                db: Session = Depends(get_db),
                user: AdminUser = Depends(require_roles(Role.CUSTOMER_OWNER))):
    """Generate the weekly/monthly summary now (also runs automatically on schedule)."""
    from ..services import scheduled_reports
    if user.role == Role.PLATFORM_SUPER_ADMIN:
        n = scheduled_reports.run_periodic_report(db, period)
        db.commit()
        return {"ok": True, "tenants": n, "period": period}
    # company owner: just their tenant
    from datetime import datetime, timedelta, timezone
    from ..models import Tenant, Notification
    t = db.get(Tenant, user.tenant_id)
    since = datetime.now(timezone.utc) - timedelta(days=7 if period == "weekly" else 30)
    rows = scheduled_reports._summary_rows(db, t.id, since)
    body = "\n".join(f"{r[0]}: {r[1]}" for r in rows[1:])
    db.add(Notification(tenant_id=t.id, kind="report",
                        title=f"{period.capitalize()} summary — {t.company_name}", body=body))
    db.commit()
    return {"ok": True, "period": period, "summary": body}


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
