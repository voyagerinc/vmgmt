"""System maintenance — admin-triggered `git pull` + service restart (platform admin only).

Powerful by design, so it is restricted to the Platform Super Admin, audited, and can be
disabled entirely with EMP_ALLOW_SELF_UPDATE=false. The restart is scheduled in a detached
process so it survives this request's worker being replaced.
"""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import __version__, audit
from ..config import BASE_DIR, settings
from ..database import get_db
from ..deps import client_ip, platform_admin
from ..models import AdminUser

router = APIRouter(prefix="/api/system", tags=["system"])

REPO_DIR = BASE_DIR.parent          # the git repo root (.../vmgmt)
IS_WIN = platform.system() == "Windows"


def _service_name() -> str:
    if settings.service_name:
        return settings.service_name
    return "EndpointMgmtServer" if IS_WIN else "vmgmt"


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 120) -> dict:
    try:
        p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                           text=True, timeout=timeout)
        return {"cmd": " ".join(cmd), "code": p.returncode,
                "out": (p.stdout or "") + (p.stderr or "")}
    except Exception as e:
        return {"cmd": " ".join(cmd), "code": -1, "out": f"{type(e).__name__}: {e}"}


@router.get("/info")
def system_info(admin: AdminUser = Depends(platform_admin)):
    git = _run(["git", "-C", str(REPO_DIR), "rev-parse", "--short", "HEAD"], timeout=15)
    branch = _run(["git", "-C", str(REPO_DIR), "rev-parse", "--abbrev-ref", "HEAD"], timeout=15)
    is_repo = (REPO_DIR / ".git").exists()
    return {
        "version": __version__,
        "platform": platform.platform(),
        "is_git_repo": is_repo,
        "commit": git["out"].strip() if git["code"] == 0 else None,
        "branch": branch["out"].strip() if branch["code"] == 0 else None,
        "service_name": _service_name(),
        "self_update_enabled": settings.allow_self_update,
    }


@router.post("/update")
def pull_and_restart(body: dict | None = None, request: Request = None,
                     db: Session = Depends(get_db), admin: AdminUser = Depends(platform_admin)):
    """git pull (+ optional pip install) then restart the service in a detached process."""
    if not settings.allow_self_update:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Self-update is disabled on this server")
    if not (REPO_DIR / ".git").exists():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"{REPO_DIR} is not a git checkout — update via your normal deploy process")
    body = body or {}
    steps = [_run(["git", "-C", str(REPO_DIR), "pull", "--ff-only"], timeout=120)]
    if body.get("pip_install"):
        steps.append(_run([sys.executable, "-m", "pip", "install", "-r",
                           str(BASE_DIR / "requirements.txt")], timeout=600))

    restart = bool(body.get("restart", True))
    pull_ok = steps[0]["code"] == 0
    scheduled = False
    if restart and pull_ok:
        scheduled = _schedule_restart()

    audit.record(db, action="system_update", actor_id=admin.id, actor_email=admin.email,
                 new_value={"pull_code": steps[0]["code"], "restart": scheduled},
                 source_ip=client_ip(request) if request else None)
    db.commit()
    return {
        "ok": pull_ok,
        "steps": steps,
        "restarting": scheduled,
        "message": ("Updated. The service is restarting — reload the page in ~15 seconds."
                    if scheduled else
                    "Pull finished. Restart was not scheduled (see steps)."),
    }


def _schedule_restart() -> bool:
    """Spawn a detached child that restarts the service shortly after we respond."""
    svc = _service_name()
    try:
        if IS_WIN:
            # wait ~3s, then stop+start the Windows service, detached from this process
            cmd = f'ping -n 4 127.0.0.1 >NUL & net stop "{svc}" & net start "{svc}"'
            DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(["cmd", "/c", cmd], creationflags=DETACHED, close_fds=True)
        else:
            cmd = f"sleep 2; systemctl restart {svc}"
            subprocess.Popen(["/bin/sh", "-c", cmd], start_new_session=True, close_fds=True)
        return True
    except Exception:
        return False
