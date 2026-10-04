"""FastAPI application entrypoint — wires routers, scheduler, console (PRD §5, §26, §30, §31)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import LOG_DIR, settings
from .database import SessionLocal, init_db
from .bootstrap import ensure_bootstrap
from .routers import (
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
    users,
)
from .services import alert_engine, retention, scheduled_reports

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
    finally:
        db.close()
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_maintenance_tick, "interval", seconds=max(60, settings.offline_after_seconds // 2),
                       id="offline_check")
    _scheduler.add_job(_retention_tick, "interval", hours=6, id="retention")
    _scheduler.add_job(lambda: _report_tick("weekly"), "cron", day_of_week="mon", hour=7,
                       id="weekly_report")
    _scheduler.add_job(lambda: _report_tick("monthly"), "cron", day=1, hour=7, id="monthly_report")
    _scheduler.start()
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
          evidence, reports, settings_router, downloads):
    app.include_router(r.router)


@app.get("/api/health", tags=["system"])
def health():
    return {"status": "ok", "version": __version__, "deployment": settings.deployment_model}


@app.get("/api/meta", tags=["system"])
def meta():
    return {"app_name": settings.app_name, "version": __version__,
            "heartbeat_interval": settings.heartbeat_interval_seconds,
            "server_url": settings.server_public_url,
            "license_server": settings.license_server}


# ---- static admin console (SPA) ----
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        candidate = STATIC_DIR / path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
