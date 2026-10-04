"""Launch the Management Server (double-click friendly). PRD §7.1."""
from __future__ import annotations

import sys
import webbrowser
from threading import Timer

import uvicorn

from app.config import settings


FROZEN = getattr(sys, "frozen", False)


def _open_browser() -> None:
    host = "127.0.0.1" if settings.host in ("0.0.0.0", "::") else settings.host
    try:
        webbrowser.open(f"http://{host}:{settings.port}/")
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
        Timer(2.0, _open_browser).start()
    if FROZEN:
        # Packaged .exe: pass the app object (import-string reloading isn't available frozen).
        from app.main import app as application
        uvicorn.run(application, host=settings.host, port=settings.port, log_level="info")
    else:
        uvicorn.run("app.main:app", host=settings.host, port=settings.port,
                    reload="--reload" in sys.argv, log_level="info")
