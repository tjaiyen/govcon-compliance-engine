"""Live smoke test — hits the real Anthropic API ONCE to confirm SDK wiring.

Skipped unless ANTHROPIC_API_KEY is set (so CI and offline runs stay green).
Asserts only that a determination came back and grounding passed — NEVER on the
model's wording (that is nondeterministic).
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="live API test — set ANTHROPIC_API_KEY to run (uv sync --extra ai)",
)


def test_live_ask_grounds_a_real_determination(session_factory, monkeypatch):
    monkeypatch.setenv("GOVCON_DATA_MODE", "synthetic")
    from fastapi.testclient import TestClient

    from govcon.ai import default_client_or_none
    from govcon.api import create_app

    client = default_client_or_none()
    assert client is not None, "ai extra + key must be present"
    c = TestClient(create_app(session_factory=session_factory, llm_client=client))
    body = c.post("/api/ask", json={
        "question": "Does CAS coverage apply to a $12,000,000 award made on 2026-05-15 "
                    "to an other-than-small contractor? Answer briefly."
    }).json()
    assert body["ai_available"] is True
    # a determination was actually produced by calling the engine as a tool
    tiers = [d["result"].get("tier") for d in body["determinations"]
             if d["tool"] == "determine_cas_coverage"]
    assert "modified" in tiers
    # the prose was grounded (or, if not, was withheld — never an ungrounded claim)
    assert body["grounding"]["verified"] in (True, False)
    if not body["grounding"]["verified"]:
        assert "could not be" in body["prose"].lower()
    assert body["cost"]["calls"] >= 1
