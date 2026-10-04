"""Build Server_Setup.exe (double-click wizard) and update.exe (client updater).

No Inno Setup required — these are PyInstaller executables. Build the server + agent exes
first (installers/build.py or the commands in server/README), then run:

    python installers/build_setup.py

Outputs to installers/dist/ and stages copies into server/server_dist/ so the console's
"Download server software" serves Server_Setup.exe and includes update.exe.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"
WORK = HERE / "build"
SERVER_EXE = ROOT / "server" / "server_dist" / "ManagementServer.exe"
STAGE = ROOT / "server" / "server_dist"


def _run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    subprocess.check_call(cmd)


def main() -> int:
    if not SERVER_EXE.exists():
        sys.exit(f"Build ManagementServer.exe first (missing {SERVER_EXE}).")

    # 1. update.exe
    _run([sys.executable, "-m", "PyInstaller", "--onefile", "--windowed", "--uac-admin",
          "--name", "update", "--distpath", str(DIST), "--workpath", str(WORK),
          "--specpath", str(WORK), str(HERE / "update.py")])

    # 2. Server_Setup.exe (bundles ManagementServer.exe + update.exe)
    sep = ";" if sys.platform == "win32" else ":"
    _run([sys.executable, "-m", "PyInstaller", "--onefile", "--windowed", "--uac-admin",
          "--name", "Server_Setup", "--distpath", str(DIST), "--workpath", str(WORK),
          "--specpath", str(WORK),
          "--add-data", f"{SERVER_EXE}{sep}.",
          "--add-data", f"{DIST / 'update.exe'}{sep}.",
          str(HERE / "server_setup.py")])

    # 3. stage for distribution
    STAGE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DIST / "update.exe", STAGE / "update.exe")
    shutil.copy2(DIST / "Server_Setup.exe", STAGE / "Server_Setup.exe")
    print("\nStaged into", STAGE)
    print("  Server_Setup.exe, update.exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
