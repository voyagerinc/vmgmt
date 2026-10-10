"""Event trackers (Windows): logins, network/Wi-Fi, USB file copies, email file sends.

Driven by the tracking profile the server sends in each heartbeat:
    {logins, network, wifi, usb_files, email_files, file_types, sync_interval, ...}

Every tracker appends events to a queue persisted in STATE_DIR (survives restarts/offline) and
the queue is uploaded to POST /api/agents/events every `sync_interval` seconds. Only metadata is
recorded (names, sizes, times, drive / Wi-Fi / recipient names) — never file contents or mail
bodies. All collectors are best-effort: a failure disables that collector, never the agent.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("agent.trackers")

IS_WIN = os.name == "nt"
_NO_WINDOW = 0x08000000                         # CREATE_NO_WINDOW for netsh
_BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "iexplore.exe",
             "vivaldi.exe", "opera_gx.exe"}
_WEBMAIL = ("gmail", "mail.google", "outlook", "office 365", "microsoft 365", "yahoo mail",
            "zoho mail", "rediffmail", "proton mail", "inbox", "compose", "new message", "mail -")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _user() -> str:
    dom = os.environ.get("USERDOMAIN", "")
    name = os.environ.get("USERNAME") or ""
    return f"{dom}\\{name}" if dom and name else name


class EventTracker:
    def __init__(self, state_dir: Path, send):
        """send(events: list[dict]) -> dict | None  (posts a batch; returns server JSON or None)."""
        self.state_dir = state_dir
        self.queue_file = state_dir / "tracking_queue.jsonl"
        self.cursor_file = state_dir / "tracking_cursor.json"
        self._send = send
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.settings: dict = {}
        self._last_flush = 0.0
        self._threads: list[threading.Thread] = []
        self._act_lock = threading.Lock()
        self.activity_buf: list[dict] = []      # app/web activity, drained into each heartbeat
        try:
            self.cursor = json.loads(self.cursor_file.read_text(encoding="utf-8"))
        except Exception:
            self.cursor = {}

    # ------------------------------------------------------------------ plumbing
    def apply(self, settings: dict | None) -> None:
        if not settings:
            return
        changed = settings != self.settings
        self.settings = dict(settings)
        if changed:
            log.info("Tracking profile '%s': logins=%s network=%s wifi=%s usb=%s email=%s every %ss",
                     settings.get("profile_name"), settings.get("logins"), settings.get("network"),
                     settings.get("wifi"), settings.get("usb_files"), settings.get("email_files"),
                     settings.get("sync_interval"))
        if not self._threads and IS_WIN:
            for fn in (self._login_loop, self._network_loop, self._usb_loop, self._email_loop,
                       self._app_loop, self._web_loop):
                t = threading.Thread(target=self._guard, args=(fn,), name=fn.__name__, daemon=True)
                t.start()
                self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()

    def _guard(self, fn) -> None:
        while not self._stop.is_set():
            try:
                fn()
                return
            except Exception as e:                       # restart the collector after a pause
                log.warning("Tracker %s error: %s", fn.__name__, e)
                self._stop.wait(60)

    def _on(self, key: str) -> bool:
        return bool(self.settings.get(key))

    def _tracked(self, name: str) -> bool:
        ext = os.path.splitext(name)[1].lower()
        types = self.settings.get("file_types") or []
        return bool(ext) and ext in types

    def emit(self, category: str, event_type: str, detail: str = "", **fields) -> None:
        # user="" (machine events: startup/shutdown/sleep) is kept blank; omitted = current user
        user = fields.pop("user") if "user" in fields else _user()
        ev = {"category": category, "event_type": event_type, "detail": detail,
              "user": user or None, "ts": fields.pop("ts", None) or _now_iso(), **fields}
        line = json.dumps(ev, default=str)
        with self._lock:
            try:
                with open(self.queue_file, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as e:
                log.warning("Could not queue event: %s", e)
        log.info("Event %s/%s %s", category, event_type, detail)

    def _save_cursor(self) -> None:
        try:
            self.cursor_file.write_text(json.dumps(self.cursor), encoding="utf-8")
        except OSError:
            pass

    def maybe_flush(self, force: bool = False) -> None:
        """Upload queued events when the profile's sync interval has elapsed."""
        interval = int(self.settings.get("sync_interval") or 300)
        if not force and time.time() - self._last_flush < interval:
            return
        with self._lock:
            try:
                lines = self.queue_file.read_text(encoding="utf-8").splitlines() if self.queue_file.exists() else []
            except OSError:
                return
        if not lines:
            return                       # nothing yet: check again next second (interval starts at a send)
        self._last_flush = time.time()
        batch = []
        for ln in lines[:1000]:
            try:
                batch.append(json.loads(ln))
            except ValueError:
                pass
        resp = self._send(batch)
        if resp is None:
            if len(lines) > 50000:                       # server unreachable for a long time: cap
                with self._lock:
                    self.queue_file.write_text("\n".join(lines[-50000:]) + "\n", encoding="utf-8")
            return
        with self._lock:                                  # drop what was sent, keep newer lines
            try:
                cur = self.queue_file.read_text(encoding="utf-8").splitlines()
                rest = cur[len(lines[:1000]):]
                self.queue_file.write_text(("\n".join(rest) + "\n") if rest else "", encoding="utf-8")
            except OSError:
                pass
        if resp.get("tracking"):
            self.apply(resp["tracking"])
        if len(lines) > 1000:
            self._last_flush = 0                          # more to send: next tick

    # ------------------------------------------------------------------ app / web activity
    def take_activity(self) -> list[dict]:
        """Everything collected so far, including the part of the app/idle segment still in
        progress (it is split here, so reports are never more than one heartbeat behind)."""
        with self._act_lock:
            seg = getattr(self, "_seg", None)
            if seg and seg[4] >= 1:
                self.activity_buf.append(self._seg_item(seg))
                seg[3], seg[4] = seg[3] + seg[4], 0.0          # continue the same window from here
            items, self.activity_buf = self.activity_buf, []
        return items

    @staticmethod
    def _seg_item(seg) -> dict:
        app, title = seg[1], seg[2]
        return {"event_type": "idle" if app == "(idle)" else "app",
                "application": None if app == "(idle)" else app,
                "title": (title or "")[:400], "duration_seconds": int(seg[4]),
                "ts": datetime.fromtimestamp(seg[3], timezone.utc).isoformat()}

    def _add_activity(self, item: dict) -> None:
        with self._act_lock:
            if len(self.activity_buf) < 20000:           # bounded if the server is unreachable
                self.activity_buf.append(item)

    def _app_loop(self) -> None:
        """Time spent per application/window: sample the foreground window every 5 s and emit a
        segment with its real duration when it changes (max 5 min per segment). Time with no
        keyboard/mouse input for 2 min is reported as idle, not as app usage."""
        import collectors
        self._seg = None                                   # [key, app, title, start_ts, seconds]
        last = time.time()

        def close():
            with self._act_lock:
                if self._seg and self._seg[4] >= 1 and len(self.activity_buf) < 20000:
                    self.activity_buf.append(self._seg_item(self._seg))
                self._seg = None

        while not self._stop.wait(5):
            now = time.time()
            step, last = min(now - last, 30), now          # never credit a sleep gap
            if not self._on("app_activity"):
                close()
                continue
            idle = collectors.idle_seconds() or 0
            if idle >= 120:
                key, app, title = ("(idle)",), "(idle)", ""
            else:
                aw = collectors.active_window() or {}
                app = (aw.get("application") or "unknown").lower()
                title = aw.get("title") or ""
                key = (app, title)
            seg = self._seg
            if seg and (seg[0] != key or seg[4] >= 300):
                close()
            with self._act_lock:
                if self._seg is None:
                    self._seg = [key, app, title, now - step, 0.0]
                self._seg[4] += step

    _CHROMIUM = {"chrome": r"Google\Chrome\User Data", "edge": r"Microsoft\Edge\User Data",
                 "brave": r"BraveSoftware\Brave-Browser\User Data", "vivaldi": r"Vivaldi\User Data",
                 "opera": r"Opera Software\Opera Stable"}

    def _web_loop(self) -> None:
        """Websites visited, from the browsers' own history databases (Chrome, Edge, Brave,
        Vivaldi, Opera, Firefox). Read every minute from a copy (the live file is locked).
        URLs are stored without query string/fragment (tokens, personal data)."""
        while not self._stop.wait(60):
            if not self._on("web_activity"):
                continue
            local = os.environ.get("LOCALAPPDATA", "")
            roaming = os.environ.get("APPDATA", "")
            for browser, rel in self._CHROMIUM.items():
                root = Path(roaming if browser == "opera" else local) / rel
                if not root.exists():
                    continue
                dbs = [root / "History"] if browser == "opera" else \
                    [p / "History" for p in root.iterdir() if p.is_dir() and
                     (p.name == "Default" or p.name.startswith("Profile "))]
                for db in dbs:
                    if db.exists():
                        self._read_history(browser, db, chromium=True)
            ff = Path(roaming) / "Mozilla" / "Firefox" / "Profiles"
            if ff.exists():
                for prof in ff.iterdir():
                    db = prof / "places.sqlite"
                    if db.exists():
                        self._read_history("firefox", db, chromium=False)

    def _read_history(self, browser: str, db: Path, chromium: bool) -> None:
        import shutil
        import sqlite3
        import tempfile
        from urllib.parse import urlsplit, urlunsplit
        key = f"web:{db}"
        last = self.cursor.get(key)
        tmp = Path(tempfile.gettempdir()) / f"voyager_hist_{os.getpid()}.db"
        try:
            shutil.copy2(db, tmp)
            con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            try:
                if chromium:     # visit_time: microseconds since 1601-01-01; visit_duration: microseconds
                    q = ("SELECT v.visit_time, u.url, u.title, v.visit_duration FROM visits v "
                         "JOIN urls u ON u.id = v.url WHERE v.visit_time > ? ORDER BY v.visit_time LIMIT 2000")
                else:            # Firefox visit_date: microseconds since 1970
                    q = ("SELECT v.visit_date, p.url, p.title, 0 FROM moz_historyvisits v "
                         "JOIN moz_places p ON p.id = v.place_id WHERE v.visit_date > ? ORDER BY v.visit_date LIMIT 2000")
                if last is None:  # first run: start from now, no back-fill of old history
                    row = con.execute("SELECT MAX(visit_time) FROM visits" if chromium
                                      else "SELECT MAX(visit_date) FROM moz_historyvisits").fetchone()
                    self.cursor[key] = (row[0] if row else 0) or 0
                    self._save_cursor()
                    return
                rows = con.execute(q, (last,)).fetchall()
            finally:
                con.close()
        except Exception as e:
            log.debug("History read %s: %s", db, e)
            return
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        newest = last
        for t, url, title, dur in rows:
            newest = max(newest, t)
            if not url or not url.startswith(("http://", "https://")):
                continue
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            if host.startswith("www."):
                host = host[4:]
            secs = (t / 1e6 - 11644473600) if chromium else t / 1e6
            self._add_activity({"event_type": "web", "domain": host, "application": browser,
                                "title": (title or "")[:400],
                                "duration_seconds": int((dur or 0) / 1e6),
                                "ts": datetime.fromtimestamp(secs, timezone.utc).isoformat(),
                                "meta": {"url": urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))[:500]}})
        if newest != last:
            self.cursor[key] = newest
            self._save_cursor()

    # ------------------------------------------------------------------ logins
    def _login_loop(self) -> None:
        user32 = ctypes.windll.user32
        locked = None
        last_tick = time.time()
        next_log_read = 0.0
        while not self._stop.wait(5):
            now = time.time()
            if not self._on("logins"):
                locked, last_tick = None, now
                continue
            # sleep/hibernate leaves a gap in this 5-second loop
            if now - last_tick > 120:
                gone = datetime.now(timezone.utc) - timedelta(seconds=now - last_tick)
                self.emit("login", "sleep", f"PC asleep/suspended ~{int((now - last_tick) // 60)} min",
                          ts=gone.isoformat())
                self.emit("login", "wake", "PC resumed")
            last_tick = now
            # locked = the input desktop is the secure (Winlogon) desktop
            h = user32.OpenInputDesktop(0, False, 0x0100)
            is_locked = not h
            if h:
                user32.CloseDesktop(h)
            if locked is not None and is_locked != locked:
                self.emit("login", "lock" if is_locked else "unlock",
                          "Screen locked" if is_locked else "Screen unlocked")
            locked = is_locked
            if now >= next_log_read:
                next_log_read = now + 60
                self._read_system_log()

    def _read_system_log(self) -> None:
        """Logon/logoff/startup/shutdown from the System event log (readable by normal users)."""
        import win32evtlog
        import win32security
        names = {7001: ("logon", "Logged on"), 7002: ("logoff", "Logged off"),
                 6005: ("startup", "Windows started"), 6006: ("shutdown", "Windows shut down"),
                 6008: ("unexpected_shutdown", "Unexpected shutdown (power loss / crash)"),
                 1074: ("shutdown_initiated", "Shutdown/restart requested"),
                 42: ("sleep", "PC going to sleep"), 1: ("wake", "PC woke up")}
        h = win32evtlog.OpenEventLog(None, "System")
        try:
            last = int(self.cursor.get("syslog_record") or 0)
            first_run = not last
            cutoff = datetime.now() - timedelta(hours=24)
            newest = last
            found = []
            flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
            done = False
            while not done:
                recs = win32evtlog.ReadEventLog(h, flags, 0)
                if not recs:
                    break
                for r in recs:
                    newest = max(newest, r.RecordNumber)
                    if r.RecordNumber <= last or (first_run and r.TimeGenerated < cutoff):
                        done = True
                        break
                    eid = r.EventID & 0xFFFF
                    src = (r.SourceName or "").lower()
                    if eid not in names:
                        continue
                    if eid in (1,) and "power-troubleshooter" not in src:
                        continue
                    if eid == 42 and "kernel-power" not in src:
                        continue
                    if eid in (7001, 7002) and "winlogon" not in src:
                        continue
                    found.append((r, eid))
            for r, eid in reversed(found):
                etype, text = names[eid]
                user = None
                ins = list(r.StringInserts or [])
                if eid in (7001, 7002) and len(ins) > 1:
                    try:
                        sid = win32security.ConvertStringSidToSid(ins[1])
                        n, d, _ = win32security.LookupAccountSid(None, sid)
                        user = f"{d}\\{n}"
                    except Exception:
                        user = ins[1]
                if eid == 1074 and len(ins) >= 7:
                    text = f"{ins[4].capitalize()} requested by {ins[6]} via {os.path.basename(ins[0])}"
                    user = ins[6]
                ts = r.TimeGenerated.replace(tzinfo=None).astimezone(timezone.utc).isoformat()
                self.emit("login", etype, text, user=user or "", ts=ts, meta={"event_id": eid})
            if newest != last:
                self.cursor["syslog_record"] = newest
                self._save_cursor()
        finally:
            win32evtlog.CloseEventLog(h)

    # ------------------------------------------------------------------ network / Wi-Fi
    def _wifi(self) -> dict | None:
        try:
            out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True,
                                 timeout=10, creationflags=_NO_WINDOW).stdout or ""
        except Exception:
            return None
        if "There is no wireless interface" in out or not out.strip():
            return None
        info = {}
        for line in out.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip().lower()
                if k not in info:
                    info[k] = v.strip()
        state = (info.get("state") or "").lower()
        return {"connected": state == "connected", "ssid": info.get("ssid") or "",
                "bssid": info.get("bssid") or info.get("ap bssid") or "", "signal": info.get("signal") or "",
                "radio": info.get("radio type") or "", "auth": info.get("authentication") or "",
                "location_blocked": "location" in out.lower() and not info.get("ssid")}

    @staticmethod
    def _internet() -> bool:
        for host in ("8.8.8.8", "1.1.1.1"):
            try:
                socket.create_connection((host, 53), timeout=3).close()
                return True
            except OSError:
                continue
        return False

    @staticmethod
    def _adapters() -> dict:
        import psutil
        stats, addrs = psutil.net_if_stats(), psutil.net_if_addrs()
        out = {}
        for name, st in stats.items():
            low = name.lower()
            if "loopback" in low or "pseudo" in low or low.startswith("vethernet") or "isatap" in low:
                continue
            ips = [a.address for a in addrs.get(name, []) if a.family == socket.AF_INET]
            out[name] = {"up": st.isup, "ip": ",".join(ips)}
        return out

    def _network_loop(self) -> None:
        prev_ad, prev_wifi, prev_net = None, None, None
        while not self._stop.wait(15):
            if self._on("network"):
                ad = self._adapters()
                if prev_ad is not None:
                    for name, cur in ad.items():
                        old = prev_ad.get(name)
                        if old is None or old["up"] != cur["up"]:
                            if old is not None or cur["up"]:
                                self.emit("network", "adapter_up" if cur["up"] else "adapter_down",
                                          f"{name} {'connected' if cur['up'] else 'disconnected'}"
                                          + (f" ({cur['ip']})" if cur["up"] and cur["ip"] else ""),
                                          target=name)
                        elif cur["up"] and cur["ip"] and old["ip"] != cur["ip"]:
                            self.emit("network", "ip_changed", f"{name} IP {old['ip'] or '-'} → {cur['ip']}",
                                      target=name, meta={"old": old["ip"], "new": cur["ip"]})
                prev_ad = ad
                net = self._internet()
                if prev_net is not None and net != prev_net:
                    self.emit("network", "internet_restored" if net else "internet_lost",
                              "Internet connection restored" if net else "Internet connection lost")
                elif prev_net is None:
                    self.emit("network", "status", "Internet " + ("available" if net else "unavailable")
                              + "; adapters up: " + (", ".join(n for n, a in ad.items() if a["up"]) or "none"))
                prev_net = net
            else:
                prev_ad, prev_net = None, None
            if self._on("wifi"):
                w = self._wifi()
                if w is not None:
                    key = (w["connected"], w["ssid"])
                    if prev_wifi is None or key != prev_wifi:
                        if w["connected"]:
                            name = w["ssid"] or ("(name hidden — Windows location is off)"
                                                 if w["location_blocked"] else "(unknown)")
                            etype = "wifi_changed" if prev_wifi and prev_wifi[0] else "wifi_connected"
                            self.emit("network", etype, f"Wi-Fi {name} signal {w['signal']} {w['radio']}".strip(),
                                      target=w["ssid"] or None,
                                      meta={k: w[k] for k in ("bssid", "signal", "radio", "auth")})
                        elif prev_wifi is not None:
                            self.emit("network", "wifi_disconnected",
                                      f"Wi-Fi disconnected from {prev_wifi[1] or 'network'}", target=prev_wifi[1] or None)
                    prev_wifi = key
            else:
                prev_wifi = None

    # ------------------------------------------------------------------ USB
    @staticmethod
    def _usb_drives() -> dict:
        """Removable drives and USB-attached disks: {"E:\\": "SanDisk (E:)"}."""
        k32 = ctypes.windll.kernel32
        out = {}
        mask = k32.GetLogicalDrives()
        for i in range(26):
            if not mask & (1 << i):
                continue
            root = f"{chr(65 + i)}:\\"
            dtype = k32.GetDriveTypeW(root)
            if dtype == 2 or (dtype == 3 and i > 2 and EventTracker._bus_is_usb(chr(65 + i))):
                label = ctypes.create_unicode_buffer(261)
                k32.GetVolumeInformationW(root, label, 261, None, None, None, None, 0)
                out[root] = f"{label.value or 'USB drive'} ({root[:2]})"
        return out

    @staticmethod
    def _bus_is_usb(letter: str) -> bool:
        """External USB hard disks report as 'fixed'; check the storage bus type."""
        import ctypes.wintypes as wt
        k32 = ctypes.windll.kernel32
        h = k32.CreateFileW(f"\\\\.\\{letter}:", 0, 3, None, 3, 0, None)
        if h in (0, -1, ctypes.c_void_p(-1).value):
            return False
        try:
            query = (ctypes.c_ubyte * 12)()                       # StorageDeviceProperty, standard query
            buf = (ctypes.c_ubyte * 1024)()
            ret = wt.DWORD()
            ok = k32.DeviceIoControl(h, 0x2D1400, query, 12, buf, 1024, ctypes.byref(ret), None)
            return bool(ok) and int.from_bytes(bytes(buf[28:32]), "little") == 7      # BusTypeUsb
        finally:
            k32.CloseHandle(h)

    def _watch(self, root: str, on_file, alive) -> None:
        """Recursive change watcher; calls on_file(path) for new/changed files.

        Uses overlapped ReadDirectoryChangesW and waits in 1 s slices: the blocking form holds
        Python's GIL and would freeze every other agent thread until a change happens."""
        import pywintypes
        import win32con
        import win32event
        import win32file
        h = win32file.CreateFile(root, 0x0001, 7, None, win32con.OPEN_EXISTING,
                                 win32con.FILE_FLAG_BACKUP_SEMANTICS | win32con.FILE_FLAG_OVERLAPPED, None)
        ov = pywintypes.OVERLAPPED()
        ov.hEvent = win32event.CreateEvent(None, True, False, None)
        buf = win32file.AllocateReadBuffer(65536)
        flt = win32con.FILE_NOTIFY_CHANGE_FILE_NAME | win32con.FILE_NOTIFY_CHANGE_SIZE
        try:
            while alive() and not self._stop.is_set():
                win32event.ResetEvent(ov.hEvent)
                try:
                    win32file.ReadDirectoryChangesW(h, buf, True, flt, ov)
                except pywintypes.error:
                    return                                          # drive removed / folder gone
                while alive() and not self._stop.is_set():
                    if win32event.WaitForSingleObject(ov.hEvent, 1000) == win32event.WAIT_OBJECT_0:
                        break
                else:
                    return
                try:
                    n = win32file.GetOverlappedResult(h, ov, True)
                except pywintypes.error:
                    return
                for action, rel in win32file.FILE_NOTIFY_INFORMATION(buf, n) if n else []:
                    if action in (1, 3, 5):                         # added / modified / renamed-to
                        on_file(os.path.join(root, rel))
        finally:
            try:
                win32file.CancelIo(h)
            except Exception:
                pass
            win32file.CloseHandle(h)

    def _usb_loop(self) -> None:
        drives: dict = {}
        index: dict = {}              # (name.lower(), size) -> drive label, for copies FROM usb
        pending: dict = {}            # path -> (direction, label, first_seen, last_size)
        reported: dict = {}           # path -> time (de-duplicate)
        plock = threading.Lock()
        home_watch = {"on": False}

        home = os.environ.get("USERPROFILE") or ""
        skip = (os.path.join(home, "AppData").lower() + "\\",) if home else ()   # app/browser cache noise

        def stage(path, direction, label):
            low = path.lower()
            if not self._tracked(path) or "\\$recycle.bin" in low or (skip and low.startswith(skip)):
                return
            with plock:
                if path not in pending:
                    pending[path] = (direction, label, time.time(), -1)

        def on_usb_file(label):
            return lambda p: stage(p, "to", label)

        def on_home_file(p):
            if drives:
                stage(p, "from", None)

        while not self._stop.wait(3):
            if not self._on("usb_files"):
                continue
            cur = self._usb_drives()
            for root, label in cur.items():
                if root not in drives:
                    try:
                        import shutil
                        tot = shutil.disk_usage(root).total
                        size = f"{tot / 1e9:.1f} GB"
                    except Exception:
                        size = "?"
                    self.emit("usb", "usb_inserted", f"USB drive inserted: {label}, {size}", target=label)
                    drives[root] = label
                    alive = (lambda r=root: r in drives)
                    threading.Thread(target=self._guard_watch, args=(root, on_usb_file(label), alive),
                                     daemon=True).start()
                    threading.Thread(target=self._index_drive, args=(root, label, index), daemon=True).start()
            for root in list(drives):
                if root not in cur:
                    self.emit("usb", "usb_removed", f"USB drive removed: {drives[root]}", target=drives[root])
                    lbl = drives.pop(root)
                    for k in [k for k, v in index.items() if v == lbl]:
                        index.pop(k, None)
            if drives and not home_watch["on"]:
                home_watch["on"] = True
                if home:
                    threading.Thread(target=self._guard_watch,
                                     args=(home, on_home_file, lambda: self._on("usb_files")), daemon=True).start()
            # finalize copies whose size stopped changing
            now = time.time()
            with plock:
                items = list(pending.items())
            for path, (direction, label, first, last_size) in items:
                try:
                    size = os.path.getsize(path)
                except OSError:
                    with plock:
                        pending.pop(path, None)
                    continue
                if size != last_size or now - first < 3:
                    with plock:
                        pending[path] = (direction, label, first, size)
                    continue
                with plock:
                    pending.pop(path, None)
                if now - reported.get(path, 0) < 120:
                    continue
                name = os.path.basename(path)
                if direction == "from":
                    label = index.get((name.lower(), size))
                    if not label:
                        continue                                    # not a copy from an attached USB
                reported[path] = now
                ext = os.path.splitext(name)[1].lower()
                if direction == "to":
                    self.emit("usb", "copied_to_usb", f"{name} copied to {label}", file_name=name,
                              file_ext=ext, file_size=size, target=label, meta={"path": path})
                    index[(name.lower(), size)] = label
                else:
                    self.emit("usb", "copied_from_usb", f"{name} copied from {label} to {path}",
                              file_name=name, file_ext=ext, file_size=size, target=label, meta={"path": path})
            for p in [p for p, t in reported.items() if now - t > 600]:
                reported.pop(p, None)

    def _guard_watch(self, root, cb, alive) -> None:
        try:
            self._watch(root, cb, alive)
        except Exception as e:
            log.info("Watcher for %s stopped: %s", root, e)

    def _index_drive(self, root: str, label: str, index: dict) -> None:
        """Names+sizes of tracked files on a USB drive, to recognise copies FROM it."""
        n, t0 = 0, time.time()
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if self._tracked(f):
                    try:
                        index[(f.lower(), os.path.getsize(os.path.join(dirpath, f)))] = label
                    except OSError:
                        pass
                n += 1
                if n > 50000 or time.time() - t0 > 30:
                    return

    # ------------------------------------------------------------------ email
    def _email_loop(self) -> None:
        last_outlook = 0.0
        title_hist: list = []            # (time, exe, title) of recent foreground windows
        while not self._stop.wait(2):
            if not self._on("email_files"):
                continue
            fg = self._foreground()
            if fg:
                title_hist.append((time.time(), *fg))
                title_hist[:] = [x for x in title_hist if time.time() - x[0] < 60][-60:]
            try:
                self._check_dialog_mru(title_hist)
            except Exception as e:
                log.debug("MRU check: %s", e)
            if time.time() - last_outlook > 60:
                last_outlook = time.time()
                try:
                    self._check_outlook()
                except Exception as e:
                    log.info("Outlook check skipped: %s", e)

    @staticmethod
    def _foreground():
        try:
            import psutil
            import win32gui
            import win32process
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd) or ""
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            return psutil.Process(pid).name().lower(), title
        except Exception:
            return None

    def _check_dialog_mru(self, title_hist) -> None:
        """Webmail (best effort): a tracked file picked in a browser's file dialog while a
        webmail page was in front. Windows records each pick under ComDlg32\\OpenSavePidlMRU."""
        import winreg
        base = r"Software\Microsoft\Windows\CurrentVersion\Explorer\ComDlg32\OpenSavePidlMRU"
        seen = self.cursor.setdefault("mru", {})
        changed = False
        for ext in self.settings.get("file_types") or []:
            try:
                k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{base}\\{ext.lstrip('.')}")
            except OSError:
                continue
            with k:
                try:
                    order, _ = winreg.QueryValueEx(k, "MRUListEx")
                    first = int.from_bytes(order[:4], "little")
                    pidl, _ = winreg.QueryValueEx(k, str(first))
                except OSError:
                    continue
            sig = f"{first}:{hashlib.sha1(bytes(pidl)).hexdigest()[:16]}"   # stable across restarts
            if seen.get(ext) == sig:
                continue
            first_time = ext not in seen
            seen[ext] = sig
            changed = True
            if first_time:
                continue                                        # baseline, not a new pick
            path = self._pidl_path(bytes(pidl))
            if not path:
                continue
            recent = [x for x in title_hist if time.time() - x[0] < 45]
            mail = next((x for x in reversed(recent) if x[1] in _BROWSERS and
                         any(w in x[2].lower() for w in _WEBMAIL)), None)
            if not mail:
                continue
            name = os.path.basename(path)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = None
            site = mail[2][:120]
            self.emit("email", "email_attached",
                      f"{name} chosen for upload in webmail ({site}) — send not confirmed",
                      file_name=name, file_ext=os.path.splitext(name)[1].lower(), file_size=size,
                      target=site, meta={"browser": mail[1], "path": path})
        if changed:
            self._save_cursor()

    @staticmethod
    def _pidl_path(pidl: bytes) -> str | None:
        buf = ctypes.create_string_buffer(pidl, len(pidl) + 2)
        out = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.shell32.SHGetPathFromIDListW(buf, out):
            return out.value
        return None

    def _check_outlook(self) -> None:
        """Outlook desktop: sent items with tracked attachments (only if Outlook is running)."""
        import psutil
        if not any((p.info.get("name") or "").lower() == "outlook.exe"
                   for p in psutil.process_iter(["name"])):
            return
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            ol = win32com.client.GetActiveObject("Outlook.Application")
            items = ol.GetNamespace("MAPI").GetDefaultFolder(5).Items     # olFolderSentMail
            items.Sort("[SentOn]", True)
            last = self.cursor.get("outlook_sent")
            newest = last
            if last is None:                                             # start now; no history
                self.cursor["outlook_sent"] = datetime.now().isoformat()
                self._save_cursor()
                return
            last_dt = datetime.fromisoformat(last)
            for i, it in enumerate(items):
                if i >= 100:
                    break
                try:
                    sent = datetime.fromisoformat(str(it.SentOn)[:19])
                except Exception:
                    continue
                if sent <= last_dt:
                    break
                newest = max(newest, sent.isoformat())
                att = it.Attachments
                for j in range(1, att.Count + 1):
                    a = att.Item(j)
                    name = a.FileName or ""
                    if not self._tracked(name):
                        continue
                    to = (it.To or "")[:380]
                    self.emit("email", "email_sent", f"{name} sent by Outlook to {to}",
                              file_name=name, file_ext=os.path.splitext(name)[1].lower(),
                              file_size=getattr(a, "Size", None), target=to,
                              ts=sent.astimezone(timezone.utc).isoformat(),
                              meta={"subject": (it.Subject or "")[:200], "cc": (it.CC or "")[:200]})
            if newest != last:
                self.cursor["outlook_sent"] = newest
                self._save_cursor()
        finally:
            pythoncom.CoUninitialize()
