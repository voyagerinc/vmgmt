"""Auto-generated weekly & monthly summaries (PRD §2.7). Excel primary, emailed if SMTP set."""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Alert, Device, DeviceStatus, Evidence, Notification, Tenant
from . import email_service
from . import settings_service as ss


def _summary_rows(db: Session, tenant_id: str, since: datetime) -> list[list]:
    dev = db.query(Device).filter(Device.tenant_id == tenant_id)
    total = dev.count()
    online = dev.filter(Device.status == DeviceStatus.ACTIVE).count()
    offline = dev.filter(Device.status == DeviceStatus.OFFLINE).count()
    new_alerts = db.query(func.count(Alert.id)).filter(
        Alert.tenant_id == tenant_id, Alert.created_at >= since).scalar()
    evidence = db.query(func.count(Evidence.id)).filter(
        Evidence.tenant_id == tenant_id, Evidence.captured_at >= since).scalar()
    return [
        ["Metric", "Value"],
        ["Devices total", total],
        ["Devices online", online],
        ["Devices offline", offline],
        ["Alerts raised (period)", new_alerts],
        ["Evidence captured (period)", evidence],
        ["Period start (UTC)", since.strftime("%Y-%m-%d %H:%M")],
        ["Generated (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")],
    ]


def _xlsx(title: str, rows: list[list]) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    for r in rows:
        ws.append([str(c) for c in r])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def run_periodic_report(db: Session, period: str) -> int:
    """period in {'weekly','monthly'}. One summary per tenant; emailed where possible."""
    days = 7 if period == "weekly" else 30
    since = datetime.now(timezone.utc) - timedelta(days=days)
    count = 0
    for t in db.query(Tenant).filter(Tenant.status == "active").all():
        rows = _summary_rows(db, t.id, since)
        body = "\n".join(f"{r[0]}: {r[1]}" for r in rows[1:])
        db.add(Notification(tenant_id=t.id, kind="report",
                            title=f"{period.capitalize()} summary — {t.company_name}",
                            body=body, severity="info"))
        if t.contact_email:
            xlsx = _xlsx(f"{period} summary", rows)
            email_service.send_email(
                to=t.contact_email,
                subject=f"Voyager {period} summary — {t.company_name}",
                body=f"Attached is your {period} endpoint summary.\n\n{body}\n",
                attachments=[(f"{period}_summary_{datetime.now(timezone.utc):%Y%m%d}.xlsx",
                              xlsx, "xlsx")],
                smtp=ss.resolve_smtp(db, t.id),
            )
        count += 1
    return count
