"""Update distribution chain (PRD §30): GitHub -> License Server -> Client Servers -> Agents.

Roles (auto-detected from config):
  * License server  = EMP_LICENSE_SERVER is blank. It is a git checkout on systemd and
    updates itself from GitHub ("Update from GitHub & restart"). It also publishes the
    latest server/agent binaries that client servers and agents pull.
  * Client server   = EMP_LICENSE_SERVER is set. It checks the license server for a newer
    build and self-applies (download + swap exe + restart).

All actions are Platform-Super-Admin only, audited, SHA-256 verified, and can be disabled
with EMP_ALLOW_SELF_UPDATE=false.
"""
from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from .. import __version__, audit, get_build
from ..config import BASE_DIR, DATA_DIR, settings
from ..database import get_db
from ..deps import client_ip, get_current_user, platform_admin, require_roles
from ..models import AdminUser, Role

router = APIRouter(prefix="/api", tags=["system"])

# The client admin (company owner) may view/apply updates on their own server.
_UPD = require_roles(Role.CUSTOMER_OWNER)   # platform super admin passes implicitly

REPO_DIR = BASE_DIR.parent
IS_WIN = platform.system() == "Windows"
SERVER_EXE = BASE_DIR / "server_dist" / "ManagementServer.exe"
AGENT_EXE = BASE_DIR / "agent_dist" / "VoyagerAgent.exe"
BUNDLE = BASE_DIR / "server_dist" / "EndpointManagementServer-Universal-Windows.zip"
STAGE_DIR = DATA_DIR / "updates"


def _is_license_server() -> bool:
    return not settings.license_server


def _service_name() -> str:
    return settings.service_name or ("EndpointMgmtServer" if IS_WIN else "vmgmt")


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 300) -> dict:
    try:
        p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                           text=True, timeout=timeout)
        return {"cmd": " ".join(cmd), "code": p.returncode, "out": (p.stdout or "") + (p.stderr or "")}
    except Exception as e:
        return {"cmd": " ".join(cmd), "code": -1, "out": f"{type(e).__name__}: {e}"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _ver_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in str(v).split(".")[:3])
    except Exception:
        return (0,)


# --------------------------------------------------------------- shared info
def _build_number() -> int | None:
    r = _run(["git", "-C", str(REPO_DIR), "rev-list", "--count", "HEAD"], timeout=15)
    if r["code"] == 0 and r["out"].strip().isdigit():
        return int(r["out"].strip())
    return None


@router.get("/system/info")
def system_info(db: Session = Depends(get_db), admin: AdminUser = Depends(_UPD)):
    from ..services import settings_service as ss
    binfo = ss.get_setting(db, "build", None) or {}
    bid = get_build()      # works on non-git clients (baked app/BUILD) and dev (git)
    version_display = f"{__version__}+{bid}" if bid else __version__
    return {
        "version": __version__,
        "version_display": version_display,
        "build": bid,
        "updated_at": binfo.get("updated_at"),
        "previous_version": binfo.get("previous"),
        "role": "license_server" if _is_license_server() else "client_server",
        "platform": platform.platform(),
        "is_git_repo": (REPO_DIR / ".git").exists(),
        "commit": bid,
        "license_server": settings.license_server or None,
        "service_name": _service_name(),
        "self_update_enabled": settings.allow_self_update,
        "has_server_exe": SERVER_EXE.exists(),
        "has_agent_exe": AGENT_EXE.exists(),
    }


def _get_or_build_bundle() -> Path | None:
    target = BUNDLE if BUNDLE.parent.exists() else (REPO_DIR / "EndpointManagementServer-Universal-Windows.zip")
    # Rebuild whenever the code version changed (any file, not just the few checked below);
    # the commit the bundle was built from is stored next to it.
    stamp = target.with_name(target.name + ".build")
    current = get_build() or ""
    needs_build = not target.exists() or not stamp.exists() or \
        stamp.read_text(encoding="utf-8").strip() != current
    if target.exists() and not needs_build:
        try:
            scaffold = REPO_DIR / "installers" / "win2012_scaffold"
            builder = REPO_DIR / "installers" / "build_universal_bundle.py"
            t_mtime = target.stat().st_mtime
            check_paths = [
                BASE_DIR / "app" / "main.py",
                builder,
                scaffold / "SETUP_AND_RUN.bat",
                scaffold / "START.bat",
                scaffold / "requirements-win38.txt",
                scaffold / "doctor.py",
            ]
            for p in check_paths:
                if p.exists() and p.stat().st_mtime > t_mtime:
                    needs_build = True
                    break
        except Exception:
            pass

    if needs_build:
        try:
            builder = REPO_DIR / "installers" / "build_universal_bundle.py"
            if builder.exists():
                r = _run([sys.executable, str(builder)], cwd=REPO_DIR, timeout=120)
                root_zip = REPO_DIR / "EndpointManagementServer-Universal-Windows.zip"
                if root_zip.exists() and target != root_zip:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(root_zip, target)
                if r.get("code") == 0 and target.exists():
                    stamp.write_text(current, encoding="utf-8")
        except Exception:
            pass
    if target.exists():
        return target
    alt = REPO_DIR / "EndpointManagementServer-Universal-Windows.zip"
    return alt if alt.exists() else None


# --------------------------------------------------------------- LICENSE SERVER: publish
@router.get("/updates/latest")
def updates_latest():
    """Public manifest of the latest server/agent builds this license server distributes."""
    man = {}
    vfile = SERVER_EXE.parent / "version.json"
    versions = {}
    if vfile.exists():
        try:
            versions = json.loads(vfile.read_text(encoding="utf-8"))
        except Exception:
            versions = {}
    base = settings.server_public_url.rstrip("/")
    if SERVER_EXE.exists():
        man["server"] = {"version": versions.get("server", __version__),
                         "url": f"{base}/api/updates/download/server",
                         "sha256": _sha256(SERVER_EXE), "size": SERVER_EXE.stat().st_size}
    if AGENT_EXE.exists():
        man["agent"] = {"version": versions.get("agent", __version__),
                        "url": f"{base}/api/updates/download/agent",
                        "sha256": _sha256(AGENT_EXE), "size": AGENT_EXE.stat().st_size}
    bundle_path = _get_or_build_bundle()
    if bundle_path and bundle_path.exists():
        man["bundle"] = {"version": versions.get("server", __version__),
                         "url": f"{base}/api/updates/download/bundle",
                         "sha256": _sha256(bundle_path), "size": bundle_path.stat().st_size}
    return {"code_version": __version__, "components": man}


@router.get("/updates/download/{component}")
def updates_download(component: str):
    """Serve a staged binary to client servers / agents."""
    path = {"server": SERVER_EXE, "agent": AGENT_EXE}.get(component)
    if component == "bundle":
        path = _get_or_build_bundle()
    if not path or not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No staged {component} build")
    return Response(content=path.read_bytes(), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{path.name}"'})


@router.get("/updates/agent-info")
def updates_agent_info(device_id: str | None = None, db: Session = Depends(get_db)):
    """Staged VoyagerAgent.exe: version + checksum (client servers compare it with their cached
    copy). With ?device_id= (asked by the PC's updater) also whether this computer is approved
    to update now. Public: it only reveals the version and an approve yes/no."""
    from ..models import Device
    from ..services import agent_update as au
    if settings.license_server:
        au.sync_from_license_server()                   # client server: pick up newer cloud builds
    info = au.exe_info(AGENT_EXE)
    out = {"available": True, **info} if info else {"available": False}
    if device_id:
        dev = db.get(Device, device_id)
        out["approved"] = bool(info) and au.approved(db, dev, info["version"])
        if dev:                                         # machine policies the SYSTEM task enforces
            from ..services import tracking as trk
            s = trk.normalize_settings(trk.effective_profile(db, dev).settings)
            out["policies"] = {"block_private_browsing": s["block_private_browsing"]}
            db.commit()
    return out


@router.post("/system/agent-exe")
async def upload_agent_exe(request: Request, file: UploadFile = File(...),
                           db: Session = Depends(get_db), admin: AdminUser = Depends(platform_admin)):
    """Upload the standalone VoyagerAgent.exe (built on Windows with PyInstaller). On the license
    server every client server then pulls it automatically; on a client server it is used for
    agent downloads directly."""
    AGENT_EXE.parent.mkdir(parents=True, exist_ok=True)
    tmp = AGENT_EXE.with_suffix(".upload")
    size = 0
    head = b""
    with open(tmp, "wb") as out:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            if not head:
                head = chunk[:2]
            size += len(chunk)
            out.write(chunk)
    if head != b"MZ" or size < 1_000_000:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "That is not VoyagerAgent.exe (expected a Windows .exe of several MB).")
    tmp.replace(AGENT_EXE)
    audit.record(db, action="agent_exe_upload", actor_id=admin.id, actor_email=admin.email,
                 new_value={"size": size, "sha256": _sha256(AGENT_EXE), "filename": file.filename},
                 source_ip=client_ip(request))
    db.commit()
    from ..services import agent_update as au
    info = au.exe_info(AGENT_EXE) or {}
    return {"ok": True, "size": size, "sha256": _sha256(AGENT_EXE), "version": info.get("version"),
            "warning": None if info.get("version") else
            "No version stamp found in this build - computers cannot auto-update to it. "
            "Build the agent with installers/build.py voyageragent."}


# --------------------------------------------------------------- LICENSE SERVER: self-update
@router.post("/system/update")
def pull_and_restart(body: dict | None = None, request: Request = None,
                     db: Session = Depends(get_db), admin: AdminUser = Depends(platform_admin)):
    """License server: git pull (+optional pip) then restart the service (PRD §30)."""
    if not settings.allow_self_update:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Self-update is disabled on this server")
    if not (REPO_DIR / ".git").exists():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Not a git checkout — update this server via its installer instead")
    body = body or {}
    steps = [_run(["git", "-C", str(REPO_DIR), "pull", "--ff-only"], timeout=120)]
    if body.get("pip_install", True):
        steps.append(_run([sys.executable, "-m", "pip", "install", "-r",
                           str(BASE_DIR / "requirements.txt")], timeout=600))
    pull_ok = steps[0]["code"] == 0
    scheduled = _schedule_service_restart() if (body.get("restart", True) and pull_ok) else False
    audit.record(db, action="system_update", actor_id=admin.id, actor_email=admin.email,
                 new_value={"pull_code": steps[0]["code"], "restart": scheduled},
                 source_ip=client_ip(request) if request else None)
    db.commit()
    return {"ok": pull_ok, "steps": steps, "restarting": scheduled,
            "message": ("Updated from GitHub. Service restarting — reload in ~15s."
                        if scheduled else "Pull finished; restart not scheduled (see steps).")}


# --------------------------------------------------------------- CLIENT SERVER: check & apply
@router.get("/system/check-update")
def check_update(admin: AdminUser = Depends(_UPD)):
    """Client server: ask the license server (http://vmgmt.voyager.co.in:8084) whether a newer build exists."""
    server = settings.resolve_license_server()
    if _is_license_server() and not settings.license_server:
        return {"role": "license_server", "current": __version__, "update_available": False,
                "message": "This is the central license server; update it from GitHub.",
                "license_server": server}
    try:
        req = urllib.request.Request(
            f"{server}/api/updates/latest",
            headers={"User-Agent": f"VoyagerClientServer/{__version__}"}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            man = json.loads(r.read().decode())
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not reach license server ({server}): {e}")
    latest = (man.get("components", {}).get("server") or {}).get("version") or man.get("code_version") or man.get("version")
    available = bool(latest and _ver_tuple(latest) > _ver_tuple(__version__))
    return {"role": "client_server", "current": __version__, "latest": latest or __version__,
            "update_available": available, "license_server": server, "manifest": man}


@router.post("/system/apply-update")
def apply_update(request: Request = None, db: Session = Depends(get_db),
                 admin: AdminUser = Depends(_UPD)):
    """Client server self-update from the license server (http://vmgmt.voyager.co.in:8084)."""
    if not settings.allow_self_update:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Self-update is disabled on this server")
    if _is_license_server() and not settings.license_server:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This is the central license server")
    server = settings.resolve_license_server()
    try:
        req = urllib.request.Request(
            f"{server}/api/updates/latest",
            headers={"User-Agent": f"VoyagerClientServer/{__version__}"}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            man = json.loads(r.read().decode())
        comps = man.get("components", {})
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not fetch update from {server}: {e}")

    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    frozen = getattr(sys, "frozen", False)

    if frozen:
        comp = comps.get("server") or {"url": f"{server}/api/download/server", "version": man.get("code_version", "latest")}
        newexe = STAGE_DIR / "ManagementServer.new.exe"
        try:
            urllib.request.urlretrieve(comp["url"], newexe)
        except Exception as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Download failed from {server}: {e}")
        if comp.get("sha256") and _sha256(newexe) != comp["sha256"]:
            newexe.unlink(missing_ok=True)
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Checksum mismatch — update aborted")
        scheduled = _schedule_exe_swap(newexe)
    else:
        comp = comps.get("bundle") or {"url": f"{server}/api/download/server-bundle", "version": man.get("code_version", "latest")}
        scheduled = _apply_source_bundle(comp)

    audit.record(db, action="system_update", actor_id=admin.id, actor_email=admin.email,
                 new_value={"from": __version__, "to": comp.get("version"), "license_server": server,
                            "mode": "exe" if frozen else "source", "applied": scheduled},
                 source_ip=client_ip(request) if request else None)
    db.commit()
    return {"ok": scheduled, "to_version": comp.get("version"), "license_server": server,
            "message": (f"Update downloaded from {server} and verified. Server is applying update & restarting "
                        "— reload in ~20s.") if scheduled else "Could not schedule the update."}


def _apply_source_bundle(comp: dict) -> bool:
    """Download the source bundle, replace server/app + entry files, then restart."""
    import io
    import shutil
    import zipfile
    try:
        with urllib.request.urlopen(comp["url"], timeout=120) as r:
            data = r.read()
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Bundle download failed: {e}")
    if comp.get("sha256") and hashlib.sha256(data).hexdigest() != comp["sha256"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Checksum mismatch — update aborted")
    tmp = STAGE_DIR / "bundle"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Bundle extract failed: {e}")
    src_server = tmp / "server"
    if not (src_server / "app").exists():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bundle missing server/app")
    # replace app/ and entry files; never touch data/ or .env
    shutil.copytree(src_server / "app", BASE_DIR / "app", dirs_exist_ok=True)
    for f in ("run_server.py", "server_service.py", "requirements.txt", "doctor.py"):
        if (src_server / f).exists():
            shutil.copy2(src_server / f, BASE_DIR / f)
    # copy updated .bat helpers next to the install (START.bat/UPDATE.bat/etc.)
    for bat in tmp.glob("*.bat"):
        try:
            shutil.copy2(bat, BASE_DIR.parent / bat.name)
        except Exception:
            pass
    return _schedule_service_restart()


# --------------------------------------------------------------- restart / swap helpers
def _schedule_service_restart() -> bool:
    svc = _service_name()
    try:
        if IS_WIN:
            cmd = f'ping -n 4 127.0.0.1 >NUL & net stop "{svc}" & net start "{svc}" & ' \
                  f'sc query "{svc}" | find "RUNNING" >NUL || start "" "{sys.executable}" run_server.py --no-browser'
            subprocess.Popen(["cmd", "/c", cmd], cwd=str(BASE_DIR),
                             creationflags=0x00000008 | 0x00000200, close_fds=True)
        else:
            # Try systemd; if there is no such service (nohup/manual run), kill + relaunch.
            py = sys.executable
            srv = str(BASE_DIR)
            script = (
                f"sleep 2; "
                f"if systemctl restart {svc} 2>/dev/null; then exit 0; fi; "
                f"pkill -f 'run_server.py' 2>/dev/null; sleep 2; "
                f"cd '{srv}' && nohup '{py}' run_server.py --no-browser >> server.out 2>&1 &"
            )
            subprocess.Popen(["/bin/sh", "-c", script], start_new_session=True, close_fds=True)
        return True
    except Exception:
        return False


def _schedule_exe_swap(newexe: Path) -> bool:
    """Write an updater that waits for this exe to exit, replaces it, and restarts it."""
    if not IS_WIN:
        return False
    try:
        cur = Path(sys.executable).resolve()
        svc = _service_name()
        bat = STAGE_DIR / "apply_update.bat"
        bat.write_text(
            "@echo off\r\n"
            "timeout /t 3 /nobreak >NUL\r\n"
            f'net stop "{svc}" >NUL 2>&1\r\n'
            f'taskkill /f /im "{cur.name}" >NUL 2>&1\r\n'
            "timeout /t 2 /nobreak >NUL\r\n"
            f'move /y "{newexe}" "{cur}" >NUL\r\n'
            f'net start "{svc}" >NUL 2>&1 || start "" "{cur}"\r\n',
            encoding="utf-8")
        subprocess.Popen(["cmd", "/c", str(bat)],
                         creationflags=0x00000008 | 0x00000200, close_fds=True)
        return True
    except Exception:
        return False
