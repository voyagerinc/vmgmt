"""Telemetry collectors for the endpoint agent (PRD §10, §13, §14, §15).

All collection is best-effort and degrades gracefully when a capability or platform
is unavailable. No credentials, secrets or unrelated private data are ever collected
(PRD §3.2, §17.3, §18).
"""
from __future__ import annotations

import platform
import socket
import time
import uuid

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

IS_WIN = platform.system() == "Windows"
_LAST_NET = {"t": 0.0, "sent": 0, "recv": 0}


# ----------------------------------------------------------------- identity
def machine_uid() -> str:
    """Stable per-device id. Prefers Windows MachineGuid, else MAC-derived UUID."""
    if IS_WIN:
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
            val, _ = winreg.QueryValueEx(k, "MachineGuid")
            winreg.CloseKey(k)
            return str(val)
        except Exception:
            pass
    return str(uuid.UUID(int=uuid.getnode()))


def hostname() -> str:
    return socket.gethostname()


def primary_ip() -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def mac_address() -> str:
    n = uuid.getnode()
    return ":".join(f"{(n >> i) & 0xff:02x}" for i in range(40, -1, -8))


def os_info() -> tuple[str, str]:
    return platform.system(), platform.version()


# ----------------------------------------------------------------- hardware
def hardware() -> dict:
    hw: dict = {"cpu": platform.processor() or platform.machine()}
    if psutil:
        try:
            hw["cores"] = psutil.cpu_count(logical=True)
            hw["ram_gb"] = round(psutil.virtual_memory().total / (1024 ** 3), 1)
            disk = psutil.disk_usage("/" if not IS_WIN else "C:\\")
            hw["disk_gb"] = round(disk.total / (1024 ** 3), 1)
        except Exception:
            pass
    return hw


# ----------------------------------------------------------------- health (PRD §15)
def health() -> dict:
    out: dict = {}
    if not psutil:
        return out
    try:
        out["cpu_percent"] = psutil.cpu_percent(interval=0.3)
        out["ram_percent"] = psutil.virtual_memory().percent
        out["disk_percent"] = psutil.disk_usage("C:\\" if IS_WIN else "/").percent
        out["uptime_seconds"] = int(time.time() - psutil.boot_time())
        now = time.time()
        io = psutil.net_io_counters()
        if _LAST_NET["t"]:
            dt = max(now - _LAST_NET["t"], 0.001)
            out["net_up_kbps"] = round((io.bytes_sent - _LAST_NET["sent"]) * 8 / 1000 / dt, 1)
            out["net_down_kbps"] = round((io.bytes_recv - _LAST_NET["recv"]) * 8 / 1000 / dt, 1)
        _LAST_NET.update(t=now, sent=io.bytes_sent, recv=io.bytes_recv)
        batt = getattr(psutil, "sensors_battery", lambda: None)()
        if batt:
            out["battery_percent"] = round(batt.percent, 0)
            out["battery_health"] = "on_ac" if batt.power_plugged else "on_battery"
    except Exception:
        pass
    return out


# ----------------------------------------------------------------- software (PRD §13)
def installed_software() -> list[dict]:
    if not IS_WIN:
        return _software_fallback()
    import winreg
    paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    seen, out = set(), []
    for root, path in paths:
        try:
            base = winreg.OpenKey(root, path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(base)[0]):
            try:
                sub = winreg.OpenKey(base, winreg.EnumKey(base, i))
                name = _reg(sub, "DisplayName")
                if not name or name in seen:
                    continue
                if _reg(sub, "SystemComponent") == 1:
                    continue
                seen.add(name)
                out.append({"name": name, "publisher": _reg(sub, "Publisher"),
                            "version": _reg(sub, "DisplayVersion"),
                            "install_date": _reg(sub, "InstallDate")})
            except OSError:
                continue
    return out


def _reg(key, name):
    try:
        import winreg
        v, _ = winreg.QueryValueEx(key, name)
        return v
    except Exception:
        return None


def _software_fallback() -> list[dict]:
    """Dev fallback on non-Windows: report the running Python + a couple of markers."""
    return [{"name": f"Python {platform.python_version()}", "publisher": "python.org",
             "version": platform.python_version(), "install_date": None}]


# ----------------------------------------------------------------- activity (PRD §14)
def active_window() -> dict | None:
    """Foreground window title + process (policy-gated by the server). Windows only."""
    if not IS_WIN:
        return None
    try:
        import win32gui
        import win32process
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        app = None
        if psutil and pid:
            try:
                app = psutil.Process(pid).name()
            except Exception:
                pass
        if not title and not app:
            return None
        return {"event_type": "active_window", "application": app, "title": title}
    except Exception:
        return None


# ----------------------------------------------------------------- screenshot (PRD §17)
def capture_screenshot() -> bytes | None:
    """Capture the primary display to PNG bytes. Only runs for signed, policy-approved jobs."""
    try:
        import io
        from mss import mss
        from PIL import Image
        with mss() as sct:
            mon = sct.monitors[1]
            raw = sct.grab(mon)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
    except Exception:
        return None
