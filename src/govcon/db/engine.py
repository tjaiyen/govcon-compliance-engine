"""Engine and session factory.

SQLite does NOT enforce foreign keys by default — the connect-event PRAGMA
below is load-bearing referential integrity, not an optimization.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DB_URL = "sqlite:///govcon.db"


def get_db_url() -> str:
    return os.environ.get("GOVCON_DB_URL", DEFAULT_DB_URL)


def make_engine(url: str | None = None) -> Engine:
    engine = create_engine(url or get_db_url())

    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _enable_sqlite_fks(dbapi_conn, _record):  # pragma: no cover - trivial
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    # Importing govcon.db.audit / govcon.db.guards registers their
    # session-level event listeners (they listen on the Session class).
    from govcon.db import audit as _audit  # noqa: F401
    from govcon.db import guards as _guards  # noqa: F401

    return sessionmaker(bind=engine, expire_on_commit=False)
