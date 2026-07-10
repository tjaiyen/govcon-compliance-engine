"""Phase 4 enterprise hardening: actor identity + workspace isolation.

Pre-registered (B35):
  * audit rows are attributed to the actor active at flush time; two actors
    in one process produce two distinct user_ids and the hash chain still
    verifies (attribution is inside the hashed payload).
  * identity is contextvar-scoped: concurrent threads see their own actor.
  * the API middleware attributes requests from X-Govcon-User; /api/whoami
    reflects it.
  * workspaces are PHYSICALLY isolated: data posted in workspace A is
    invisible via workspace B's header; hostile names are rejected as 422
    (path-traversal guard), unknown ones as 404.
"""

import concurrent.futures
import datetime
import threading

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from govcon.api import create_app
from govcon.core.identity import actor_context, current_actor
from govcon.db.audit import verify_audit_chain
from govcon.models import AuditTrail, Period
from govcon.models.enums import PeriodStatus


def _post_period(session, year, month):
    session.add(Period(
        fiscal_year=year, period_number=month,
        start_date=datetime.date(year, month, 1),
        end_date=datetime.date(year, month, 28),
        status=PeriodStatus.OPEN,
    ))
    session.flush()


# --- identity -------------------------------------------------------------------


def test_default_actor_is_cli_or_user_env(monkeypatch):
    monkeypatch.delenv("GOVCON_USER", raising=False)
    assert current_actor().startswith(("cli:", "proc:"))
    monkeypatch.setenv("GOVCON_USER", "tj")
    assert current_actor() == "user:tj"
    with actor_context("web:reviewer"):
        assert current_actor() == "web:reviewer"  # explicit beats env
    assert current_actor() == "user:tj"  # restored


def test_actor_context_rejects_empty():
    with pytest.raises(ValueError, match="non-empty"):
        with actor_context("  "):
            pass


def test_audit_rows_attribute_the_flushing_actor(session):
    with actor_context("web:alice"):
        _post_period(session, 2030, 1)
    with actor_context("web:bob"):
        _post_period(session, 2030, 2)
    rows = session.execute(
        sa.select(AuditTrail.user_id)
        .where(AuditTrail.table_name == "periods")
        .order_by(AuditTrail.trail_id)
    ).scalars().all()
    assert rows[-2:] == ["web:alice", "web:bob"]
    ok, bad = verify_audit_chain(session)
    assert ok, f"chain broke at {bad} — attribution must live INSIDE the hash"


def test_actor_is_isolated_across_threads():
    barrier = threading.Barrier(2)

    def in_thread(name):
        with actor_context(name):
            barrier.wait(timeout=5)  # both threads hold their actor at once
            return current_actor()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(in_thread, ["web:t1", "web:t2"]))
    assert results == ["web:t1", "web:t2"]


def test_api_middleware_attributes_requests(session_factory):
    c = TestClient(create_app(session_factory=session_factory))
    assert c.get("/api/whoami").json()["actor"] == "web:anonymous"
    body = c.get("/api/whoami", headers={"X-Govcon-User": "tj"}).json()
    assert body["actor"] == "web:tj"
    assert body["workspace"] == "default"  # no registry configured
    assert body["workspaces"] is None


# --- workspaces -----------------------------------------------------------------


pytestmark_ws = pytest.mark.sqlite_only(
    "workspaces are SQLite files by design (POSTGRES.md maps them to "
    "database-per-workspace); the registry fixture copies the SQLite template"
)


@pytest.fixture()
def registry(tmp_path, template_db):
    """A real registry whose create() copies the session-scoped template DB
    instead of re-running alembic (same artifact, seconds faster)."""
    import shutil

    from govcon.workspaces import WorkspaceRegistry

    reg = WorkspaceRegistry(root=tmp_path / "home")

    def fast_create(name):
        path = reg.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(template_db, path)
        return path

    reg.create = fast_create
    return reg


def test_workspace_name_validation_blocks_traversal(tmp_path):
    from govcon.workspaces import WorkspaceRegistry

    reg = WorkspaceRegistry(root=tmp_path)
    for hostile in ("../evil", "a/b", "..", "A B", "x" * 41, "", ".hidden"):
        with pytest.raises(ValueError, match="invalid workspace name"):
            reg.path(hostile)
    assert reg.path("team-a_1").name == "team-a_1.db"


@pytestmark_ws
def test_workspaces_are_physically_isolated(registry):
    registry.create("alpha")
    registry.create("beta")
    app = create_app(workspace_registry=registry)
    c = TestClient(app)

    # post a GL account into ALPHA only (direct session — the API is read-only)
    from govcon.db.engine import make_engine, make_session_factory
    from govcon.models import GLAccount
    from govcon.models.enums import CostType

    factory = make_session_factory(make_engine(registry.url("alpha")))
    with factory() as session:
        session.add(GLAccount(account_code="5000", account_name="Direct Labor",
                              cost_type=CostType.DIRECT))
        session.commit()

    a = c.get("/api/sf1408", headers={"X-Govcon-Workspace": "alpha"}).json()
    b = c.get("/api/sf1408", headers={"X-Govcon-Workspace": "beta"}).json()
    a_findings = " ".join(a["criteria"][0]["findings"])
    b_findings = " ".join(b["criteria"][0]["findings"])
    assert "1 GL account" in a_findings or a["has_data"] or "0 GL" not in a_findings
    assert "0 GL accounts" in b_findings  # beta never sees alpha's account
    assert a_findings != b_findings


@pytestmark_ws
def test_unknown_and_hostile_workspace_headers(registry):
    c = TestClient(create_app(workspace_registry=registry))
    r = c.get("/api/sf1408", headers={"X-Govcon-Workspace": "ghost"})
    assert r.status_code == 404 and "no workspace" in r.json()["detail"]
    r = c.get("/api/sf1408", headers={"X-Govcon-Workspace": "../evil"})
    assert r.status_code == 422 and "invalid workspace name" in r.json()["detail"]


@pytestmark_ws
def test_whoami_lists_workspaces_when_routing_enabled(registry):
    registry.create("alpha")
    c = TestClient(create_app(workspace_registry=registry))
    body = c.get("/api/whoami", headers={"X-Govcon-Workspace": "alpha"}).json()
    assert body["workspace"] == "alpha"
    assert body["workspaces"] == ["alpha"]


def test_limitations_state_asserted_identity(session_factory):
    c = TestClient(create_app(session_factory=session_factory))
    about = c.get("/api/about").text
    assert "ASSERTED" in about and "not authenticated" in about
