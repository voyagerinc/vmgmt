"""Local admin-account tool for the Management Server (run ON the server, from this folder).

  python admin_tool.py list                         show all admin accounts in this server's DB
  python admin_tool.py check  <email> <password>    test whether a password matches
  python admin_tool.py reset  <email> <new_password> set a new password + unlock the account

Uses the same database the running server uses (data\\management.db or EMP_DATABASE_URL).
"""
from __future__ import annotations

import sys

from app.config import DATA_DIR, settings
from app.database import SessionLocal, init_db
from app.models import AdminUser, Tenant
from app.security import hash_password, validate_password_strength, verify_password


def _users(db, email: str):
    return db.query(AdminUser).filter(AdminUser.email == email.strip().lower()).all()


def cmd_list(db) -> None:
    print(f"Database : {settings.database_url}")
    print(f"Data dir : {DATA_DIR}\n")
    rows = db.query(AdminUser).order_by(AdminUser.created_at).all()
    if not rows:
        print("No admin accounts found (server has not been started yet?).")
    for u in rows:
        t = db.get(Tenant, u.tenant_id) if u.tenant_id else None
        flags = []
        if not u.is_active:
            flags.append("DISABLED")
        if u.locked_until:
            flags.append(f"locked until {u.locked_until:%Y-%m-%d %H:%M} UTC")
        if t is not None and t.status == "inactive":
            flags.append("COMPANY INACTIVE")
        print(f"- {u.email:<35} role={u.role.value:<22} "
              f"company={(t.company_name if t else 'platform'):<20} tenant_id={u.tenant_id or '-'}"
              + (f"  [{', '.join(flags)}]" if flags else ""))


def cmd_check(db, email: str, password: str) -> None:
    users = _users(db, email)
    if not users:
        print(f"No account '{email}' in this database -> login says 'Invalid credentials'.")
        return
    for u in users:
        ok = verify_password(password, u.password_hash)
        print(f"{u.email} (tenant {u.tenant_id or '-'}): password {'MATCHES' if ok else 'does NOT match'}")


def cmd_reset(db, email: str, new_pw: str) -> None:
    err = validate_password_strength(new_pw)
    if err:
        sys.exit(f"Rejected: {err}")
    users = _users(db, email)
    if not users:
        sys.exit(f"No account '{email}' in this database.")
    for u in users:
        u.password_hash = hash_password(new_pw)
        u.failed_logins = 0
        u.locked_until = None
        u.is_active = True
    db.commit()
    print(f"Password reset and account unlocked for {email} ({len(users)} account(s)).")


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] not in ("list", "check", "reset"):
        sys.exit(__doc__)
    init_db()
    db = SessionLocal()
    try:
        if args[0] == "list":
            cmd_list(db)
        elif len(args) != 3:
            sys.exit(__doc__)
        elif args[0] == "check":
            cmd_check(db, args[1], args[2])
        else:
            cmd_reset(db, args[1], args[2])
    finally:
        db.close()


if __name__ == "__main__":
    main()
