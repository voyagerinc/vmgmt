"""Build the server and agent executables with PyInstaller (PRD §7, Appendix A).

Produces onedir bundles under installers/dist/ which the Inno Setup scripts
(server.iss / agent.iss) then wrap into the double-click Server_Setup.exe / Agent_Setup.exe.

Usage (from a Windows build machine with the venvs created):
    python installers/build.py server
    python installers/build.py agent
    python installers/build.py all
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = Path(__file__).resolve().parent / "dist"
WORK = Path(__file__).resolve().parent / "build"


def _run(cmd: list[str]) -> None:
    print("＄", " ".join(cmd))
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


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    DIST.mkdir(parents=True, exist_ok=True)
    if target in ("server", "all"):
        build_server()
    if target in ("agent", "all"):
        build_agent()
