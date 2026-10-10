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

__version__ = "4.4.0"


def _stamped_version() -> str:
    """The version the build stamped into this .exe (VOYAGER_AGENT_VERSION), else __version__.
    Servers compare against the stamp, so using it here keeps both sides in agreement."""
    if not getattr(sys, "frozen", False):
        return __version__
    import re
    try:
        with open(sys.executable, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 65536))
            m = re.findall(rb"VOYAGER_AGENT_VERSION:([0-9][0-9A-Za-z.\-]{0,30}):END_VOYAGER_AGENT_VERSION", f.read())
        return m[-1].decode() if m else __version__
    except OSError:
        return __version__


AGENT_VERSION = _stamped_version()

STATE_DIR = Path(os.environ.get("PROGRAMDATA", str(Path.home()))) / "EndpointAgent"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "agent_state.json"
LOG_FILE = STATE_DIR / "agent.log"

_handlers: list[logging.Handler] = []
if sys.stderr is not None:                  # windowed .exe has no console
    _handlers.append(logging.StreamHandler())
try:
    _handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))
except OSError:                             # e.g. log created by another account without access
    pass
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=_handlers or [logging.NullHandler()])
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
        self._last_interval_shot = 0.0
        self._activity_buf: list[dict] = []
        # logins / network+Wi-Fi / USB / email trackers, configured by the server's tracking profile
        self.tracker = None
        if os.name == "nt":
            try:
                import trackers
                self.tracker = trackers.EventTracker(STATE_DIR, self._send_events)
            except Exception as e:
                log.warning("Trackers unavailable: %s", e)

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
            "mac_address": collectors.mac_address(), "agent_version": AGENT_VERSION,
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
        body: dict = {"agent_version": AGENT_VERSION, "ip_address": collectors.primary_ip(),
                      "policy_version": self.policy_version, "activity": [], "file_events": []}
        if self.collection.get("health", True):
            body["health"] = collectors.health()
        if self.collection.get("active_time", True) and body.get("health"):
            idle = collectors.idle_seconds()
            if idle is not None:
                body["health"].setdefault("extra", {})["idle_seconds"] = idle
                body["health"]["extra"]["active"] = idle < 60
        trk = self.tracker.settings if self.tracker else {}
        if self.tracker and (trk.get("app_activity") or trk.get("web_activity")):
            body["activity"].extend(self.tracker.take_activity())   # timed app segments + web visits
        elif self.collection.get("activity", False):
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

    def _send_events(self, events: list[dict]) -> dict | None:
        """Upload a batch of tracker events; None keeps them queued for the next sync."""
        if not self.enrolled:
            return None
        try:
            r = self.session.post(f"{self.server}/api/agents/events", json={"events": events},
                                  headers=self._auth_headers(), timeout=60)
        except requests.RequestException as e:
            log.warning("Event sync failed (kept for next sync): %s", e)
            return None
        if not r.ok:
            log.warning("Event sync error %s: %s", r.status_code, r.text[:200])
            return None
        log.info("Synced %d tracking event(s)", len(events))
        return r.json()

    def _handle_response(self, resp: dict) -> None:
        self.heartbeat_interval = resp.get("heartbeat_interval", self.heartbeat_interval)
        if resp.get("tracking") and self.tracker:
            self.tracker.apply(resp["tracking"])
            if resp["tracking"] != self.state.get("tracking"):
                self.state["tracking"] = resp["tracking"]
                self._save_state()
        if resp.get("collection"):
            self.collection = resp["collection"]
            self.state["collection"] = self.collection
            self._save_state()
        if resp.get("policies") is not None:
            self.policies = resp["policies"]
            self.policy_version = resp.get("policy_version", self.policy_version)
            self._save_state()
            log.info("Policy updated: %d rule(s)", len(self.policies))
        upd = resp.get("agent_update")
        if upd and FROZEN:
            self._self_update(upd)
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

    def _self_update(self, upd: dict) -> None:
        """Approved agent update offered in the heartbeat (PRD §30).

        Per-user installs (%LOCALAPPDATA%) swap their own .exe here. Installs for all users
        (Program Files) are not writable by this process: the SYSTEM updater task does it, and
        this agent restarts itself when it sees its .exe replaced (_exe_replaced)."""
        if vtuple(upd.get("version")) <= vtuple(AGENT_VERSION) or not _is_installed_copy():
            return
        if Path(sys.executable).resolve().parent != USER_DIR.resolve():
            return                                      # machine-wide install: SYSTEM task updates it
        if _apply_update(USER_DIR, upd["url"], upd.get("sha256"), upd.get("version"), self.session):
            log.info("Agent %s installed; restarting", upd.get("version"))

    def _exe_replaced(self) -> bool:
        """True when our .exe on disk was swapped for a newer build (by an updater)."""
        try:
            st = Path(sys.executable).stat()
            return (st.st_size, st.st_mtime) != self._exe_sig
        except OSError:
            return False

    def _restart_into_new_exe(self) -> None:
        import ctypes
        import subprocess
        log.info("Agent .exe was updated - restarting into the new version")
        global _mutex
        if _mutex:                                       # let the new copy take the session slot
            ctypes.windll.kernel32.CloseHandle(_mutex)
            _mutex = None
        subprocess.Popen([sys.executable], close_fds=True, creationflags=0x00000008 | 0x00000200)
        self._running = False
        if self.tracker:
            self.tracker.stop()

    def _status(self, status: str) -> None:
        (STATE_DIR / "status.txt").write_text(status, encoding="utf-8")

    # ------------------------------------------------------------- main loop
    def _interval_capture_due(self) -> bool:
        interval = int(self.collection.get("screenshot_interval", 0) or 0)
        if interval <= 0 or not self.collection.get("screenshots", True):
            return False
        return (time.time() - self._last_interval_shot) >= interval

    def _interval_capture(self) -> None:
        data = collectors.capture_screenshot()
        if not data:
            return
        try:
            self.session.post(f"{self.server}/api/agents/evidence",
                              data={"reason": "interval"},
                              files={"file": ("shot.png", data, "image/png")},
                              headers=self._auth_headers(), timeout=60)
            self._last_interval_shot = time.time()
        except requests.RequestException as e:
            log.warning("Interval screenshot upload failed: %s", e)

    def run(self) -> None:
        log.info("Agent %s starting on %s", AGENT_VERSION, platform.platform())
        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)
        if self.tracker and self.state.get("tracking"):
            self.tracker.apply(self.state["tracking"])      # last known profile, before 1st heartbeat
        watch_exe = FROZEN and os.name == "nt" and _is_installed_copy()
        if watch_exe:
            st = Path(sys.executable).stat()
            self._exe_sig = (st.st_size, st.st_mtime)
        while self._running:
            self._flush_queue()
            resp = self._post_heartbeat(self._build_heartbeat())
            if resp:
                self._handle_response(resp)
            slept = 0
            while slept < self.heartbeat_interval and self._running:
                time.sleep(1)
                slept += 1
                if self._interval_capture_due():
                    self._interval_capture()
                if self.tracker:
                    self.tracker.maybe_flush()
                if watch_exe and slept % 30 == 0 and self._exe_replaced():
                    self._restart_into_new_exe()
                    return

    def _stop(self, *_):
        log.info("Agent stopping")
        self._running = False
        if self.tracker:
            self.tracker.stop()


FROZEN = getattr(sys, "frozen", False)


def _config_search_paths():
    paths = []
    if FROZEN:
        paths.append(Path(sys.executable).resolve().parent / "agent_config.json")  # next to the .exe
    paths.append(Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "agent_config.json")
    paths.append(Path(__file__).resolve().parent / "agent_config.json")
    paths.append(STATE_DIR / "agent_config.json")
    return paths


_CFG_BEGIN = b"VOYAGER_AGENT_CONFIG:"
_CFG_END = b":END_VOYAGER_AGENT_CONFIG"


def load_embedded_config() -> dict:
    """Config the Management Server appended to this .exe when it was downloaded
    (base64 JSON between markers at the end of the file), so one file is all a PC needs."""
    if not FROZEN:
        return {}
    import base64
    try:
        with open(sys.executable, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))
            tail = f.read()
        start = tail.rfind(_CFG_BEGIN)
        if start < 0:
            return {}
        body = tail[start + len(_CFG_BEGIN):]
        return json.loads(base64.b64decode(body[:body.index(_CFG_END)]))
    except Exception:
        return {}


def load_bundled_config() -> dict:
    """Load the server/license/enroll_token config: embedded in the .exe (preferred), else a
    generated agent_config.json next to the .exe / agent.py. Lets the agent self-enroll with
    no typing (PRD §7.2).
    """
    import json
    embedded = load_embedded_config()
    if embedded.get("server"):
        return embedded
    for candidate in _config_search_paths():
        try:
            if candidate.exists():
                return json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


MACHINE_DIR = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "VoyagerAgent"
USER_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VoyagerAgent"
AUTOSTART_NAME = "VoyagerEndpointAgent"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_mutex = None


def _is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _is_installed_copy() -> bool:
    """True when running from the install folder (autostart), False when double-clicked
    from wherever it was downloaded (installer mode)."""
    here = str(Path(sys.executable).resolve().parent).lower()
    return here in (str(MACHINE_DIR).lower(), str(USER_DIR).lower())


def _relaunch_elevated() -> bool:
    """Ask Windows (UAC) to run this .exe as administrator; True if the elevated copy started."""
    try:
        import ctypes
        import subprocess
        params = subprocess.list2cmdline(sys.argv[1:])
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1) > 32
    except Exception:
        return False


def _single_instance() -> bool:
    """One agent per logon session (named mutex); False if another one is already running."""
    global _mutex
    try:
        import ctypes
        _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\VoyagerEndpointAgent")
        return ctypes.windll.kernel32.GetLastError() != 183      # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def _stop_running_copies(dest_exe: Path) -> None:
    """Stop an already-installed agent so its .exe can be replaced (re-install / update)."""
    try:
        import psutil
        target = str(dest_exe).lower()
        procs = []
        for p in psutil.process_iter(["pid", "exe"]):
            if p.info["pid"] != os.getpid() and (p.info.get("exe") or "").lower() == target:
                p.terminate()
                procs.append(p)
        psutil.wait_procs(procs, timeout=5)
    except Exception as e:
        log.warning("Could not stop running agent: %s", e)


UPDATER_TASK = "VoyagerAgentUpdater"


def vtuple(v) -> tuple:
    try:
        return tuple(int(x) for x in str(v).split(".")[:3])
    except Exception:
        return (0,)


def _apply_update(install_dir: Path, url: str, sha256: str | None, version: str | None, session=None) -> bool:
    """Download the new agent next to the installed one, verify it, then swap: a running .exe
    can be renamed (not deleted), so running agents keep going and restart into the new file
    when they notice it (no logoff, no killing other users' agents)."""
    import hashlib
    cur = install_dir / "VoyagerAgent.exe"
    new = install_dir / "VoyagerAgent.new"
    sess = session or requests.Session()
    try:
        h = hashlib.sha256()
        size = 0
        with sess.get(url, stream=True, timeout=300) as r:
            if not r.ok:
                log.warning("Update download failed: HTTP %s", r.status_code)
                return False
            with open(new, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    if chunk:
                        f.write(chunk)
                        h.update(chunk)
                        size += len(chunk)
        with open(new, "rb") as f:
            magic = f.read(2)
        if magic != b"MZ" or size < 1_000_000 or (sha256 and h.hexdigest() != sha256):
            log.warning("Update rejected: not a valid agent build (size=%s, checksum mismatch=%s)",
                        size, bool(sha256 and h.hexdigest() != sha256))
            new.unlink(missing_ok=True)
            return False
        old = install_dir / f"VoyagerAgent.old-{int(time.time())}.exe"
        cur.rename(old)                                  # allowed while it is running
        try:
            new.rename(cur)
        except OSError:
            old.rename(cur)                              # roll back
            raise
        try:
            meta = json.loads((install_dir / "machine.json").read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        meta["version"] = version
        try:
            (install_dir / "machine.json").write_text(json.dumps(meta), encoding="utf-8")
        except OSError:
            pass
        log.info("Agent updated to %s in %s", version, install_dir)
        return True
    except Exception as e:
        log.warning("Update failed: %s", e)
        try:
            new.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def run_updater() -> int:
    """`VoyagerAgent.exe --update`, run every 10 minutes by the SYSTEM task of an all-users install.

    Trusts only machine.json in the admin-only install folder (server address, device id), asks
    the server whether a newer agent is approved for this computer, and swaps it in."""
    install_dir = Path(sys.executable).resolve().parent
    for stale in install_dir.glob("VoyagerAgent.old-*.exe"):     # left by an earlier update
        try:
            stale.unlink()
        except OSError:
            pass                                                  # still running somewhere
    try:
        meta = json.loads((install_dir / "machine.json").read_text(encoding="utf-8"))
    except Exception:
        log.info("Updater: no machine.json in %s - nothing to do", install_dir)
        return 0
    server = (meta.get("server") or "").rstrip("/")
    if not server:
        return 0
    installed = meta.get("version") or AGENT_VERSION
    try:
        params = {"device_id": meta["device_id"]} if meta.get("device_id") else None
        info = requests.get(f"{server}/api/updates/agent-info", params=params, timeout=30).json()
    except Exception as e:
        log.info("Updater: server not reachable (%s)", e)
        return 0
    ver = info.get("version")
    if not info.get("available") or not ver or vtuple(ver) <= vtuple(installed):
        return 0
    if not info.get("approved"):
        log.info("Updater: agent %s available but not approved for this computer yet", ver)
        return 0
    ok = _apply_update(install_dir, f"{server}/api/updates/download/agent", info.get("sha256"), ver)
    return 0 if ok else 1


def _write_machine_meta(dest_dir: Path, server: str, device_id: str | None) -> None:
    try:
        (dest_dir / "machine.json").write_text(json.dumps(
            {"server": server, "device_id": device_id, "version": AGENT_VERSION}), encoding="utf-8")
    except OSError as e:
        log.warning("Could not write machine.json: %s", e)


def _install(machine_wide: bool, server: str = "", device_id: str | None = None) -> Path:
    """Copy this .exe (it carries its own config) to the install folder and register autostart.

    machine_wide (run as administrator): C:\\Program Files\\VoyagerAgent, started for EVERY user
    at logon (HKLM Run), state folder writable by all users, outbound firewall rule, and a SYSTEM
    task that applies approved agent updates every 10 minutes.
    Otherwise: %LOCALAPPDATA%\\VoyagerAgent + a per-user logon task (no admin needed); that copy
    updates itself from the heartbeat.
    """
    import shutil
    import subprocess
    dest_dir = MACHINE_DIR if machine_wide else USER_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_exe = dest_dir / "VoyagerAgent.exe"
    if Path(sys.executable).resolve() != dest_exe.resolve():
        _stop_running_copies(dest_exe)
        shutil.copy2(sys.executable, dest_exe)
    if machine_wide:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, _RUN_KEY, 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
            winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_SZ, f'"{dest_exe}"')
        # a per-user task from an earlier install would start a second copy
        subprocess.run(["schtasks", "/delete", "/tn", AUTOSTART_NAME, "/f"], capture_output=True)
        # employees (standard users) must be able to update the shared agent state/log
        subprocess.run(["icacls", str(STATE_DIR), "/grant", "*S-1-5-32-545:(OI)(CI)M", "/T", "/C", "/Q"],
                       capture_output=True)
        _add_firewall_rule(dest_exe)
        _write_machine_meta(dest_dir, server, device_id)   # admin-only folder: updater's trust root
        r = subprocess.run(["schtasks", "/create", "/tn", UPDATER_TASK, "/tr", f'"{dest_exe}" --update',
                            "/sc", "minute", "/mo", "10", "/ru", "SYSTEM", "/rl", "HIGHEST", "/f"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            log.warning("Updater task not created: %s", (r.stdout or r.stderr).strip())
    else:
        _write_machine_meta(dest_dir, server, device_id)
        subprocess.run(["schtasks", "/create", "/tn", AUTOSTART_NAME, "/tr", f'"{dest_exe}"',
                        "/sc", "onlogon", "/rl", "limited", "/f"], capture_output=True)
    log.info("Installed %s (%s)", dest_exe, "all users" if machine_wide else "current user")
    return dest_exe


def _start_installed(dest_exe: Path) -> None:
    import subprocess
    try:
        subprocess.Popen([str(dest_exe)], close_fds=True,
                         creationflags=0x00000008 | 0x00000200)   # DETACHED_PROCESS | NEW_PROCESS_GROUP
    except Exception as e:
        log.warning("Could not start installed agent: %s", e)


def _add_firewall_rule(exe: Path) -> None:
    """Add an outbound Windows Firewall allow rule for the agent->server connection (PRD §3.1).

    Requires admin rights; silently ignored otherwise. This only *allows* our own traffic —
    it never disables the firewall or opens unrelated ports.
    """
    if os.name != "nt":
        return
    import subprocess
    try:
        subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule",
                        "name=Voyager Endpoint Agent"], capture_output=True, text=True)
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             "name=Voyager Endpoint Agent", "dir=out", "action=allow",
             f"program={exe}", "enable=yes"],
            capture_output=True, text=True,
        )
        log.info("Firewall rule added for %s", exe)
    except Exception as e:
        log.warning("Firewall rule skipped (needs admin): %s", e)


def _enroll_if_needed(agent: "Agent", args) -> bool:
    """Enroll (or re-enroll when the .exe points at a different server). Shows a popup on failure."""
    if agent.enrolled and (agent.state.get("server") or "").rstrip("/") != args.server.rstrip("/"):
        log.info("Server changed (%s -> %s): re-enrolling", agent.state.get("server"), args.server)
        agent.state.pop("device_id", None)
        agent.state.pop("device_cert", None)
    if agent.enrolled:
        return True
    if not (args.license and args.token):
        log.error("Not enrolled. Provide --license and --token (or a bundled agent_config.json).")
        if FROZEN:
            _show_message("This VoyagerAgent.exe has no company settings.\n"
                          "Download the agent again from your Management Server "
                          "(Downloads -> Download VoyagerAgent.exe) and run that file.")
        return False
    if agent.enroll(args.license, args.token):
        return True
    if FROZEN:
        status = STATE_DIR / "status.txt"
        label = status.read_text(encoding="utf-8").strip() if status.exists() else "Enrollment failed"
        _show_message(f"Could not connect this computer to {args.server}.\nStatus: {label}\n\n"
                      "Check that this PC can open that address in a browser and that the "
                      "company license is active, then run VoyagerAgent.exe again.")
    return False


def main() -> int:
    cfg = load_bundled_config()
    ap = argparse.ArgumentParser(description="Endpoint Management Agent")
    ap.add_argument("--server", default=cfg.get("server"),
                    help="Management Server URL (defaults to the config embedded in the .exe)")
    ap.add_argument("--license", default=cfg.get("license_id"),
                    help="Customer License ID (first enrollment only)")
    ap.add_argument("--token", default=cfg.get("enroll_token"),
                    help="Enrollment token (first enrollment only)")
    ap.add_argument("--reenroll", action="store_true", help="Force re-enrollment")
    ap.add_argument("--update", action="store_true",
                    help="Apply an approved agent update (run by the SYSTEM updater task)")
    args = ap.parse_args()

    if args.update:
        return run_updater()

    if not args.server and STATE_FILE.exists():
        try:                       # already enrolled: keep using the saved server (e.g. after self-update)
            args.server = json.loads(STATE_FILE.read_text(encoding="utf-8")).get("server")
        except Exception:
            pass
    if not args.server:
        log.error("No server URL. Provide --server or a bundled agent_config.json.")
        if FROZEN:
            _show_message("This VoyagerAgent.exe has no company settings.\n"
                          "Download the agent again from your Management Server and run that file.")
        return 2

    windows_exe = FROZEN and os.name == "nt"

    # ---- installer mode: the downloaded .exe was double-clicked ----
    if windows_exe and not _is_installed_copy():
        machine_wide = _is_admin()
        if not machine_wide and _relaunch_elevated():
            return 0                                  # the elevated copy does the install
        agent = Agent(args.server)
        if args.reenroll:
            agent.state.pop("device_id", None)
            agent.state.pop("device_cert", None)
        if not _enroll_if_needed(agent, args):
            return 3
        try:
            dest = _install(machine_wide, args.server, agent.state.get("device_id"))
        except Exception as e:
            log.error("Install failed: %s", e)
            _show_message(f"The agent is enrolled but could not be installed:\n{e}")
            return 4
        _start_installed(dest)
        company = cfg.get("company") or "your company"
        _show_message(f"Voyager Endpoint Agent is installed for {company}.\n\n"
                      f"Server: {args.server}\nInstalled to: {dest.parent}\n\n"
                      + ("It runs in the background and starts automatically for every user "
                         "at logon." if machine_wide else
                         "It runs in the background and starts automatically at your logon.\n"
                         "(Run it as administrator to install it for all users.)"))
        return 0

    # ---- run mode: installed copy at logon (or the source agent) ----
    if windows_exe and not _single_instance():
        return 0                                      # already running in this session
    agent = Agent(args.server)
    if args.reenroll:
        agent.state.pop("device_id", None)
        agent.state.pop("device_cert", None)
    if not _enroll_if_needed(agent, args):
        return 3
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
