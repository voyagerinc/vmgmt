"""Web / application activity: per-computer summaries, top apps/sites and detailed events.

Shared by the Activity page and the downloadable reports. Dates are the viewer's local days:
date_from/date_to are YYYY-MM-DD and tz_offset is the browser's getTimezoneOffset() in minutes
(IST = -330), so "today" means the local day, not the UTC day.
Time per event: duration_seconds (agent 4.4+ measures it); legacy "active_window" samples from
older agents (one per ~60 s heartbeat, no duration) count as 60 s.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from ..models import ActivityEvent, Device, Employee

APP_TYPES = ("app", "active_window", "app_start", "app_stop")
WEB_TYPES = ("web", "web_visit")


def window(date_from: str | None, date_to: str | None, tz_offset: int = 0, hours: int | None = None):
    """UTC (start, end) for local dates; default = last `hours` (24) when no dates."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    off = timedelta(minutes=int(tz_offset or 0))
    start = end = None
    if date_from:
        start = datetime.combine(date.fromisoformat(date_from), datetime.min.time()) + off
    if date_to:
        end = datetime.combine(date.fromisoformat(date_to), datetime.min.time()) + timedelta(days=1) + off
    if not start and not end:
        start = now - timedelta(hours=hours or 24)
    return start, end


def _secs():
    return func.sum(case((ActivityEvent.duration_seconds > 0, ActivityEvent.duration_seconds),
                         (ActivityEvent.event_type == "active_window", 60), else_=0))


def _base(db: Session, tid: str, start, end, device_id=None):
    q = db.query(ActivityEvent).filter(ActivityEvent.tenant_id == tid)
    if start:
        q = q.filter(ActivityEvent.ts >= start)
    if end:
        q = q.filter(ActivityEvent.ts < end)
    if device_id:
        q = q.filter(ActivityEvent.device_id == device_id)
    return q


def _filters(q, start, end, tid, device_id):
    q = q.filter(ActivityEvent.tenant_id == tid)
    if start:
        q = q.filter(ActivityEvent.ts >= start)
    if end:
        q = q.filter(ActivityEvent.ts < end)
    if device_id:
        q = q.filter(ActivityEvent.device_id == device_id)
    return q


def device_names(db: Session, tid: str) -> dict:
    out = {}
    for d, emp in (db.query(Device, Employee.name).outerjoin(Employee, Employee.id == Device.employee_id)
                   .filter(Device.tenant_id == tid).all()):
        out[d.id] = {"hostname": d.hostname, "employee": emp, "department": d.department}
    return out


def top(db: Session, tid: str, by: str, start, end, device_id=None, limit: int = 25) -> list[dict]:
    if by == "domain":
        col, types = ActivityEvent.domain, WEB_TYPES
    else:
        col, types = ActivityEvent.application, APP_TYPES
    q = db.query(col, _secs(), func.count(ActivityEvent.id), func.count(func.distinct(ActivityEvent.device_id)))
    q = _filters(q, start, end, tid, device_id).filter(col.isnot(None), ActivityEvent.event_type.in_(types))
    rows = q.group_by(col).order_by(_secs().desc()).limit(limit).all()
    return [{"name": n, "seconds": int(s or 0), "events": c, "computers": dc} for n, s, c, dc in rows]


def computer_summary(db: Session, tid: str, start, end, device_id=None) -> list[dict]:
    names = device_names(db, tid)
    kind = case((ActivityEvent.event_type.in_(APP_TYPES), "app"),
                (ActivityEvent.event_type.in_(WEB_TYPES), "web"), else_=ActivityEvent.event_type)
    q = db.query(ActivityEvent.device_id, kind, _secs(), func.count(ActivityEvent.id),
                 func.min(ActivityEvent.ts), func.max(ActivityEvent.ts))
    rows = _filters(q, start, end, tid, device_id).group_by(ActivityEvent.device_id, kind).all()
    per: dict = {}
    for dev, k, secs, n, first, last in rows:
        r = per.setdefault(dev, {"device_id": dev, **names.get(dev, {"hostname": dev, "employee": None, "department": None}),
                                 "app_seconds": 0, "web_seconds": 0, "idle_seconds": 0, "web_visits": 0,
                                 "events": 0, "first": first, "last": last})
        if k == "app":
            r["app_seconds"] += int(secs or 0)
        elif k == "web":
            r["web_seconds"] += int(secs or 0)
            r["web_visits"] += n
        elif k == "idle":
            r["idle_seconds"] += int(secs or 0)
        r["events"] += n
        r["first"] = min(r["first"], first) if r["first"] else first
        r["last"] = max(r["last"], last) if r["last"] else last
    for dev, r in per.items():
        ta = top(db, tid, "application", start, end, dev, 1)
        ts = top(db, tid, "domain", start, end, dev, 1)
        r["top_app"] = ta[0]["name"] if ta else None
        r["top_site"] = ts[0]["name"] if ts else None
        r["active_seconds"] = r["app_seconds"]           # foreground use, idle excluded
    return sorted(per.values(), key=lambda r: -r["app_seconds"])


def usage(db: Session, tid: str, by: str, start, end, device_id=None) -> list[dict]:
    """Per computer x application (or site) totals."""
    names = device_names(db, tid)
    if by == "domain":
        col, types = ActivityEvent.domain, WEB_TYPES
    else:
        col, types = ActivityEvent.application, APP_TYPES
    q = db.query(ActivityEvent.device_id, col, _secs(), func.count(ActivityEvent.id),
                 func.min(ActivityEvent.ts), func.max(ActivityEvent.ts))
    q = _filters(q, start, end, tid, device_id).filter(col.isnot(None), ActivityEvent.event_type.in_(types))
    rows = q.group_by(ActivityEvent.device_id, col).order_by(ActivityEvent.device_id, _secs().desc()).all()
    return [{"device_id": dev, **names.get(dev, {"hostname": dev, "employee": None, "department": None}),
             "name": n, "seconds": int(s or 0), "events": c, "first": f, "last": l}
            for dev, n, s, c, f, l in rows]


def events(db: Session, tid: str, start, end, device_id=None, kind: str | None = None,
           q: str | None = None, limit: int = 200, offset: int = 0):
    names = device_names(db, tid)
    qry = _base(db, tid, start, end, device_id)
    if kind == "app":
        qry = qry.filter(ActivityEvent.event_type.in_(APP_TYPES))
    elif kind == "web":
        qry = qry.filter(ActivityEvent.event_type.in_(WEB_TYPES))
    elif kind == "idle":
        qry = qry.filter(ActivityEvent.event_type == "idle")
    if q:
        like = f"%{q.strip()}%"
        qry = qry.filter(or_(ActivityEvent.application.ilike(like), ActivityEvent.domain.ilike(like),
                             ActivityEvent.title.ilike(like)))
    total = qry.count()
    rows = qry.order_by(ActivityEvent.ts.desc()).offset(offset).limit(limit).all()
    out = []
    for e in rows:
        n = names.get(e.device_id, {"hostname": e.device_id, "employee": None})
        secs = e.duration_seconds or (60 if e.event_type == "active_window" else 0)
        out.append({"ts": e.ts, "device_id": e.device_id, "hostname": n["hostname"], "employee": n.get("employee"),
                    "type": "web" if e.event_type in WEB_TYPES else ("idle" if e.event_type == "idle" else "app"),
                    "app": e.application, "domain": e.domain, "title": e.title, "seconds": secs,
                    "url": (e.meta or {}).get("url")})
    return total, out
