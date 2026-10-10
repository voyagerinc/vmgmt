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


def _connect(cfg: dict) -> smtplib.SMTP:
    """Open an authenticated SMTP session using the encryption the port expects:
    465 = implicit SSL (SMTP_SSL), otherwise STARTTLS when use_tls is on (587), else plain (25).
    Connecting to 465 with a plain/STARTTLS client makes the server drop the connection."""
    host, port = cfg["host"].strip(), int(cfg.get("port") or 587)
    user, pw = (cfg.get("user") or "").strip(), cfg.get("password")
    ctx = ssl.create_default_context()
    if port == 465:
        s = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
    else:
        s = smtplib.SMTP(host, port, timeout=20)
        if cfg.get("use_tls", True):
            s.ehlo()
            s.starttls(context=ctx)
            s.ehlo()
    try:
        if user:
            s.login(user, pw)
    except smtplib.SMTPServerDisconnected:
        # connected fine, then dropped at login: some providers (e.g. Gmail) do this for bad credentials
        s.close()
        raise smtplib.SMTPAuthenticationError(535, b"server closed the connection during login")
    except Exception:
        s.close()
        raise
    return s


def _explain(cfg: dict, e: Exception) -> str:
    where = f"{cfg.get('host')}:{cfg.get('port')}"
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return (f"Authentication failed ({e.smtp_code}) — check the username/password. Gmail, Outlook "
                "and Zoho need an App Password when 2-step verification is on.")
    if isinstance(e, (smtplib.SMTPServerDisconnected, ssl.SSLError)):
        return (f"{where} closed the connection — the port and encryption do not match. Use port 465 "
                "(SSL) or port 587 with 'Use TLS' on; port 25 usually needs TLS off.")
    if isinstance(e, smtplib.SMTPNotSupportedError):
        return (f"{where} only allows login over an encrypted connection — turn 'Use TLS' on "
                "(port 587) or use port 465.")
    if isinstance(e, smtplib.SMTPConnectError) or (isinstance(e, OSError)
                                                    and not isinstance(e, smtplib.SMTPException)):
        return (f"Could not reach {where} ({e}) — check the host name/port and that this server's "
                "firewall/ISP allows outgoing SMTP.")
    return f"{type(e).__name__}: {e}"


def _reply(code, msg) -> str:
    text = msg.decode("utf-8", "replace") if isinstance(msg, bytes) else str(msg or "")
    return f"{code} {' '.join(text.split())}".strip()


def _provider_hint(host: str) -> str:
    h = (host or "").lower()
    if "gmail" in h or "google" in h:
        return (" Gmail: turn on 2-Step Verification, create an App Password at "
                "myaccount.google.com/apppasswords and use it as the password (not your normal one).")
    if "office365" in h or "outlook" in h or "hotmail" in h or "live.com" in h:
        return (" Microsoft 365/Outlook: SMTP AUTH must be enabled for this mailbox in the admin "
                "center; with MFA use an app password.")
    if "zoho" in h:
        return " Zoho: generate an Application-Specific Password in Zoho Mail security settings."
    return ""


def diagnose_smtp(cfg: dict, send_to: str | None = None) -> dict:
    """Run the SMTP conversation step by step (DNS, TCP, SSL/STARTTLS, EHLO, login, optional
    test message) and report each step with the server's exact reply, so a failure shows where
    and why. Never includes the password."""
    import socket
    import time

    host = (cfg.get("host") or "").strip()
    port = int(cfg.get("port") or 587)
    user = (cfg.get("user") or "").strip()
    mode = "SSL (port 465)" if port == 465 else ("STARTTLS" if cfg.get("use_tls", True) else "none (plain)")
    info = {"host": host or "—", "port": port, "encryption": mode, "username": user or "—",
            "password": "set" if cfg.get("password") else "not set",
            "from": cfg.get("from_addr") or "—", "send_to": send_to or "—"}
    steps: list[dict] = []
    result = {"ok": False, "settings": info, "steps": steps, "failed_step": None, "hint": None, "error": None}

    def add(name, ok, detail, t0):
        steps.append({"step": name, "ok": ok, "detail": detail, "ms": int((time.perf_counter() - t0) * 1000)})

    def fail(name, detail, hint, t0):
        add(name, False, detail, t0)
        result.update(failed_step=name, hint=hint, error=f"{name}: {detail}")
        return result

    t0 = time.perf_counter()
    if not host:
        return fail("Settings", "No SMTP host entered.",
                    "Enter your mail provider's SMTP host, e.g. smtp.gmail.com, smtp.office365.com, smtp.zoho.in.", t0)
    add("Settings", True, f"{host}:{port}, encryption {mode}, login {'as ' + user if user else 'none'}", t0)

    t0 = time.perf_counter()
    try:
        ips = sorted({a[4][0] for a in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)})
        add("Find server (DNS)", True, f"{host} → {', '.join(ips[:4])}", t0)
    except Exception as e:
        return fail("Find server (DNS)", f"Host name not found ({e}).",
                    "Check the SMTP host spelling, and that this server has internet/DNS access.", t0)

    t0 = time.perf_counter()
    try:
        socket.create_connection((host, port), timeout=8).close()
        add("Connect (TCP)", True, f"Port {port} is reachable", t0)
    except socket.timeout:
        return fail("Connect (TCP)", f"No answer from {host}:{port} (timed out).",
                    f"Port {port} is blocked by this server's firewall, the network or the ISP, or it is the "
                    "wrong port. Many ISPs and cloud hosts block port 25 — use 587 (STARTTLS) or 465 (SSL).", t0)
    except OSError as e:
        return fail("Connect (TCP)", f"Connection refused/failed ({e}).",
                    f"Nothing is accepting mail on {host}:{port}. Check the port your provider gives "
                    "(usually 587 or 465).", t0)

    ctx = ssl.create_default_context()
    s = None
    try:
        t0 = time.perf_counter()
        try:
            if port == 465:
                s = smtplib.SMTP_SSL(timeout=20, context=ctx)
            else:
                s = smtplib.SMTP(timeout=20)
            s._host = host                      # SNI + certificate check (only set by the host= constructor)
            code, msg = s.connect(host, port)
            tls = f" · {s.sock.version()}" if port == 465 else ""
            add("Server greeting" + (" + SSL" if port == 465 else ""), True, _reply(code, msg) + tls, t0)
        except (ssl.SSLError, smtplib.SMTPServerDisconnected, ConnectionResetError) as e:
            if port == 465:
                hint = ("SSL handshake failed on port 465. If your provider uses STARTTLS, set port 587 "
                        "with 'Use STARTTLS' = Yes.")
            else:
                hint = (f"The server closed the connection right away — it probably expects SSL on port {port}. "
                        "Use port 465 (SSL), or the provider's STARTTLS port 587.")
            return fail("Server greeting", f"{type(e).__name__}: {e}", hint, t0)

        t0 = time.perf_counter()
        code, msg = s.ehlo()
        feats = ", ".join(f"{k.upper()}{(' ' + v) if v else ''}" for k, v in s.esmtp_features.items()
                          if k in ("auth", "starttls", "size", "8bitmime", "smtputf8"))
        add("Hello (EHLO)", code == 250, f"{code} · features: {feats or 'none advertised'}", t0)

        if port != 465 and cfg.get("use_tls", True):
            t0 = time.perf_counter()
            if not s.has_extn("starttls"):
                return fail("Encryption (STARTTLS)", "Server does not offer STARTTLS on this port.",
                            f"Use port 465 (SSL), or set 'Use STARTTLS' = No if {host} really "
                            "allows unencrypted mail on this port.", t0)
            try:
                code, msg = s.starttls(context=ctx)
                s.ehlo()
                add("Encryption (STARTTLS)", True, f"{_reply(code, msg)} · {s.sock.version()}", t0)
            except ssl.SSLCertVerificationError as e:
                return fail("Encryption (STARTTLS)", f"Certificate not trusted: {e.verify_message}",
                            "The mail server's certificate does not match the host name. Use the exact host "
                            "name your provider gives (e.g. smtp.gmail.com, not an IP address).", t0)

        t0 = time.perf_counter()
        if not user:
            add("Login (AUTH)", True, "Skipped — no username (server must allow sending without login)", t0)
        else:
            if not s.has_extn("auth"):
                return fail("Login (AUTH)", "Server does not offer login (AUTH) at this point.",
                            "Most servers only allow login after encryption: set 'Use STARTTLS' = Yes "
                            "(port 587) or use port 465.", t0)
            try:
                code, msg = s.login(user, cfg.get("password") or "")
                add("Login (AUTH)", True, f"{_reply(code, msg)} · signed in as {user}", t0)
            except smtplib.SMTPAuthenticationError as e:
                return fail("Login (AUTH)", f"Rejected: {_reply(e.smtp_code, e.smtp_error)}",
                            "Username or password is wrong, or the account needs an app password."
                            + _provider_hint(host), t0)
            except smtplib.SMTPServerDisconnected as e:
                return fail("Login (AUTH)", f"Server closed the connection during login ({e}).",
                            "Usually wrong credentials or a blocked sign-in." + _provider_hint(host), t0)

        if send_to:
            t0 = time.perf_counter()
            msg = EmailMessage()
            msg["From"] = f"{cfg.get('from_name') or ''} <{cfg.get('from_addr') or user}>"
            msg["To"] = send_to
            msg["Subject"] = "Voyager Endpoint Management — SMTP test"
            msg.set_content("This is a test message confirming your email (SMTP) settings work.\n\n"
                            f"Server: {host}:{port} ({mode})\nSent: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n")
            try:
                refused = s.send_message(msg)
                if refused:
                    return fail("Send test email", f"Some recipients refused: {refused}",
                                "Check the recipient address.", t0)
                add("Send test email", True, f"Accepted by {host} for delivery to {send_to}", t0)
            except smtplib.SMTPSenderRefused as e:
                if e.smtp_code == 530 or not user:
                    return fail("Send test email", f"Server requires login: {_reply(e.smtp_code, e.smtp_error)}",
                                "Enter the mailbox Username and Password — this server does not accept mail "
                                "without signing in." + _provider_hint(host), t0)
                return fail("Send test email", f"From address refused: {_reply(e.smtp_code, e.smtp_error)}",
                            f"The 'From address' must be your login ({user}) or an alias "
                            "the provider allows for it.", t0)
            except smtplib.SMTPRecipientsRefused as e:
                det = "; ".join(f"{k}: {_reply(*v)}" for k, v in e.recipients.items())
                return fail("Send test email", f"Recipient refused: {det}",
                            "Check the recipient address; some servers only relay after login.", t0)
            except smtplib.SMTPDataError as e:
                return fail("Send test email", f"Message rejected: {_reply(e.smtp_code, e.smtp_error)}",
                            "The provider rejected the message (sending limit, spam policy or From address).", t0)
        result["ok"] = True
        return result
    except Exception as e:                      # anything unexpected, reported on the step it hit
        result.update(failed_step=result["failed_step"] or "Unexpected error",
                      error=f"{type(e).__name__}: {e}", hint=_explain(cfg, e))
        steps.append({"step": "Unexpected error", "ok": False, "detail": f"{type(e).__name__}: {e}", "ms": 0})
        return result
    finally:
        if s is not None:
            try:
                s.quit()
            except Exception:
                try:
                    s.close()
                except Exception:
                    pass


def verify_smtp(cfg: dict) -> dict:
    """Connect + authenticate (no send). Returns {ok, error, diagnostics}."""
    d = diagnose_smtp(cfg)
    if not cfg.get("host"):
        d["error"] = "No SMTP host configured — emails save to the local outbox."
    return {"ok": d["ok"], "error": None if d["ok"] else (d["error"] + (f" — {d['hint']}" if d["hint"] else "")),
            "diagnostics": d}


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
        with _connect(cfg) as s:
            s.send_message(msg)
        return {"delivered": True, "via": "smtp", "detail": f"sent to {to}"}
    except Exception as e:  # pragma: no cover
        safe = "".join(c for c in to if c.isalnum() or c in "@._-")
        fname = OUTBOX / f"FAILED_{datetime.now(timezone.utc):%Y%m%d%H%M%S}_{safe}.eml"
        fname.write_bytes(bytes(msg))
        return {"delivered": False, "via": "outbox", "detail": f"SMTP error: {_explain(cfg, e)}; saved {fname}"}
