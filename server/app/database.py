"""Database engine & session (PRD §28). SQLite dev / PostgreSQL prod."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

_connect_args = {}
if settings.database_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    future=True,
)


if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _rec):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables, then apply lightweight additive column migrations."""
    from . import models  # noqa: F401  (register mappers)
    Base.metadata.create_all(bind=engine)
    _migrate_columns()


# (table, column, DDL type, default literal) — additive, idempotent (safe on every boot).
_COLUMN_MIGRATIONS = [
    ("devices", "collection", "JSON",
     '\'{"health": true, "software": true, "activity": false, "file_events": false, "screenshots": true}\''),
    ("licenses", "license_type", "VARCHAR(32)", "'subscription_monthly'"),
    ("admin_users", "cred_seq", "INTEGER", "0"),
    ("tenants", "client_server_version", "VARCHAR(50)", "NULL"),
    ("tenants", "client_server_updated_at", "DATETIME", "NULL"),
    ("tenants", "last_sync_at", "DATETIME", "NULL"),
]


def _migrate_columns() -> None:
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, column, coltype, default in _COLUMN_MIGRATIONS:
            if table not in existing_tables:
                continue
            cols = {c["name"] for c in insp.get_columns(table)}
            if column in cols:
                continue
            ddl = f"ALTER TABLE {table} ADD COLUMN {column} {coltype} DEFAULT {default}"
            try:
                conn.execute(text(ddl))
            except Exception:
                pass
