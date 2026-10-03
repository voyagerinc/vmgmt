"""Launch the Management Server (double-click friendly). PRD §7.1."""
from __future__ import annotations

import sys
import webbrowser
from threading import Timer

import uvicorn

from app.config import settings


def _open_browser() -> None:
    try:
        webbrowser.open(f"http://{settings.host}:{settings.port}/")
    except Exception:
        pass


if __name__ == "__main__":
    # Delegate service management (install/start/stop/remove) to the service wrapper so a
    # single packaged exe serves both roles: `ManagementServer.exe service <cmd>`.
    if len(sys.argv) > 1 and sys.argv[1] == "service":
        import server_service
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        raise SystemExit(server_service.main())

    no_browser = "--no-browser" in sys.argv
    if not no_browser:
        Timer(1.5, _open_browser).start()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port,
                reload="--reload" in sys.argv, log_level="info")
