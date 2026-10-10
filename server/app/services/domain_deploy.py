"""Deploy / repair the agent across a Windows domain from the client panel (no per-PC visit).

Runs on a Windows client server with network line-of-sight to the target PCs and a domain
admin account. For each computer it copies VoyagerAgent.exe to the admin share
(\\host\\C$\\Windows\\Temp) and runs it via a one-time SYSTEM scheduled task created over RPC
(schtasks /s). The admin password is used only for the job and never stored.

Requirements on the network: File and Printer Sharing / admin shares (TCP 445), the Remote
Scheduled Tasks service (RPC, TCP 135 + dynamic), and the domain admin credentials. Everything
here is a no-op / clear error on a non-Windows server.
"""
from __future__ import annotations

import platform
import re
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

IS_WIN = platform.system() == "Windows"
AGENT_EXE = None            # set from config at call time
_REMOTE_DIR = r"C:\Windows\Temp"
_REMOTE_NAME = "VoyagerAgent_deploy.exe"
_TASK = "VoyagerAgentDeploy"


def _run(cmd: list[str], timeout: int = 120, inp: str | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=inp,
                           creationflags=0x08000000 if IS_WIN else 0)   # CREATE_NO_WINDOW
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 1, "timed out"
    except Exception as e:
        return 1, f"{type(e).__name__}: {e}"


def discover_domain_computers() -> list[str]:
    """Computer names in the domain (AD), newest logons first; empty if AD tools are unavailable."""
    if not IS_WIN:
        return []
    ps = ("try{Get-ADComputer -Filter {Enabled -eq $true} -Properties LastLogonDate | "
          "Sort-Object LastLogonDate -Descending | Select-Object -ExpandProperty Name}catch{"
          "([ADSISearcher]'(objectCategory=computer)').FindAll() | ForEach-Object "
          "{$_.Properties.name}}")
    code, out = _run(["powershell", "-NoProfile", "-Command", ps], timeout=60)
    names = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith(("At ", "+", "    "))]
    return names[:5000]


def _net_use(host: str, user: str, pw: str, connect: bool) -> tuple[int, str]:
    share = rf"\\{host}\C$"
    if connect:
        return _run(["net", "use", share, pw, f"/user:{user}"], timeout=40)
    return _run(["net", "use", share, "/delete", "/y"], timeout=20)


def _reachable(host: str) -> bool:
    code, _ = _run(["ping", "-n", "1", "-w", "1500", host], timeout=8)
    return code == 0


def deploy_to_host(host: str, user: str, pw: str, exe: Path, server_url: str, action: str) -> tuple[bool, str]:
    """Copy the agent to one PC and run it (install or repair) as SYSTEM via a remote task."""
    host = host.strip().lstrip("\\")
    if not host:
        return False, "empty host name"
    if not _reachable(host):
        return False, "computer did not answer ping (off, not on the network, or firewall)"
    connected = False
    try:
        code, out = _net_use(host, user, pw, True)
        if code != 0:
            m = re.search(r"System error (\d+)", out)
            hint = {"5": "access denied — the account is not a local admin on that PC",
                    "53": "cannot find the PC (name resolution / SMB blocked)",
                    "1326": "wrong username or password",
                    "64": "admin share \\C$ not available"}.get(m.group(1) if m else "", "")
            return False, f"could not connect to \\\\{host}\\C$ ({out.strip().splitlines()[-1] if out.strip() else code}){' — ' + hint if hint else ''}"
        connected = True
        dest = rf"\\{host}\C$\Windows\Temp\{_REMOTE_NAME}"
        code, out = _run(["cmd", "/c", "copy", "/y", str(exe), dest], timeout=180)
        if code != 0 or "copied" not in out.lower():
            return False, f"could not copy the agent to the PC: {out.strip()[:160]}"
        local_exe = rf"{_REMOTE_DIR}\{_REMOTE_NAME}"
        args = " --repair" if action == "repair" else ""
        # one-time SYSTEM task that runs the agent, then deletes itself
        _run(["schtasks", "/s", host, "/u", user, "/p", pw, "/delete", "/tn", _TASK, "/f"], timeout=40)
        code, out = _run(["schtasks", "/s", host, "/u", user, "/p", pw, "/create", "/tn", _TASK,
                          "/tr", f'"{local_exe}"{args}', "/sc", "once", "/st", "00:00", "/ru", "SYSTEM",
                          "/rl", "HIGHEST", "/f"], timeout=60)
        if code != 0:
            return False, f"could not create the remote task (Remote Scheduled Tasks / RPC blocked?): {out.strip()[:160]}"
        code, out = _run(["schtasks", "/s", host, "/u", user, "/p", pw, "/run", "/tn", _TASK], timeout=40)
        if code != 0:
            return False, f"created the task but could not start it: {out.strip()[:160]}"
        return True, f"agent {'repair' if action == 'repair' else 'install'} started on {host} (SYSTEM)"
    finally:
        if connected:
            _net_use(host, user, pw, False)


def run_job(db_factory, job_id: str, tenant_id: str, hosts: list[str], user: str, pw: str,
            exe: Path, server_url: str, action: str) -> None:
    """Background worker: deploy to each host, updating the DeployJob row as it goes."""
    from ..models import DeployJob
    for host in hosts:
        ok, detail = (False, "")
        try:
            ok, detail = deploy_to_host(host, user, pw, exe, server_url, action)
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        db = db_factory()
        try:
            job = db.get(DeployJob, job_id)
            if not job:
                return
            tg = list(job.targets or [])
            tg.append({"host": host, "status": "ok" if ok else "failed", "detail": detail})
            job.targets = tg
            job.succeeded = sum(1 for t in tg if t["status"] == "ok")
            job.failed = sum(1 for t in tg if t["status"] == "failed")
            db.commit()
        finally:
            db.close()
    db = db_factory()
    try:
        job = db.get(DeployJob, job_id)
        if job:
            job.status = "done"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
    try:
        pw = "\x00" * len(pw)        # best-effort scrub of the password copy
    except Exception:
        pass
