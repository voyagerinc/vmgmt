"""Build the server and agent executables with PyInstaller (PRD §7, Appendix A).

Produces onedir bundles under installers/dist/ which the Inno Setup scripts
(server.iss / agent.iss) then wrap into the double-click Server_Setup.exe / Agent_Setup.exe.

Usage (from a Windows build machine with the venvs created):
    python installers/build.py server
    python installers/build.py agent
    python installers/build.py voyageragent   # single-file VoyagerAgent.exe -> server/agent_dist/
    python installers/build.py all

VoyagerAgent.exe is what client servers hand out: the server appends each company's config to
it at download time, so one file installs + auto-configures the agent. Build it with the agent
venv:  agent\.venv\Scripts\python installersuild.py voyageragent
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = Path(__file__).resolve().parent / "dist"
WORK = Path(__file__).resolve().parent / "build"


def _run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    subprocess.check_call(cmd)


def build_server() -> None:
    server = ROOT / "server"
    _run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--name", "ManagementServer",
        "--distpath", str(DIST), "--workpath", str(WORK), "--specpath", str(WORK),
        "--add-data", f"{server / 'app' / 'static'}{__import__('os').pathsep}app/static",
        "--hidden-import", "uvicorn.logging", "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols.http.auto", "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan.on", "--hidden-import", "passlib.handlers.argon2",
        "--collect-submodules", "app",
        str(server / "run_server.py"),
    ])
    print("Server bundle ->", DIST / "ManagementServer")


def build_agent() -> None:
    agent = ROOT / "agent"
    # windowed enrollment wizard
    _run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", "AgentEnroll",
        "--distpath", str(DIST), "--workpath", str(WORK), "--specpath", str(WORK),
        "--hidden-import", "mss", "--hidden-import", "PIL",
        str(agent / "enroll_wizard.py"),
    ])
    # background service/CLI
    _run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--name", "AgentService",
        "--distpath", str(DIST), "--workpath", str(WORK), "--specpath", str(WORK),
        "--hidden-import", "win32timezone",
        str(agent / "agent_service.py"),
    ])
    print("Agent bundles ->", DIST / "AgentEnroll", DIST / "AgentService")


def build_voyager_agent() -> None:
    """Single-file, windowed (no console) agent: everything bundled, no Python on the PC."""
    agent = ROOT / "agent"
    out = ROOT / "server" / "agent_dist"
    _run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "VoyagerAgent",
        "--distpath", str(out), "--workpath", str(WORK / "voyageragent"), "--specpath", str(WORK),
        "--paths", str(agent),
        "--hidden-import", "mss", "--collect-submodules", "mss",
        "--hidden-import", "PIL", "--hidden-import", "PIL.Image", "--hidden-import", "psutil",
        "--hidden-import", "win32gui", "--hidden-import", "win32process",
        # trackers (logins / network / USB / email)
        "--hidden-import", "trackers", "--hidden-import", "win32evtlog", "--hidden-import", "win32security",
        "--hidden-import", "win32file", "--hidden-import", "win32event", "--hidden-import", "win32con",
        "--hidden-import", "pywintypes", "--hidden-import", "pythoncom", "--hidden-import", "win32com.client",
        str(agent / "agent.py"),
    ])
    import json
    import re
    ver = re.search(r'__version__ = "([^"]+)"', (agent / "agent.py").read_text(encoding="utf-8")).group(1)
    (out / "version.json").write_text(json.dumps({"agent": ver}), encoding="utf-8")
    print("VoyagerAgent.exe ->", out / "VoyagerAgent.exe", f"(agent {ver})")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    DIST.mkdir(parents=True, exist_ok=True)
    if target in ("server", "all"):
        build_server()
    if target in ("agent", "all"):
        build_agent()
    if target in ("voyageragent", "all"):
        build_voyager_agent()
