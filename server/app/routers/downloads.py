"""Software downloads (PRD §7): server installer bundle + per-tenant pre-configured agent.

Flow the tenant follows:
  1. Download & install the SERVER software (this bundle) — or use the one already running.
  2. Download the AGENT package — it embeds this server's URL + the tenant's license + a fresh
     enrollment token (agent_config.json), so it self-enrolls with no typing.
  3. Install the agent on each PC; data appears on the tenant's dashboard.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from .. import audit
from ..config import BASE_DIR, settings
from ..database import get_db
from ..deps import get_current_user, require_roles, resolve_tenant
from ..models import AdminUser, EnrollmentToken, Role, Tenant
from ..services import license_service as lic_svc

router = APIRouter(prefix="/api/download", tags=["downloads"])

PROJECT_ROOT = BASE_DIR.parent              # .../Emp Monitoring
AGENT_DIR = PROJECT_ROOT / "agent"
AGENT_EXE = BASE_DIR / "agent_dist" / "VoyagerAgent.exe"       # prebuilt standalone agent
SERVER_EXE = BASE_DIR / "server_dist" / "ManagementServer.exe"  # prebuilt standalone server
SERVER_SETUP = BASE_DIR / "server_dist" / "Server_Setup.exe"    # double-click wizard installer
UPDATE_EXE = BASE_DIR / "server_dist" / "update.exe"            # standalone client updater
SERVER_BUNDLE = BASE_DIR / "server_dist" / "EndpointManagementServer-Universal-Windows.zip"
_EXCLUDE_DIRS = {".venv", "venv", "__pycache__", "data", "logs", "build", "dist",
                 "agent_dist", "server_dist", "build_srv", "agent_package", ".git", "node_modules"}
_EXCLUDE_FILES = {".env", ".secret", ".evidence_key", "FIRST_RUN.txt"}


def _safe(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in " _-").strip() or "download"


def _sha256(path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch_agent_exe() -> bool:
    """Client server: keep agent_dist/VoyagerAgent.exe identical to the license server's copy.
    The Universal zip ships without it, and a newer agent uploaded to the license server must
    reach every client — so compare checksums and download only when it differs. If the license
    server is unreachable the cached copy is used."""
    if not settings.license_server:
        return AGENT_EXE.exists()
    import json as _json
    import urllib.request
    srv = settings.resolve_license_server()
    try:
        with urllib.request.urlopen(f"{srv}/api/updates/agent-info", timeout=10) as resp:
            info = _json.loads(resp.read().decode())
    except Exception:
        info = None                                   # offline / older license server
    if AGENT_EXE.exists():
        if not info or not info.get("available") or info.get("sha256") == _sha256(AGENT_EXE):
            return True
    elif info is not None and not info.get("available"):
        return False
    tmp = AGENT_EXE.with_suffix(".part")
    try:
        AGENT_EXE.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(f"{srv}/api/updates/download/agent", timeout=180) as resp, \
                open(tmp, "wb") as out:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        bad = tmp.stat().st_size < 1_000_000 or (info and info.get("sha256")
                                                 and _sha256(tmp) != info["sha256"])
        if bad:                                       # error page / truncated download
            tmp.unlink(missing_ok=True)
            return AGENT_EXE.exists()
        tmp.replace(AGENT_EXE)
        return True
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return AGENT_EXE.exists()


@router.get("/agent")
def download_agent(tenant_id: str | None = Query(None), mode: str = Query("install", pattern="^(install|repair)$"),
                   db: Session = Depends(get_db),
                   user: AdminUser = Depends(require_roles(Role.IT_ADMIN, Role.CUSTOMER_OWNER))):
    """Build and stream a ready-to-run agent package for the tenant (requires active license)."""
    tid = resolve_tenant(user, tenant_id)
    tenant = db.get(Tenant, tid)
    lic = lic_svc.active_license(db, tid)
    if not lic_svc.is_usable(lic):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED,
                            "License is not active. Activate the license on this server first.")

    # Only the standalone .exe is offered (no Python needed on employee PCs). A client server
    # installed from the Universal zip fetches it once from the license server.
    if not _fetch_agent_exe():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "VoyagerAgent.exe is not available on this server yet. Platform Super Admin: "
                            "Settings -> Agent program -> upload VoyagerAgent.exe (on this server or on the "
                            "license server), then download the agent again.")

    # fresh long-lived enrollment token for this download
    tok = EnrollmentToken(tenant_id=tid, label="agent-download", max_uses=0,
                          expires_at=datetime.now(timezone.utc) + timedelta(days=365),
                          created_by=user.id)
    db.add(tok)
    db.flush()                      # materialize tok.token before embedding it in the config

    config = {
        "company": tenant.company_name, "tenant_id": tid,
        "server": settings.server_public_url.rstrip("/"),
        "license_id": lic.id, "enroll_token": tok.token,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **({"repair": True} if mode == "repair" else {}),   # Agent Repair utility: diagnose + fix on run
    }
    # One self-contained file: the standalone agent with this company's config appended
    # (the agent reads it from the end of its own .exe). Download -> double-click -> done.
    import base64
    trailer = (b"\r\nVOYAGER_AGENT_CONFIG:" + base64.b64encode(json.dumps(config).encode())
               + b":END_VOYAGER_AGENT_CONFIG\r\n")
    data = AGENT_EXE.read_bytes() + trailer
    audit.record(db, action="download_agent", tenant_id=tid, actor_id=user.id,
                 actor_email=user.email, target_type="tenant", target_id=tid,
                 new_value={"format": "exe-embedded-config"})
    db.commit()
    fname = f"VoyagerAgent{'_Repair' if mode == 'repair' else ''}_{_safe(tenant.company_name).replace(' ', '_')}.exe"
    return Response(content=data, media_type="application/vnd.microsoft.portable-executable",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/server-bundle")
def download_server_bundle(user: AdminUser = Depends(require_roles(Role.CUSTOMER_OWNER))):
    """Python-3.8 source bundle for Windows Server 2012 clients (SETUP_AND_RUN.bat / UPDATE.bat)."""
    from .system import _get_or_build_bundle
    target = _get_or_build_bundle()
    if not target or not target.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No Server 2012 bundle staged on this server")
    return Response(content=target.read_bytes(), media_type="application/zip",
                    headers={"Content-Disposition":
                             'attachment; filename="EndpointManagementServer-Universal-Windows.zip"'})


@router.get("/server")
def download_server(db: Session = Depends(get_db),
                    user: AdminUser = Depends(require_roles(Role.CUSTOMER_OWNER))):
    """Stream the server software. Prefers the standalone .exe (no Python needed); else source."""
    buf = io.BytesIO()

    # Prefer the double-click wizard installer if it's been built/staged.
    if SERVER_SETUP.exists():
        return Response(content=SERVER_SETUP.read_bytes(), media_type="application/octet-stream",
                        headers={"Content-Disposition": 'attachment; filename="Server_Setup.exe"'})

    if SERVER_EXE.exists():
        # Standalone Windows server: Python + all dependencies bundled. Double-click to run.
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(SERVER_EXE, "VoyagerServer/ManagementServer.exe")
            if UPDATE_EXE.exists():
                z.write(UPDATE_EXE, "VoyagerServer/update.exe")
            z.writestr("VoyagerServer/.env",
                       "EMP_HOST=0.0.0.0\nEMP_PORT=9084\n"
                       "EMP_SERVER_PUBLIC_URL=http://CHANGE-TO-THIS-SERVER-IP-OR-DOMAIN:9084\n"
                       "EMP_DEPLOYMENT_MODEL=on_premise\n")
            z.writestr("VoyagerServer/INSTALL.txt",
                       "VOYAGER ENDPOINT MANAGEMENT SERVER (standalone .exe)\n"
                       "===================================================\n\n"
                       "No Python or other software is required - everything is bundled in the .exe.\n\n"
                       "SETUP:\n"
                       "  1. Copy this 'VoyagerServer' folder to the server machine.\n"
                       "  2. Edit .env and set EMP_SERVER_PUBLIC_URL to this server's IP or domain\n"
                       "     (this is the address agents will connect to), e.g.\n"
                       "        EMP_SERVER_PUBLIC_URL=http://192.168.1.50:9084\n"
                       "  3. Double-click ManagementServer.exe  (allow it through Windows SmartScreen).\n"
                       "     The admin console opens at http://localhost:9084/ and FIRST_RUN.txt is\n"
                       "     written next to the exe with the first Super Admin login.\n"
                       "  4. Allow port 9084 in Windows Firewall so agents/other PCs can reach it.\n\n"
                       "RUN AS A WINDOWS SERVICE (stays up after logout, starts on boot):\n"
                       "  Open an Administrator Command Prompt in this folder and run:\n"
                       "     ManagementServer.exe service install\n"
                       "     ManagementServer.exe service start\n\n"
                       "DATA: the database, keys and evidence are stored in the 'data' folder next to\n"
                       "the exe. Back that folder up. Do not delete data\\.secret or data\\.evidence_key.\n")
        audit.record(db, action="download_server", tenant_id=user.tenant_id, actor_id=user.id,
                     actor_email=user.email, new_value={"format": "exe"})
        db.commit()
        return Response(content=buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="VoyagerServer.zip"'})

    # ---- fallback: source bundle (requires Python) ----
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for base in (BASE_DIR, AGENT_DIR):
            for fp in base.rglob("*"):
                if fp.is_dir():
                    continue
                if any(part in _EXCLUDE_DIRS for part in fp.parts):
                    continue
                if fp.name in _EXCLUDE_FILES or fp.suffix in (".pyc", ".db", ".db-wal", ".db-shm"):
                    continue
                arc = fp.relative_to(PROJECT_ROOT)
                try:
                    z.write(fp, str(arc))
                except Exception:
                    continue
        for extra in ("README.md", "generate_agent.py"):
            fp = PROJECT_ROOT / extra
            if fp.exists():
                z.write(fp, extra)
        z.writestr("INSTALL.txt",
                   "ENDPOINT MANAGEMENT SERVER - install\n"
                   "====================================\n\n"
                   "1. Install Python 3.11+ on the server machine.\n"
                   "2. Open a terminal in the 'server' folder and run:\n"
                   "     python -m venv .venv\n"
                   "     .venv\\Scripts\\activate   (Windows)\n"
                   "     pip install -r requirements.txt\n"
                   "     python run_server.py\n"
                   "3. The admin console opens at http://<this-server>:9084/\n"
                   "4. Sign in with the company admin credentials issued to you, then activate\n"
                   "   your license (License ID + License Key) on the activation screen.\n"
                   "5. Go to Downloads and get the pre-configured Agent package for your PCs.\n\n"
                   "For a double-click Windows installer, see installers/README.md.\n")
    audit.record(db, action="download_server", tenant_id=user.tenant_id, actor_id=user.id,
                 actor_email=user.email)
    db.commit()
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="EndpointManagementServer.zip"'})
