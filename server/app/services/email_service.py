"""Email delivery (PRD §23). SMTP when configured, else a local outbox for dev/testing.

Supports file attachments so the generated license key can be mailed to the registered
customer address. When no SMTP host is configured, the full message (with attachments)
is written to data/outbox/ as a .eml file and the function reports delivered=False so the
caller can surface a "download instead" path.
"""
from __future__ import annotations

import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from ..config import DATA_DIR, settings

OUTBOX = DATA_DIR / "outbox"
OUTBOX.mkdir(parents=True, exist_ok=True)


def verify_smtp(cfg: dict) -> dict:
    """Connect + authenticate (no send) to validate credentials. Returns {ok, error}."""
    if not cfg.get("host"):
        return {"ok": False, "error": "No SMTP host configured — emails save to the local outbox."}
    try:
        host, port = cfg["host"], int(cfg.get("port", 587))
        user, pw = cfg.get("user"), cfg.get("password")
        if cfg.get("use_tls", True):
            ctx = ssl.create_default_context()
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.ehlo()
                s.starttls(context=ctx)
                s.ehlo()
                if user:
                    s.login(user, pw)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                if user:
                    s.login(user, pw)
        return {"ok": True, "error": None}
    except smtplib.SMTPAuthenticationError as e:
        return {"ok": False, "error": f"Authentication failed — check username/password. ({e.smtp_code})"}
    except smtplib.SMTPConnectError as e:
        return {"ok": False, "error": f"Could not connect to {cfg.get('host')}:{cfg.get('port')} ({e})"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def send_email(to: str, subject: str, body: str,
               attachments: list[tuple[str, bytes, str]] | None = None,
               smtp: dict | None = None) -> dict:
    """Return {"delivered": bool, "via": "smtp"|"outbox", "detail": str}.

    attachments: list of (filename, content_bytes, mime_subtype e.g. "json"/"plain").
    smtp: resolved config from settings_service.resolve_smtp(); falls back to env settings.
    """
    cfg = smtp or dict(host=settings.smtp_host, port=settings.smtp_port, user=settings.smtp_user,
                       password=settings.smtp_password, use_tls=settings.smtp_use_tls,
                       from_addr=settings.smtp_from, from_name=settings.smtp_from_name)
    msg = EmailMessage()
    msg["From"] = f"{cfg.get('from_name','')} <{cfg.get('from_addr')}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    for (fname, content, subtype) in attachments or []:
        maintype = "text" if subtype in ("plain", "json", "csv") else "application"
        if maintype == "text":
            msg.add_attachment(content.decode("utf-8", "replace"), subtype=subtype, filename=fname)
        else:
            msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=fname)

    if not cfg.get("host"):
        safe = "".join(c for c in to if c.isalnum() or c in "@._-")
        fname = OUTBOX / f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}_{safe}.eml"
        fname.write_bytes(bytes(msg))
        return {"delivered": False, "via": "outbox", "detail": str(fname)}

    try:
        host, port = cfg["host"], int(cfg.get("port", 587))
        user, pw = cfg.get("user"), cfg.get("password")
        if cfg.get("use_tls", True):
            ctx = ssl.create_default_context()
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls(context=ctx)
                if user:
                    s.login(user, pw)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                if user:
                    s.login(user, pw)
                s.send_message(msg)
        return {"delivered": True, "via": "smtp", "detail": f"sent to {to}"}
    except Exception as e:  # pragma: no cover
        safe = "".join(c for c in to if c.isalnum() or c in "@._-")
        fname = OUTBOX / f"FAILED_{datetime.now(timezone.utc):%Y%m%d%H%M%S}_{safe}.eml"
        fname.write_bytes(bytes(msg))
        return {"delivered": False, "via": "outbox", "detail": f"SMTP error: {e}; saved {fname}"}
