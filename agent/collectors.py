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


def hardware_full() -> dict:
    """Complete hardware inventory from WMI (Windows): make/model/serial, BIOS, CPU, memory
    modules, physical disks (SSD/HDD), volumes with free space, GPU, OS, network adapters."""
    hw = hardware()
    if not IS_WIN:
        return hw
    try:
        import pythoncom
        import win32com.client
    except Exception:
        return hw
    pythoncom.CoInitialize()
    try:
        hw.update(_wmi_inventory(win32com.client, hw))    # all COM objects are released on return
    except Exception:
        pass
    finally:
        import gc
        gc.collect()
        pythoncom.CoUninitialize()
    return hw


def _wmi_inventory(client, hw: dict) -> dict:
    try:
        wmi = client.GetObject("winmgmts:\\\\.\\root\\cimv2")

        def q(cls, props, where=""):
            out = []
            try:
                for o in wmi.ExecQuery(f"SELECT {','.join(props)} FROM {cls} {where}"):
                    out.append({p: getattr(o, p, None) for p in props})
            except Exception:
                pass
            return out

        def gb(v):
            try:
                return round(int(v) / (1024 ** 3), 1)
            except (TypeError, ValueError):
                return None

        cs = (q("Win32_ComputerSystem", ["Manufacturer", "Model", "TotalPhysicalMemory", "Domain",
                                         "SystemType"]) or [{}])[0]
        bios = (q("Win32_BIOS", ["SerialNumber", "SMBIOSBIOSVersion", "ReleaseDate"]) or [{}])[0]
        board = (q("Win32_BaseBoard", ["Manufacturer", "Product"]) or [{}])[0]
        osi = (q("Win32_OperatingSystem", ["Caption", "Version", "BuildNumber", "OSArchitecture",
                                           "InstallDate", "LastBootUpTime"]) or [{}])[0]
        cpus = q("Win32_Processor", ["Name", "NumberOfCores", "NumberOfLogicalProcessors", "MaxClockSpeed"])
        mem = q("Win32_PhysicalMemory", ["Capacity", "Speed", "Manufacturer", "PartNumber", "DeviceLocator"])
        disks = q("Win32_DiskDrive", ["Model", "Size", "InterfaceType", "SerialNumber", "Index"])
        vols = q("Win32_LogicalDisk", ["DeviceID", "Size", "FreeSpace", "FileSystem", "VolumeName"],
                 "WHERE DriveType=3")
        gpus = q("Win32_VideoController", ["Name", "AdapterRAM", "DriverVersion"])
        nics = q("Win32_NetworkAdapterConfiguration", ["Description", "MACAddress", "IPAddress"],
                 "WHERE IPEnabled=True")
        # SSD vs HDD (Windows 8+ storage namespace)
        media = {}
        try:
            st = client.GetObject("winmgmts:\\\\.\\root\\Microsoft\\Windows\\Storage")
            for d in st.ExecQuery("SELECT DeviceId, MediaType, BusType FROM MSFT_PhysicalDisk"):
                media[str(d.DeviceId)] = ({3: "HDD", 4: "SSD", 5: "SCM"}.get(int(d.MediaType or 0), "Unknown"),
                                          {7: "USB", 11: "SATA", 17: "NVMe", 8: "RAID"}.get(int(d.BusType or 0)))
        except Exception:
            pass

        def wdate(v):
            s = str(v or "")
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}" + (f" {s[8:10]}:{s[10:12]}" if len(s) >= 12 else "") if len(s) >= 8 else None

        cpu = cpus[0] if cpus else {}
        return {
            "manufacturer": (cs.get("Manufacturer") or "").strip(), "model": (cs.get("Model") or "").strip(),
            "serial": (bios.get("SerialNumber") or "").strip(), "bios": bios.get("SMBIOSBIOSVersion"),
            "bios_date": wdate(bios.get("ReleaseDate")), "board": " ".join(
                x for x in ((board.get("Manufacturer") or "").strip(), (board.get("Product") or "").strip()) if x),
            "domain": cs.get("Domain"), "system_type": cs.get("SystemType"),
            "cpu": (cpu.get("Name") or hw.get("cpu") or "").strip(), "cpu_count": len(cpus),
            "cores": cpu.get("NumberOfCores"), "threads": cpu.get("NumberOfLogicalProcessors"),
            "cpu_mhz": cpu.get("MaxClockSpeed"),
            "ram_gb": gb(cs.get("TotalPhysicalMemory")) or hw.get("ram_gb"),
            "memory_modules": [{"slot": m.get("DeviceLocator"), "gb": gb(m.get("Capacity")), "speed": m.get("Speed"),
                                "maker": (m.get("Manufacturer") or "").strip(), "part": (m.get("PartNumber") or "").strip()}
                               for m in mem],
            "disks": [{"model": (d.get("Model") or "").strip(), "gb": gb(d.get("Size")),
                       "interface": (media.get(str(d.get("Index")), (None, None))[1] or d.get("InterfaceType")),
                       "type": media.get(str(d.get("Index")), ("Unknown", None))[0],
                       "serial": (d.get("SerialNumber") or "").strip()} for d in disks],
            "volumes": [{"drive": v.get("DeviceID"), "label": v.get("VolumeName"), "fs": v.get("FileSystem"),
                         "gb": gb(v.get("Size")), "free_gb": gb(v.get("FreeSpace"))} for v in vols],
            "disk_gb": round(sum(gb(v.get("Size")) or 0 for v in vols), 1) or hw.get("disk_gb"),
            "disk_free_gb": round(sum(gb(v.get("FreeSpace")) or 0 for v in vols), 1),
            "gpus": [{"name": g.get("Name"), "reported_vram_gb": gb(g.get("AdapterRAM")), "driver": g.get("DriverVersion")}
                     for g in gpus],
            "os": " ".join(x for x in (osi.get("Caption"), osi.get("OSArchitecture")) if x),
            "os_version": osi.get("Version"), "os_build": osi.get("BuildNumber"),
            "os_installed": wdate(osi.get("InstallDate")), "last_boot": wdate(osi.get("LastBootUpTime")),
            "network": [{"name": n.get("Description"), "mac": n.get("MACAddress"),
                         "ip": ", ".join(ip for ip in (n.get("IPAddress") or []) if ":" not in ip)} for n in nics],
            "collected_at": time.strftime("%Y-%m-%d %H:%M"),
        }
    except Exception:
        return {}


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


# ----------------------------------------------------------------- active time (PRD §2.1)
def idle_seconds() -> float | None:
    """Seconds since last keyboard/mouse input (Windows). Used for active-time tracking.

    Only an idle *duration* is measured — never key contents (keystroke logging is a
    separate, explicitly-enabled feature that skips password fields)."""
    if not IS_WIN:
        return None
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
            return round(millis / 1000.0, 1)
    except Exception:
        pass
    return None


def is_active(idle_threshold: int = 60) -> bool:
    """True if the user interacted within `idle_threshold` seconds."""
    idle = idle_seconds()
    return idle is None or idle < idle_threshold


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


def capture_jpeg(quality: int = 45, max_width: int = 1280, monitor: int = 0) -> bytes | None:
    """Capture to JPEG for live view: small + fast. monitor 0 = all screens, 1 = primary, etc."""
    try:
        import io
        from mss import mss
        from PIL import Image
        with mss() as sct:
            mons = sct.monitors
            mon = mons[monitor] if 0 <= monitor < len(mons) else mons[1 if len(mons) > 1 else 0]
            raw = sct.grab(mon)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            if img.width > max_width:
                img = img.resize((max_width, max(1, round(img.height * max_width / img.width))),
                                 Image.BILINEAR)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=max(20, min(90, quality)))
            return buf.getvalue()
    except Exception:
        return None
