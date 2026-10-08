"""Central configuration (PRD §6 deployment models are configuration-driven)."""
from __future__ import annotations

import os
import secrets
import sys
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# When running as a packaged .exe (PyInstaller), persist data/.env/logs NEXT TO the exe
# — not inside the temporary _MEIPASS extraction, which is wiped after each run.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent       # folder containing ManagementServer.exe
else:
    BASE_DIR = Path(__file__).resolve().parent.parent      # .../server
# EMP_DATA_DIR overrides where the DB/keys/evidence live (lets multiple instances run isolated).
_data_override = os.environ.get("EMP_DATA_DIR")
DATA_DIR = Path(_data_override) if _data_override else BASE_DIR / "data"
EVIDENCE_DIR = DATA_DIR / "evidence"
LOG_DIR = (DATA_DIR / "logs") if _data_override else BASE_DIR / "logs"
for _d in (DATA_DIR, EVIDENCE_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Without a console (pythonw.exe background start, Windows service) sys.stdout/stderr are None
# and uvicorn crashes at startup on sys.stdout.isatty(). Send output to logs/server.log instead.
if sys.stdout is None or sys.stderr is None:
    try:
        _console_log = open(LOG_DIR / "server.log", "a", encoding="utf-8", buffering=1)
    except Exception:
        _console_log = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = _console_log
    if sys.stderr is None:
        sys.stderr = _console_log

DEFAULT_LICENSE_SERVER = "http://vmgmt.voyager.co.in:8084"


def _detect_lan_ip() -> str:
    """Best-effort LAN IP of this machine (the address agents on other PCs will use)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))          # UDP: no packet is sent, only picks the route
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def _ensure_client_env() -> None:
    """On-premise client servers (Windows installs): create server/.env on first start so the
    server listens on the LAN, agents get a reachable URL and activation/UPDATE.bat know the
    license server. Never overwrites an existing .env; skipped on the Linux cloud license
    server and in a source checkout."""
    env_file = BASE_DIR / ".env"
    if os.name != "nt" or (BASE_DIR.parent / ".git").exists():
        return
    if env_file.exists():
        # earlier builds wrote the wrong license-server port (9084 is the client's own port)
        try:
            txt = env_file.read_text(encoding="utf-8")
            if "vmgmt.voyager.co.in:9084" in txt:
                env_file.write_text(txt.replace("vmgmt.voyager.co.in:9084", "vmgmt.voyager.co.in:8084"),
                                    encoding="utf-8")
                print(f"[config] Fixed license server port in {env_file} (9084 -> 8084)")
        except Exception:
            pass
        return
    ip = _detect_lan_ip()
    try:
        env_file.write_text(
            "# Created automatically on first start. Edit and restart the server to change.\n"
            "EMP_DEPLOYMENT_MODEL=on_premise\n"
            "EMP_HOST=0.0.0.0\n"
            "EMP_PORT=9084\n"
            f"# Address agents on employee PCs connect to (auto-detected: {ip})\n"
            f"EMP_SERVER_PUBLIC_URL=http://{ip}:9084\n"
            f"EMP_LICENSE_SERVER={DEFAULT_LICENSE_SERVER}\n",
            encoding="utf-8")
        print(f"[config] Created {env_file} (server URL http://{ip}:9084)")
    except Exception as e:
        print(f"[config] Could not create {env_file}: {e}")


_ensure_client_env()


class Settings(BaseSettings):
    """All settings overridable via environment or server/.env file."""

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_prefix="EMP_", extra="ignore")

    # --- Identity / deployment ---------------------------------------------
    app_name: str = "Voyager Endpoint Management Platform"
    deployment_model: str = "on_premise"        # on_premise | cloud_vps | hybrid | central_saas
    server_public_url: str = "http://127.0.0.1:9084"
    host: str = "127.0.0.1"
    port: int = 9084

    # --- Database -----------------------------------------------------------
    # SQLite by default (zero-complexity). Set EMP_DATABASE_URL to a Postgres DSN for production.
    database_url: str = f"sqlite:///{(DATA_DIR / 'management.db').as_posix()}"

    # --- Security -----------------------------------------------------------
    # A stable secret is generated into .secret on first run if not provided.
    secret_key: str = ""
    access_token_minutes: int = 60
    refresh_token_days: int = 14
    password_min_length: int = 10
    max_failed_logins: int = 8
    lockout_minutes: int = 15
    require_mfa: bool = False
    # Fixed first-run Super Admin password for every new server (EMP_DEFAULT_ADMIN_PASSWORD overrides).
    default_admin_password: str = "Vmgmt@99887766778899"

    # Evidence encryption key (Fernet). Auto-generated into .evidence_key on first run.
    evidence_key: str = ""

    # --- Licensing (on-prem servers activate against the cloud license server) ---
    # e.g. http://vmgmt.voyager.co.in:8084 (leave blank on the cloud license server itself)
    license_server: str = ""

    def resolve_license_server(self) -> str:
        srv = (self.license_server or "").strip()
        if not srv:
            srv = DEFAULT_LICENSE_SERVER
        if not srv.startswith("http://") and not srv.startswith("https://"):
            srv = "http://" + srv
        return srv.rstrip("/")

    # --- Agent / enrollment -------------------------------------------------
    enroll_token_ttl_hours: int = 72
    heartbeat_interval_seconds: int = 60
    offline_after_seconds: int = 300            # device considered offline (PRD §15)

    # --- Retention (PRD §24.2) ---------------------------------------------
    default_evidence_retention_days: int = 30
    default_event_retention_days: int = 90

    # --- Email / SMTP (PRD §23 notifications) ------------------------------
    # If SMTP host is unset, emails are written to data/outbox/*.eml instead of sent,
    # so the flow is fully testable without a mail server.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_from: str = "no-reply@endpointmgmt.local"
    smtp_from_name: str = "Endpoint Management Platform"

    # --- Self-update (admin "Pull & restart") ------------------------------
    # Service name used to restart the server. Defaults per-platform if blank.
    service_name: str = ""          # EMP_SERVICE_NAME, e.g. "vmgmt" (Linux) / "EndpointMgmtServer"
    allow_self_update: bool = True  # set false to disable the admin update button entirely

    # --- CORS ---------------------------------------------------------------
    cors_origins: str = "*"

    def resolve_secret(self) -> str:
        if self.secret_key:
            return self.secret_key
        f = DATA_DIR / ".secret"
        if not f.exists():
            f.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        return f.read_text(encoding="utf-8").strip()

    def resolve_evidence_key(self) -> bytes:
        from cryptography.fernet import Fernet
        if self.evidence_key:
            return self.evidence_key.encode()
        f = DATA_DIR / ".evidence_key"
        if not f.exists():
            f.write_text(Fernet.generate_key().decode(), encoding="utf-8")
        return f.read_text(encoding="utf-8").strip().encode()


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
