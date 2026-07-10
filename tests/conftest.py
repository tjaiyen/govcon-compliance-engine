"""Tests run against the REAL migration artifacts on either backend.

SQLite (default): a template DB is built once per session via `alembic
upgrade head`, then file-copied per test. Never Base.metadata.create_all():
that would skip the triggers and the seed migrations, which are exactly what
the business-rule tests probe.

Postgres (Phase 4b): set GOVCON_TEST_PG to an admin URL, e.g.
    GOVCON_TEST_PG=postgresql+psycopg://govcon@127.0.0.1:54329/postgres
A template database is migrated once per session, then each test gets a
fresh database cloned from it (CREATE DATABASE … TEMPLATE — Postgres's
file-copy equivalent) and dropped afterward. The same suite therefore
exercises the plpgsql trigger layer (0017), the advisory-locked audit
chain, and native NUMERIC storage.

Tests that are SQLite-specific BY DESIGN (workspace files, sqlite3-level
probes) declare it with the `sqlite_only` marker — a reported skip, never a
silent one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PG_ADMIN_URL = os.environ.get("GOVCON_TEST_PG")  # None = SQLite mode


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "sqlite_only(reason): the probe is SQLite-specific by design; "
        "skipped when the suite runs on Postgres (GOVCON_TEST_PG set)",
    )


def pytest_collection_modifyitems(config, items):
    if not PG_ADMIN_URL:
        return
    for item in items:
        mark = item.get_closest_marker("sqlite_only")
        if mark:
            reason = mark.args[0] if mark.args else "SQLite-specific by design"
            item.add_marker(pytest.mark.skip(reason=f"[postgres run] {reason}"))


def _migrate(url: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "GOVCON_DB_URL": url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stderr}"


# ------------------------------------------------------------------ sqlite mode
@pytest.fixture(scope="session")
def template_db(tmp_path_factory) -> Path:
    """SQLite template file (also used by the workspace tests, which are
    SQLite-by-design regardless of backend)."""
    path = tmp_path_factory.mktemp("db") / "template.db"
    _migrate(f"sqlite:///{path}")
    return path


@pytest.fixture()
def db_path(template_db: Path, tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    shutil.copy(template_db, path)
    return path


# ---------------------------------------------------------------- postgres mode
@pytest.fixture(scope="session")
def _pg_template():
    """Migrated template database, once per session; dropped at the end."""
    import sqlalchemy as sa

    admin = sa.create_engine(PG_ADMIN_URL, isolation_level="AUTOCOMMIT")
    name = f"govcon_tpl_{os.getpid()}"
    with admin.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    base = PG_ADMIN_URL.rsplit("/", 1)[0]
    _migrate(f"{base}/{name}")
    yield name
    with admin.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture()
def _pg_test_db(_pg_template, request):
    """Fresh per-test database cloned from the template."""
    import re

    import sqlalchemy as sa

    admin = sa.create_engine(PG_ADMIN_URL, isolation_level="AUTOCOMMIT")
    suffix = re.sub(r"[^a-z0-9]+", "_", request.node.name.lower())[:40]
    name = f"govcon_t_{os.getpid()}_{suffix}_{abs(hash(request.node.nodeid)) % 10_000}"
    with admin.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(sa.text(f'CREATE DATABASE "{name}" TEMPLATE "{_pg_template}"'))
    base = PG_ADMIN_URL.rsplit("/", 1)[0]
    yield f"{base}/{name}"
    with admin.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


# ------------------------------------------------------------------ shared tail
@pytest.fixture()
def engine(request):
    from govcon.db.engine import make_engine

    if PG_ADMIN_URL:
        url = request.getfixturevalue("_pg_test_db")
    else:
        url = f"sqlite:///{request.getfixturevalue('db_path')}"
    engine = make_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory(engine):
    from govcon.db.engine import make_session_factory

    return make_session_factory(engine)


@pytest.fixture()
def session(session_factory):
    with session_factory() as session:
        yield session
        session.rollback()
