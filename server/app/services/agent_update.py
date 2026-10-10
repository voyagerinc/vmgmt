"""Agent update policy: which agent version computers may update to, and when.

The build stamps its version at the end of VoyagerAgent.exe (VOYAGER_AGENT_VERSION:x.y.z), so
the license server and client servers know the version without running the .exe. Per company:
  auto              — every computer updates as soon as a newer agent is available
  approved_version  — "Update all agents now" approves that version for every computer
  devices           — "Update" on single computers approves them for the current version
Installed agents (Program Files) are updated by a SYSTEM task on the PC; per-user installs by
the agent itself. Both ask /api/updates/agent-info whether an update is approved for them.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import BASE_DIR
from ..models import Device
from . import settings_service as ss

AGENT_EXE = BASE_DIR / "agent_dist" / "VoyagerAgent.exe"
KEY = "agent_update"
_VER_RE = re.compile(rb"VOYAGER_AGENT_VERSION:([0-9][0-9A-Za-z.\-]{0,30}):END_VOYAGER_AGENT_VERSION")
_cache: dict = {}


def vtuple(v) -> tuple:
    try:
        return tuple(int(x) for x in str(v).split(".")[:3])
    except Exception:
        return (0,)


def exe_info(path: Path = AGENT_EXE) -> dict | None:
    """{version, sha256, size} of the staged agent (cached by size+mtime)."""
    if not path.exists():
        return None
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime)
    if _cache.get("key") == key:
        return _cache["info"]
    with open(path, "rb") as f:
        f.seek(max(0, st.st_size - 65536))
        tail = f.read()
    m = list(_VER_RE.finditer(tail))
    version = m[-1].group(1).decode() if m else None
    if not version:                                     # older builds: version.json next to the exe
        try:
            version = json.loads((path.parent / "version.json").read_text(encoding="utf-8")).get("agent")
        except Exception:
            version = None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    info = {"version": version, "sha256": h.hexdigest(), "size": st.st_size}
    _cache.update(key=key, info=info)
    return info


def get_policy(db: Session, tenant_id: str) -> dict:
    p = ss.get_setting(db, KEY, tenant_id) or {}
    return {"auto": bool(p.get("auto", False)), "approved_version": p.get("approved_version"),
            "devices": list(p.get("devices") or [])}


def set_policy(db: Session, tenant_id: str, **changes) -> dict:
    p = {**get_policy(db, tenant_id), **changes}
    ss.set_setting(db, KEY, tenant_id, p)
    return p


def approved(db: Session, device: Device | None, version: str | None) -> bool:
    if not device or not version:
        return False
    p = get_policy(db, device.tenant_id)
    return p["auto"] or p["approved_version"] == version or device.id in p["devices"]


def needs_update(device: Device, version: str | None) -> bool:
    return bool(version) and vtuple(version) > vtuple(device.agent_version or "0")


_last_sync = {"t": 0.0}


def sync_from_license_server() -> None:
    """Client server: refresh the staged agent from the license server (at most every 5 min)."""
    if time.time() - _last_sync["t"] < 300:
        return
    _last_sync["t"] = time.time()
    try:
        from ..routers.downloads import _fetch_agent_exe
        _fetch_agent_exe()
    except Exception:
        pass
