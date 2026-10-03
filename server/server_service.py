"""Windows service wrapper for the Management Server (PRD §7.1, §30).

Registered as "EndpointMgmtServer" by Server_Setup.exe and started automatically so the
console/API are available after a reboot without any command-line work.

    python server_service.py install | start | stop | remove
"""
from __future__ import annotations

import sys
import threading

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
except ImportError:  # pragma: no cover
    win32serviceutil = None

import uvicorn

from app.config import settings


class _UvicornRunner(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self._server = uvicorn.Server(uvicorn.Config(
            "app.main:app", host=settings.host, port=settings.port, log_level="info"))

    def run(self):
        self._server.run()

    def stop(self):
        self._server.should_exit = True


if win32serviceutil:

    class ManagementServerService(win32serviceutil.ServiceFramework):
        _svc_name_ = "EndpointMgmtServer"
        _svc_display_name_ = "Endpoint Management Server"
        _svc_description_ = "Management Server: admin console, REST API, policy/alert engine and license activation."

        def __init__(self, args):
            super().__init__(args)
            self._stop_evt = win32event.CreateEvent(None, 0, 0, None)
            self._runner: _UvicornRunner | None = None

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            if self._runner:
                self._runner.stop()
            win32event.SetEvent(self._stop_evt)

        def SvcDoRun(self):
            servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                                  servicemanager.PYS_SERVICE_STARTED, (self._svc_name_, ""))
            self._runner = _UvicornRunner()
            self._runner.start()
            win32event.WaitForSingleObject(self._stop_evt, win32event.INFINITE)


def main():
    if not win32serviceutil:
        print("pywin32 is required to manage the Windows service.")
        return 1
    win32serviceutil.HandleCommandLine(ManagementServerService)
    return 0


if __name__ == "__main__":
    sys.exit(main())
