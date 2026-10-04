"""Pre-flight diagnostic for the Windows Management Server.
Run with: python doctor.py
Exit code 0 means the application imports and database mappings are healthy.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import platform
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0

def ok(msg):
    global PASS
    PASS += 1
    print("[PASS] " + msg)

def fail(msg):
    global FAIL
    FAIL += 1
    print("[FAIL] " + msg)

print("=" * 72)
print("Endpoint Management Server - Windows Pre-flight Doctor")
print("=" * 72)
print("OS      :", platform.platform())
print("Python  :", sys.version.replace("\n", " "))
print("Arch    :", platform.architecture()[0])
print("Folder  :", ROOT)
print()

if sys.version_info < (3, 8):
    fail("Python 3.8 or newer is required.")
else:
    ok("Python version is supported by the compatibility build.")

if sys.platform != "win32":
    print("[INFO] This package is optimized for Windows; continuing anyway.")

# Core imports
packages = [
    ("fastapi", "FastAPI"),
    ("uvicorn", "Uvicorn"),
    ("sqlalchemy", "SQLAlchemy"),
    ("pydantic", "Pydantic"),
    ("pydantic_settings", "Pydantic Settings"),
    ("passlib", "Passlib"),
    ("argon2", "Argon2"),
    ("jwt", "PyJWT"),
    ("cryptography", "Cryptography"),
    ("apscheduler", "APScheduler"),
    ("reportlab", "ReportLab"),
    ("openpyxl", "OpenPyXL"),
    ("email_validator", "Email Validator"),
]
for mod, label in packages:
    try:
        importlib.import_module(mod)
        try:
            v = importlib.metadata.version(mod.replace("_", "-"))
        except Exception:
            v = "installed"
        ok("%s: %s" % (label, v))
    except Exception as exc:
        fail("%s import failed: %s" % (label, exc))

# pywin32 is required for Windows service mode
if sys.platform == "win32":
    for mod in ("win32serviceutil", "win32service", "win32event", "servicemanager"):
        try:
            importlib.import_module(mod)
            ok("pywin32 module: " + mod)
        except Exception as exc:
            fail("pywin32 module %s failed: %s" % (mod, exc))

# Application imports are deliberately tested in dependency order.
try:
    from app import models  # noqa
    ok("SQLAlchemy models import successfully.")
except Exception as exc:
    fail("SQLAlchemy models failed: %s" % exc)

try:
    from app import schemas  # noqa
    ok("Pydantic schemas import successfully.")
except Exception as exc:
    fail("Pydantic schemas failed: %s" % exc)

try:
    from app.main import app
    ok("FastAPI application imports successfully.")
except Exception as exc:
    fail("FastAPI application failed: %s" % exc)

try:
    from app.database import init_db
    init_db()
    ok("Database initialization/migrations completed.")
except Exception as exc:
    fail("Database initialization failed: %s" % exc)

# Port check: occupied is OK if a server is already running.
try:
    from app.config import settings
    host = settings.host
    port = settings.port
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    result = s.connect_ex((probe_host, port))
    s.close()
    if result == 0:
        ok("Port %s is already accepting connections." % port)
    else:
        ok("Port %s is available for the server." % port)
except Exception as exc:
    fail("Port check failed: %s" % exc)

print()
print("=" * 72)
print("RESULT: %d PASS / %d FAIL" % (PASS, FAIL))
print("=" * 72)
if FAIL:
    print("Fix the [FAIL] items above. The server was NOT started.")
    raise SystemExit(1)
print("READY: the server can be started.")
