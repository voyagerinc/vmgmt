"""Endpoint Agent — enrollment, heartbeat, telemetry, policy-driven capture (PRD §10).

Runs as a managed Windows service (see installers/). On non-Windows it runs as a
foreground process for development. Collects only what enabled server policies request;
buffers locally during outages and retries with backoff (PRD §10.3).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import signal
import sys
import time
from collections import deque
from pathlib import Path

import requests

import collectors

__version__ = "4.0.0"

STATE_DIR = Path(os.environ.get("PROGRAMDATA", str(Path.home()))) / "EndpointAgent"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "agent_state.json"
LOG_FILE = STATE_DIR / "agent.log"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
log = logging.getLogger("agent")


class Agent:
    def __init__(self, server: str):
        self.server = server.rstrip("/")
        self.state = self._load_state()
        self.session = requests.Session()
        self.heartbeat_interval = 60
        self.policy_version = self.state.get("policy_version", 0)
        self.policies: list[dict] = self.state.get("policies", [])
        # per-agent data profile (server-controlled; privacy-minimizing defaults)
        self.collection: dict = self.state.get("collection", {
            "health": True, "software": True, "activity": False,
            "file_events": False, "screenshots": True,
        })
        self.offline_queue: deque = deque(maxlen=5000)
        self._running = True
        self._last_software_push = 0.0
        self._activity_buf: list[dict] = []

    # ------------------------------------------------------------- state
    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_state(self) -> None:
        self.state["policy_version"] = self.policy_version
        self.state["policies"] = self.policies
        STATE_FILE.write_text(json.dumps(self.state), encoding="utf-8")

    @property
    def enrolled(self) -> bool:
        return bool(self.state.get("device_id") and self.state.get("device_cert"))

    def _auth_headers(self) -> dict:
        return {"X-Device-Id": self.state["device_id"], "X-Device-Cert": self.state["device_cert"]}

    # ------------------------------------------------------------- enrollment
    def enroll(self, license_id: str, token: str) -> bool:
        os_name, os_ver = collectors.os_info()
        payload = {
            "license_id": license_id, "enroll_token": token,
            "machine_uid": collectors.machine_uid(), "hostname": collectors.hostname(),
            "os_name": os_name, "os_version": os_ver, "ip_address": collectors.primary_ip(),
            "mac_address": collectors.mac_address(), "agent_version": __version__,
            "hardware": collectors.hardware(),
        }
        try:
            r = self.session.post(f"{self.server}/api/agents/enroll", json=payload, timeout=30)
        except requests.RequestException as e:
            log.error("Enrollment failed — server unreachable: %s", e)
            self._status("Server Unreachable")
            return False
        if r.status_code in (401, 402):
            log.error("Enrollment rejected: %s", r.text)
            self._status("License Issue")
            return False
        if not r.ok:
            log.error("Enrollment error %s: %s", r.status_code, r.text)
            return False
        d = r.json()
        self.state.update(device_id=d["device_id"], device_cert=d["device_cert"],
                          license_id=license_id, server=self.server)
        self.heartbeat_interval = d.get("heartbeat_interval", 60)
        self._save_state()
        log.info("Enrolled as device %s (status=%s)", d["device_id"], d.get("status"))
        self._status("Connected")
        return True

    # ------------------------------------------------------------- policy helpers
    def _policy_enabled(self, when_type: str) -> bool:
        """Only collect categories that at least one active policy references (data minimization)."""
        return any((p.get("rule", {}).get("when", {}).get("type") == when_type) for p in self.policies)

    # ------------------------------------------------------------- heartbeat
    def _build_heartbeat(self) -> dict:
        # Only collect what the server's per-agent profile enables (data minimization).
        body: dict = {"agent_version": __version__, "ip_address": collectors.primary_ip(),
                      "policy_version": self.policy_version, "activity": [], "file_events": []}
        if self.collection.get("health", True):
            body["health"] = collectors.health()
        if self.collection.get("activity", False):
            aw = collectors.active_window()
            if aw:
                body["activity"].append(aw)
            if self._activity_buf:
                body["activity"].extend(self._activity_buf)
                self._activity_buf = []
        # periodic full software snapshot (every 30 min)
        if self.collection.get("software", True) and time.time() - self._last_software_push > 1800:
            body["software"] = collectors.installed_software()
            self._last_software_push = time.time()
        return body

    def _post_heartbeat(self, body: dict) -> dict | None:
        try:
            r = self.session.post(f"{self.server}/api/agents/heartbeat", json=body,
                                  headers=self._auth_headers(), timeout=30)
        except requests.RequestException as e:
            log.warning("Heartbeat failed (queued): %s", e)
            self.offline_queue.append(body)
            self._status("Server Unreachable")
            return None
        if r.status_code in (401, 403):
            log.error("Device auth failed/revoked: %s", r.text)
            self._status("License Issue")
            return None
        if not r.ok:
            log.warning("Heartbeat error %s: %s", r.status_code, r.text)
            return None
        self._status("Connected")
        return r.json()

    def _flush_queue(self) -> None:
        while self.offline_queue:
            body = self.offline_queue[0]
            if self._post_heartbeat(body) is None:
                break
            self.offline_queue.popleft()

    def _handle_response(self, resp: dict) -> None:
        self.heartbeat_interval = resp.get("heartbeat_interval", self.heartbeat_interval)
        if resp.get("collection"):
            self.collection = resp["collection"]
            self.state["collection"] = self.collection
            self._save_state()
        if resp.get("policies") is not None:
            self.policies = resp["policies"]
            self.policy_version = resp.get("policy_version", self.policy_version)
            self._save_state()
            log.info("Policy updated: %d rule(s)", len(self.policies))
        for job in resp.get("screenshot_jobs", []):
            self._run_screenshot_job(job)
        for sess in resp.get("remote_sessions", []):
            log.info("Remote support session %s (%s, consent=%s) — broker handshake out of scope here",
                     sess.get("session_id"), sess.get("status"), sess.get("consent_mode"))

    def _run_screenshot_job(self, job: dict) -> None:
        """Capture only for a signed, policy-approved job (PRD §17.2)."""
        if not self.collection.get("screenshots", True):
            log.info("Screenshot job %s skipped — disabled in this agent's profile", job.get("job_id"))
            return
        log.info("Screenshot job %s reason=%s", job.get("job_id"), job.get("reason"))
        data = collectors.capture_screenshot()
        if not data:
            log.warning("Screenshot capture unavailable on this host")
            return
        try:
            files = {"file": (f"{job['job_id']}.png", data, "image/png")}
            r = self.session.post(f"{self.server}/api/agents/screenshot/{job['job_id']}",
                                  files=files, headers=self._auth_headers(), timeout=60)
            if r.ok:
                log.info("Evidence uploaded: %s", r.json().get("evidence_id"))
            else:
                log.warning("Evidence upload failed %s: %s", r.status_code, r.text)
        except requests.RequestException as e:
            log.warning("Evidence upload error: %s", e)

    def _status(self, status: str) -> None:
        (STATE_DIR / "status.txt").write_text(status, encoding="utf-8")

    # ------------------------------------------------------------- main loop
    def run(self) -> None:
        log.info("Agent %s starting on %s", __version__, platform.platform())
        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)
        while self._running:
            self._flush_queue()
            resp = self._post_heartbeat(self._build_heartbeat())
            if resp:
                self._handle_response(resp)
            slept = 0
            while slept < self.heartbeat_interval and self._running:
                time.sleep(1)
                slept += 1

    def _stop(self, *_):
        log.info("Agent stopping")
        self._running = False


FROZEN = getattr(sys, "frozen", False)


def _config_search_paths():
    paths = []
    if FROZEN:
        paths.append(Path(sys.executable).resolve().parent / "agent_config.json")  # next to the .exe
    paths.append(Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "agent_config.json")
    paths.append(Path(__file__).resolve().parent / "agent_config.json")
    paths.append(STATE_DIR / "agent_config.json")
    return paths


def load_bundled_config() -> dict:
    """Load a generated agent_config.json (server/license/enroll_token) if present.

    For the packaged .exe it is read from the folder the .exe runs in; for the source
    package, next to agent.py. Lets the agent self-enroll with no typing (PRD §7.2).
    """
    import json
    for candidate in _config_search_paths():
        try:
            if candidate.exists():
                return json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def _install_autostart() -> None:
    """Install the packaged agent for auto-start at logon (no admin required).

    Copies the .exe + its config into %LOCALAPPDATA%\\VoyagerAgent and registers a
    Scheduled Task that runs it hidden at every logon. Safe to call repeatedly.
    """
    if not FROZEN or os.name != "nt":
        return
    import shutil
    import subprocess
    try:
        dest_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VoyagerAgent"
        dest_dir.mkdir(parents=True, exist_ok=True)
        exe = Path(sys.executable).resolve()
        dest_exe = dest_dir / "VoyagerAgent.exe"
        if exe != dest_exe.resolve():
            shutil.copy2(exe, dest_exe)
            cfg = exe.parent / "agent_config.json"
            if cfg.exists():
                shutil.copy2(cfg, dest_dir / "agent_config.json")
        subprocess.run(
            ["schtasks", "/create", "/tn", "VoyagerEndpointAgent",
             "/tr", f'"{dest_exe}"', "/sc", "onlogon", "/rl", "limited", "/f"],
            capture_output=True, text=True,
        )
        log.info("Auto-start installed: %s", dest_exe)
    except Exception as e:
        log.warning("Auto-start install skipped: %s", e)


def main() -> int:
    cfg = load_bundled_config()
    ap = argparse.ArgumentParser(description="Endpoint Management Agent")
    ap.add_argument("--server", default=cfg.get("server"),
                    help="Management Server URL (defaults to bundled agent_config.json)")
    ap.add_argument("--license", default=cfg.get("license_id"),
                    help="Customer License ID (first enrollment only)")
    ap.add_argument("--token", default=cfg.get("enroll_token"),
                    help="Enrollment token (first enrollment only)")
    ap.add_argument("--reenroll", action="store_true", help="Force re-enrollment")
    args = ap.parse_args()

    if not args.server:
        log.error("No server URL. Provide --server or a bundled agent_config.json.")
        return 2

    agent = Agent(args.server)
    if args.reenroll:
        agent.state.pop("device_id", None)
        agent.state.pop("device_cert", None)

    if not agent.enrolled:
        if not (args.license and args.token):
            log.error("Not enrolled. Provide --license and --token (or a bundled agent_config.json).")
            if FROZEN:
                _show_message("Setup incomplete: agent_config.json is missing or invalid.\n"
                              "Keep VoyagerAgent.exe and agent_config.json together in the same folder.")
            return 2
        enrolled = agent.enroll(args.license, args.token)
        if not enrolled:
            if FROZEN:
                status = (STATE_DIR / "status.txt")
                label = status.read_text(encoding="utf-8").strip() if status.exists() else "Enrollment failed"
                _show_message(f"Could not enroll this computer.\nStatus: {label}\n"
                              "Check that the server is reachable and the license is active.")
            return 3
        if FROZEN:
            _install_autostart()
            _show_message("This computer has been enrolled and the agent is now running in the "
                          "background. It will start automatically at every logon.")
    elif FROZEN:
        _install_autostart()

    agent.run()
    return 0


def _show_message(text: str) -> None:
    """Best-effort popup for the packaged .exe; falls back to the log."""
    log.info(text.replace("\n", " "))
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, "Voyager Endpoint Agent", 0x40)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
