"""Generate a deployable, pre-configured endpoint agent for a specific company (tenant).

Produces agent_package/<Company>/ containing the agent code plus an `agent_config.json`
that embeds the server URL, license id and a fresh enrollment token, so the agent
self-enrolls on first run with no command-line typing (PRD §7.2 Zero-Complexity).

Usage (run with the server's venv):
    python generate_agent.py --company "Allfine Industries Pvt Ltd" \
        --server-url http://192.168.31.113:8080 \
        --edition standard --devices 25 --term-days 365

    # or target an existing tenant by id, and reuse its existing license:
    python generate_agent.py --tenant <TENANT_ID> --server-url http://host:8080
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "server"
AGENT = ROOT / "agent"
sys.path.insert(0, str(SERVER))

from app.database import SessionLocal                      # noqa: E402
from app.models import EnrollmentToken, License, LicenseEdition, Tenant  # noqa: E402
from app.services import license_service as L             # noqa: E402

AGENT_FILES = ["agent.py", "collectors.py", "requirements.txt"]


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_") or "company"


def find_tenant(db, company: str | None, tenant_id: str | None) -> Tenant:
    if tenant_id:
        t = db.get(Tenant, tenant_id)
        if not t:
            sys.exit(f"No tenant with id {tenant_id}")
        return t
    rows = db.query(Tenant).filter(Tenant.company_name == company).all()
    if not rows:
        sys.exit(f"No company named {company!r}. Create it first in the console (Licenses & Tenants).")
    if len(rows) > 1:
        sys.exit(f"Multiple tenants named {company!r}; pass --tenant <id> instead.")
    return rows[0]


def ensure_license(db, tenant: Tenant, edition: str, devices: int, admins: int, term_days: int) -> License:
    lic = L.active_license(db, tenant.id)
    if lic and L.effective_status(lic).value == "active":
        print(f"Using existing active license {lic.id} ({lic.edition.value}).")
        return lic
    ed = LicenseEdition(edition)
    is_demo = ed == LicenseEdition.DEMO
    lic = License(
        tenant_id=tenant.id, edition=ed,
        start_date=datetime.now(timezone.utc),
        expiry_date=datetime.now(timezone.utc) + timedelta(days=15 if is_demo else term_days),
        max_devices=devices, max_admins=admins, max_storage_mb=10240,
        is_demo=is_demo, features=L.DEFAULT_FEATURES,
        activated=True, activated_server_id="generated",
    )
    db.add(lic)
    db.flush()
    lic.signature = L.build_activation_token(lic, tenant.company_name)
    db.commit()
    print(f"Created {ed.value} license {lic.id} ({devices} devices, "
          f"{'15d demo' if is_demo else str(term_days)+'d'}).")
    return lic


def make_token(db, tenant: Tenant, label: str, ttl_days: int) -> EnrollmentToken:
    tok = EnrollmentToken(
        tenant_id=tenant.id, label=label, max_uses=0,  # unlimited within seat cap
        expires_at=datetime.now(timezone.utc) + timedelta(days=ttl_days),
        created_by="generate_agent",
    )
    db.add(tok)
    db.commit()
    print(f"Created enrollment token {tok.token} (valid {ttl_days} days).")
    return tok


def build_package(tenant: Tenant, lic: License, tok: EnrollmentToken, server_url: str) -> Path:
    out = ROOT / "agent_package" / _slug(tenant.company_name)
    out.mkdir(parents=True, exist_ok=True)
    for f in AGENT_FILES:
        shutil.copy2(AGENT / f, out / f)

    config = {
        "company": tenant.company_name,
        "tenant_id": tenant.id,
        "server": server_url.rstrip("/"),
        "license_id": lic.id,
        "enroll_token": tok.token,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (out / "agent_config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    (out / "install_and_run.bat").write_text(
        "@echo off\r\n"
        "REM Endpoint Agent - pre-configured for " + tenant.company_name + "\r\n"
        "cd /d \"%~dp0\"\r\n"
        "echo Installing agent dependencies...\r\n"
        "python -m pip install -r requirements.txt\r\n"
        "echo Starting agent (reads agent_config.json automatically)...\r\n"
        "python agent.py\r\n"
        "pause\r\n",
        encoding="utf-8",
    )

    (out / "README.txt").write_text(
        f"Endpoint Agent - {tenant.company_name}\n"
        f"{'='*50}\n\n"
        f"Server : {config['server']}\n"
        f"License: {lic.id}\n"
        f"Token  : {tok.token}\n\n"
        "TO DEPLOY ON AN EMPLOYEE PC:\n"
        "  1. Copy this whole folder to the target computer.\n"
        "  2. Make sure Python 3.11+ is installed (or use the compiled Agent_Setup.exe build).\n"
        "  3. Double-click install_and_run.bat  (or run: python agent.py)\n\n"
        "The agent reads agent_config.json and enrolls itself - no typing required.\n"
        "It then runs in the background and syncs on the server-controlled heartbeat.\n\n"
        "Status is written to %PROGRAMDATA%\\EndpointAgent\\status.txt\n"
        "Log is at        %PROGRAMDATA%\\EndpointAgent\\agent.log\n\n"
        "NOTE: the server must be reachable at the URL above from this PC.\n"
        "For a production rollout, build the signed Agent_Setup.exe (see installers/README.md)\n"
        "and drop this agent_config.json next to it.\n",
        encoding="utf-8",
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a pre-configured agent for a company")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--company", help="Company (tenant) name")
    g.add_argument("--tenant", help="Tenant id")
    ap.add_argument("--server-url", required=True, help="Server URL agents will connect to")
    ap.add_argument("--edition", default="standard", choices=["demo", "standard", "custom", "enterprise"])
    ap.add_argument("--devices", type=int, default=25)
    ap.add_argument("--admins", type=int, default=3)
    ap.add_argument("--term-days", type=int, default=365)
    ap.add_argument("--token-ttl-days", type=int, default=365)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        tenant = find_tenant(db, args.company, args.tenant)
        print(f"Tenant: {tenant.company_name} ({tenant.id})")
        lic = ensure_license(db, tenant, args.edition, args.devices, args.admins, args.term_days)
        tok = make_token(db, tenant, label="generated-package", ttl_days=args.token_ttl_days)
        out = build_package(tenant, lic, tok, args.server_url)
    finally:
        db.close()

    print("\nAgent package ready:")
    print(f"  {out}")
    print("\nDeploy: copy the folder to each PC and double-click install_and_run.bat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
