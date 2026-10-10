"""FastAPI application entrypoint — wires routers, scheduler, console (PRD §5, §26, §30, §31)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from . import __version__
from .config import LOG_DIR, settings
from .database import SessionLocal, get_db, init_db
from .bootstrap import ensure_bootstrap
from .routers import (
    agent_updates,
    agents,
    alerts,
    auth,
    devices,
    directory,
    downloads,
    evidence,
    inventory,
    policies,
    portal,
    reports,
    settings as settings_router,
    system as system_router,
    tracking as tracking_router,
    users,
)
from .services import alert_engine, license_sync, retention, scheduled_reports

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.StreamHandler(),
              logging.FileHandler(LOG_DIR / "server.log", encoding="utf-8")],
)
log = logging.getLogger("management-server")

STATIC_DIR = Path(__file__).resolve().parent / "static"
_scheduler: BackgroundScheduler | None = None


def _maintenance_tick() -> None:
    from .services import live_view
    live_view.sweep()
    db = SessionLocal()
    try:
        offline = alert_engine.check_offline(db, settings.offline_after_seconds)
        db.commit()
        if offline:
            log.info("Marked %d device(s) offline", offline)
    except Exception:
        db.rollback()
        log.exception("offline check failed")
    finally:
        db.close()


def _retention_tick() -> None:
    db = SessionLocal()
    try:
        ev = retention.purge_expired_evidence(db)
        evt = retention.purge_old_events(db)
        db.commit()
        if ev or evt:
            log.info("Retention: purged %d evidence, %d events", ev, evt)
    except Exception:
        db.rollback()
        log.exception("retention job failed")
    finally:
        db.close()


def _record_build_version(db) -> None:
    """Stamp version + build id + when it changed, so the UI's 'Last updated' reflects any
    update — on git servers and on non-git clients (baked app/BUILD)."""
    from datetime import datetime, timezone
    from . import get_build
    from .services import settings_service as ss
    bid = get_build()
    try:
        cur = ss.get_setting(db, "build", None) or {}
        changed = cur.get("version") != __version__ or (bid and cur.get("commit") != bid)
        if changed:
            ss.set_setting(db, "build", None, {
                "version": __version__,
                "commit": bid,
                "previous": cur.get("version"),
                "previous_commit": cur.get("commit"),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            db.commit()
            log.info("Build changed -> %s (%s)", __version__, bid or "no-build-id")
    except Exception:
        db.rollback()


def _client_sync_tick() -> None:
    db = SessionLocal()
    try:
        r = license_sync.run_client_sync(db)
        if r.get("changed"):
            log.info("License sync applied: %s", r["changed"])
    except Exception:
        db.rollback()
        log.exception("license sync failed")
    finally:
        db.close()


def _report_tick(period: str) -> None:
    db = SessionLocal()
    try:
        n = scheduled_reports.run_periodic_report(db, period)
        db.commit()
        log.info("%s report generated for %d tenant(s)", period, n)
    except Exception:
        db.rollback()
        log.exception("%s report job failed", period)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    init_db()
    db = SessionLocal()
    try:
        ensure_bootstrap(db)
        _record_build_version(db)
    finally:
        db.close()
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_maintenance_tick, "interval", seconds=max(60, settings.offline_after_seconds // 2),
                       id="offline_check")
    _scheduler.add_job(_retention_tick, "interval", hours=6, id="retention")
    _scheduler.add_job(lambda: _report_tick("weekly"), "cron", day_of_week="mon", hour=7,
                       id="weekly_report")
    _scheduler.add_job(lambda: _report_tick("monthly"), "cron", day=1, hour=7, id="monthly_report")
    if license_sync.is_client_server():
        # every 10 min (first run ~now, not paused) so cloud resets apply without anyone logging in
        _scheduler.add_job(_client_sync_tick, "interval", minutes=10, id="license_sync")
    _scheduler.start()
    if license_sync.is_client_server():
        _client_sync_tick()      # sync on startup too, so a restart applies a pending reset
    log.info("Management Server %s started (deployment=%s)", __version__, settings.deployment_model)
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description="Employee Monitoring, IT Asset & Endpoint Management Platform — Management Server API.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")] if settings.cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


# API routers
for r in (auth, portal, users, agents, devices, directory, inventory, policies, alerts,
          evidence, reports, settings_router, downloads, system_router, tracking_router, agent_updates):
    app.include_router(r.router)


@app.get("/api/health", tags=["system"])
def health():
    return {"status": "ok", "version": __version__, "deployment": settings.deployment_model}


@app.get("/api/meta", tags=["system"])
def meta(db: Session = Depends(get_db)):
    from . import get_build
    from .services import settings_service as ss
    binfo = ss.get_setting(db, "build", None) or {}
    bid = get_build()
    v_display = f"v{__version__}" + (f" ({bid})" if bid else "")
    from datetime import datetime, timezone
    from .config import BASE_DIR
    mtime = None
    try:
        main_py = BASE_DIR / "app" / "main.py"
        if main_py.exists():
            mtime = datetime.fromtimestamp(main_py.stat().st_mtime, tz=timezone.utc).isoformat()
    except Exception:
        pass
    updated_at = binfo.get("updated_at") or mtime
    return {
        "app_name": settings.app_name,
        "version": __version__,
        "build": bid,
        "version_display": v_display,
        "updated_at": updated_at,
        "heartbeat_interval": settings.heartbeat_interval_seconds,
        "server_url": settings.server_public_url,
        "license_server": settings.license_server,
        "role": "license_server" if not settings.license_server else "client_server",
    }


# ---- static admin console (SPA) ----
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    def _index_page():
        """index.html with the build id as the asset version, so browsers always load the
        app.js/styles.css that belong to the running server (no stale cached console)."""
        import re as _re
        from fastapi.responses import HTMLResponse
        from . import get_build
        v = get_build() or __version__
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        html = _re.sub(r"(/assets/(?:app\.js|styles\.css))\?v=[^\"']*", rf"\1?v={v}", html)
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/", include_in_schema=False)
    def index():
        return _index_page()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path == "api" or path.startswith("api/"):          # unknown API route: a real 404, never the page
            return JSONResponse({"detail": f"Not found: /{path}"}, status_code=404)
        candidate = STATIC_DIR / path
        if candidate.is_file():
            return FileResponse(candidate)
        return _index_page()
