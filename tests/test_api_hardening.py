"""Backend ops hardening (PR-2): /health, security headers, request-id, the
optional bearer gate + CORS, the /api/ask rate limiter, cache headers, the
suggestions cap, and SQLite WAL."""

import sqlalchemy as sa
from fastapi.testclient import TestClient

from govcon.api import create_app


def _c(session_factory, **kw):
    return TestClient(create_app(session_factory=session_factory, **kw))


def test_health_reports_db_and_ai(session_factory):
    body = _c(session_factory).get("/health").json()
    assert body["status"] == "ok" and body["db"] is True and body["ai"] is False


def test_security_headers_and_request_id_on_every_response(session_factory):
    r = _c(session_factory).get("/api/about")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" in r.headers
    assert r.headers.get("x-request-id")


def test_request_id_is_echoed_when_supplied(session_factory):
    r = _c(session_factory).get("/health", headers={"X-Request-Id": "abc123"})
    assert r.headers["x-request-id"] == "abc123"


def test_index_has_cache_control_and_charset(session_factory):
    r = _c(session_factory).get("/")
    assert "must-revalidate" in r.headers.get("cache-control", "")
    assert "charset=utf-8" in r.headers.get("content-type", "")


def test_bearer_gate_off_by_default(session_factory):
    # no GOVCON_API_TOKEN → open (dev default)
    assert _c(session_factory).get("/api/about").status_code == 200


def test_bearer_gate_enforced_when_set(session_factory, monkeypatch):
    monkeypatch.setenv("GOVCON_API_TOKEN", "s3cret")
    c = _c(session_factory)
    # a gated /api/* route requires the shared secret
    assert c.get("/api/reverify").status_code == 401
    assert c.get("/api/reverify", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    # the UI (non-/api) stays open so the page can bootstrap
    assert c.get("/").status_code == 200
    # /api/about is a deliberate public carve-out: the transparency/limitations
    # text is always readable, gate or no gate.
    assert c.get("/api/about").status_code == 200


def test_cors_allow_list_when_configured(session_factory, monkeypatch):
    monkeypatch.setenv("GOVCON_CORS_ORIGINS", "https://example.gov")
    c = _c(session_factory)
    r = c.get("/api/about", headers={"Origin": "https://example.gov"})
    assert r.headers.get("access-control-allow-origin") == "https://example.gov"


def test_ask_rate_limited(session_factory, monkeypatch):
    monkeypatch.setenv("GOVCON_DATA_MODE", "synthetic")
    monkeypatch.setenv("GOVCON_AI_RATE_LIMIT", "2")
    monkeypatch.setenv("GOVCON_AI_RATE_WINDOW_S", "60")
    from tests.ai.conftest import FakeLLMClient, final_turn

    # a fake that answers immediately (no tools) so each call is cheap
    fake = FakeLLMClient([final_turn("ok") for _ in range(10)])
    c = _c(session_factory, llm_client=fake)
    codes = [c.post("/api/ask", json={"question": "hi"}).status_code for _ in range(4)]
    assert codes.count(200) == 2 and codes.count(429) == 2


def test_suggestions_endpoint_is_capped(session_factory):
    body = _c(session_factory).get("/api/suggestions").json()
    assert "truncated" in body and body["truncated"] is False  # empty DB


def test_sqlite_runs_in_wal_mode(engine):
    if engine.dialect.name != "sqlite":
        import pytest

        pytest.skip("WAL is a SQLite pragma")
    with engine.connect() as conn:
        mode = conn.execute(sa.text("PRAGMA journal_mode")).scalar()
    assert str(mode).lower() == "wal"


def test_rate_limiter_memory_is_bounded():
    # stress-test finding: rotated IPs must not grow _hits without bound
    from govcon.api.hardening import RateLimiter

    rl = RateLimiter(limit=5, window_s=60)
    rl._MAX_KEYS = 100  # small cap for the test
    for i in range(1000):
        rl.allow(f"ip-{i}")
    assert len(rl._hits) <= 100  # LRU-capped, not 1000
    # a normal repeated caller is still limited correctly
    rl2 = RateLimiter(limit=3, window_s=60)
    assert [rl2.allow("x") for _ in range(5)] == [True, True, True, False, False]
