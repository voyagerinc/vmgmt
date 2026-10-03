"""Windows service wrapper for the endpoint agent (PRD §7.2, §10).

Installed by Agent_Setup.exe as "EndpointAgent" and started automatically. Reads its
configuration (server URL, device identity) from the agent state written at enrollment.

Manual service management (normally done by the installer, not the user):
    python agent_service.py install
    python agent_service.py start | stop | remove
"""
from __future__ import annotations

import sys

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
except ImportError:  # pragma: no cover  (non-Windows dev)
    win32serviceutil = None

from agent import Agent, STATE_DIR
import json


def _server_url() -> str:
    cfg = STATE_DIR / "agent_state.json"
    if cfg.exists():
        try:
            return json.loads(cfg.read_text(encoding="utf-8")).get("server", "")
        except Exception:
            pass
    return ""


if win32serviceutil:

    class EndpointAgentService(win32serviceutil.ServiceFramework):
        _svc_name_ = "EndpointAgent"
        _svc_display_name_ = "Endpoint Management Agent"
        _svc_description_ = ("Authorized IT endpoint management agent: inventory, health and "
                             "policy-driven monitoring. Managed by the organization's Management Server.")

        def __init__(self, args):
            super().__init__(args)
            self._stop_evt = win32event.CreateEvent(None, 0, 0, None)
            self._agent: Agent | None = None

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            if self._agent:
                self._agent._running = False
            win32event.SetEvent(self._stop_evt)

        def SvcDoRun(self):
            servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                                  servicemanager.PYS_SERVICE_STARTED, (self._svc_name_, ""))
            url = _server_url()
            if not url:
                servicemanager.LogErrorMsg("EndpointAgent: not enrolled; run the enrollment wizard.")
                return
            self._agent = Agent(url)
            if not self._agent.enrolled:
                servicemanager.LogErrorMsg("EndpointAgent: device not enrolled.")
                return
            self._agent.run()


def main():
    if not win32serviceutil:
        print("pywin32 is required to manage the Windows service.")
        return 1
    win32serviceutil.HandleCommandLine(EndpointAgentService)
    return 0


if __name__ == "__main__":
    sys.exit(main())
