"""Runtime settings resolution (SMTP etc.), DB-backed with env fallback (PRD §23)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import settings as env_settings
from ..models import Setting

SMTP_KEY = "smtp"

# fields that are safe to return to the UI (password is write-only)
SMTP_PUBLIC_FIELDS = ("host", "port", "user", "use_tls", "from_addr", "from_name", "configured")


def get_setting(db: Session, key: str, tenant_id: str | None) -> dict | None:
    row = db.query(Setting).filter(Setting.key == key, Setting.tenant_id == tenant_id).first()
    return row.value if row else None


def set_setting(db: Session, key: str, tenant_id: str | None, value: dict) -> Setting:
    row = db.query(Setting).filter(Setting.key == key, Setting.tenant_id == tenant_id).first()
    if row:
        merged = {**(row.value or {}), **value}
        row.value = merged
    else:
        row = Setting(key=key, tenant_id=tenant_id, value=value)
        db.add(row)
    db.flush()
    return row


def resolve_smtp(db: Session, tenant_id: str | None = None) -> dict:
    """Effective SMTP config: tenant override -> platform global -> env defaults."""
    cfg = dict(host=env_settings.smtp_host, port=env_settings.smtp_port,
               user=env_settings.smtp_user, password=env_settings.smtp_password,
               use_tls=env_settings.smtp_use_tls, from_addr=env_settings.smtp_from,
               from_name=env_settings.smtp_from_name)
    for scope in (None, tenant_id) if tenant_id else (None,):
        row = get_setting(db, SMTP_KEY, scope)
        if row:
            for k in ("host", "port", "user", "password", "use_tls", "from_addr", "from_name"):
                if row.get(k) not in (None, ""):
                    cfg[k] = row[k]
    cfg["configured"] = bool(cfg.get("host"))
    return cfg


def public_smtp(db: Session, tenant_id: str | None) -> dict:
    cfg = resolve_smtp(db, tenant_id)
    return {k: cfg.get(k) for k in SMTP_PUBLIC_FIELDS}
