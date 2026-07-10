"""Tests run against the REAL migration artifacts — a template DB is built
once per session via `alembic upgrade head`, then file-copied per test.
Never Base.metadata.create_all(): that would skip the triggers and the seed
migration, which are exactly what the business-rule tests probe.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def template_db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("db") / "template.db"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "GOVCON_DB_URL": f"sqlite:///{path}"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stderr}"
    return path


@pytest.fixture()
def db_path(template_db: Path, tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    shutil.copy(template_db, path)
    return path


@pytest.fixture()
def engine(db_path: Path):
    from govcon.db.engine import make_engine

    engine = make_engine(f"sqlite:///{db_path}")
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
