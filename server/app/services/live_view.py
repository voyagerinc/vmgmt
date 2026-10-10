"""Live screen view: low-latency frames kept in memory only (never written to disk/DB).

Flow: the console calls /api/live/{device}/start every few seconds while its window is open,
which marks the device "wanted" with a short TTL. The agent polls /api/agents/live-check often;
while wanted it captures a JPEG every `interval_ms` and POSTs it to /api/agents/live-frame. The
console polls /api/live/{device}/frame for the newest frame. Frames expire quickly and are only
ever held for devices an admin is actively watching.
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_wanted: dict[str, float] = {}          # device_id -> watch-session expiry (epoch seconds)
_frames: dict[str, tuple[float, bytes]] = {}   # device_id -> (captured_epoch, jpeg bytes)

WATCH_TTL = 15          # a device is streamed for this long after the last console keepalive
FRAME_TTL = 20          # drop a stored frame older than this
DEFAULT_INTERVAL_MS = 1000
DEFAULT_QUALITY = 45
DEFAULT_MAX_WIDTH = 1280
_settings: dict[str, dict] = {}         # per-device live settings (quality/width/monitor)


def want(device_id: str, settings: dict | None = None) -> None:
    with _lock:
        _wanted[device_id] = time.time() + WATCH_TTL
        if settings:
            _settings[device_id] = settings


def stop(device_id: str) -> None:
    with _lock:
        _wanted.pop(device_id, None)
        _frames.pop(device_id, None)


def is_wanted(device_id: str) -> bool:
    with _lock:
        return _wanted.get(device_id, 0) > time.time()


def live_config(device_id: str) -> dict:
    with _lock:
        active = _wanted.get(device_id, 0) > time.time()
        s = _settings.get(device_id, {})
    return {"active": active,
            "interval_ms": int(s.get("interval_ms", DEFAULT_INTERVAL_MS)),
            "quality": int(s.get("quality", DEFAULT_QUALITY)),
            "max_width": int(s.get("max_width", DEFAULT_MAX_WIDTH)),
            "monitor": int(s.get("monitor", 0))}


def put_frame(device_id: str, data: bytes) -> None:
    with _lock:
        _frames[device_id] = (time.time(), data)


def get_frame(device_id: str) -> tuple[float, bytes] | None:
    with _lock:
        f = _frames.get(device_id)
        if f and time.time() - f[0] <= FRAME_TTL:
            return f
        _frames.pop(device_id, None)
        return None


def sweep() -> None:
    """Drop expired watch sessions and frames (called from the maintenance tick)."""
    now = time.time()
    with _lock:
        for d in [d for d, exp in _wanted.items() if exp <= now]:
            _wanted.pop(d, None)
        for d in [d for d, (t, _) in _frames.items() if now - t > FRAME_TTL]:
            _frames.pop(d, None)
