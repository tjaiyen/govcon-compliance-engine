"""AI tutor (Pattern 2): the same grounded engine-as-tools loop as /api/ask,
taught at a persona's depth. Asserts on DISPATCH, GROUNDING, GATING, and the
persona wiring — never on nondeterministic prose. Persona depth never changes
the determination; grounding still withholds ungrounded prose.
"""

from fastapi.testclient import TestClient

from govcon.ai.prompts import _TUTOR_PERSONAS, system_for
from govcon.api import create_app
from tests.ai.conftest import final_turn, tool_turn


def _client(session_factory, fake):
    return TestClient(create_app(session_factory=session_factory, llm_client=fake))


# ------------------------------------------------------------- system prompt
def test_each_persona_shapes_the_system_prompt():
    for persona, clause in _TUTOR_PERSONAS.items():
        sys = system_for("tutor", persona=persona)
        assert clause in sys  # the persona's audience clause is present
        assert "INTERFACE over a deterministic" in sys  # …but the guardrail holds
    # an unknown persona degrades to the default, never crashes
    assert _TUTOR_PERSONAS["newcomer"] in system_for("tutor", persona="bogus")


# ----------------------------------------------------------------- endpoint
def test_tutor_dispatches_tool_and_teaches_beside_determination(
    session_factory, make_fake, synthetic_mode
):
    fake = make_fake([
        tool_turn(("t1", "determine_cas_coverage",
                   {"award_date": "2026-05-15", "contract_value": "12000000.00",
                    "contractor_size": "other_than_small"})),
        final_turn("This $12,000,000.00 award has modified CAS coverage — here's why."),
    ])
    c = _client(session_factory, fake)
    body = c.post("/api/tutor",
                  json={"question": "Teach me CAS on a $12M May-2026 award",
                        "persona": "newcomer"}).json()
    assert body["ai_available"] is True and body["persona"] == "newcomer"
    # the authoritative determination is present and matches the engine
    assert body["determinations"][0]["result"]["tier"] == "modified"
    assert body["grounding"]["verified"] is True
    assert body["cost"]["calls"] == 2


def test_tutor_uses_the_selected_persona_prompt(session_factory, make_fake, synthetic_mode):
    fake = make_fake([final_turn("ok")])
    c = _client(session_factory, fake)
    c.post("/api/tutor", json={"question": "hi", "persona": "auditor"})
    # the fake recorded the system prompt it was called with → assert the persona
    assert _TUTOR_PERSONAS["auditor"] in fake.calls[0]["system"]


def test_tutor_defaults_to_newcomer_when_persona_omitted(
    session_factory, make_fake, synthetic_mode
):
    fake = make_fake([final_turn("ok")])
    c = _client(session_factory, fake)
    body = c.post("/api/tutor", json={"question": "hi"}).json()
    assert body["persona"] == "newcomer"
    assert _TUTOR_PERSONAS["newcomer"] in fake.calls[0]["system"]


def test_unknown_persona_is_422(session_factory, make_fake, synthetic_mode):
    fake = make_fake([final_turn("ok")])
    c = _client(session_factory, fake)
    r = c.post("/api/tutor", json={"question": "hi", "persona": "wizard"})
    assert r.status_code == 422  # enum validation rejects it
    assert fake.calls == []  # and no LLM call happened


def test_tutor_withholds_ungrounded_prose_but_keeps_determination(
    session_factory, make_fake, synthetic_mode
):
    fake = make_fake([
        tool_turn(("t1", "determine_cas_coverage",
                   {"award_date": "2026-05-15", "contract_value": "12000000.00",
                    "contractor_size": "other_than_small"})),
        final_turn("The trigger is $500,000,000 so this is full coverage."),
    ])
    c = _client(session_factory, fake)
    body = c.post("/api/tutor", json={"question": "..."}).json()
    assert body["grounding"]["verified"] is False
    assert "could not be" in body["prose"].lower()  # prose withheld
    assert body["determinations"][0]["result"]["tier"] == "modified"  # truth kept


def test_tutor_synthetic_gate_blocks_real_mode_before_any_llm_call(
    session_factory, make_fake, monkeypatch
):
    monkeypatch.setenv("GOVCON_DATA_MODE", "real")
    fake = make_fake([final_turn("should never be reached")])
    c = _client(session_factory, fake)
    body = c.post("/api/tutor", json={"question": "...", "persona": "analyst"}).json()
    assert body["ai_available"] is False
    assert fake.calls == []  # auth ≠ data-mode; the gate held, LLM never called


def test_tutor_unavailable_when_no_client(session_factory):
    c = TestClient(create_app(session_factory=session_factory))  # llm_client=None
    body = c.post("/api/tutor", json={"question": "..."}).json()
    assert body["ai_available"] is False
