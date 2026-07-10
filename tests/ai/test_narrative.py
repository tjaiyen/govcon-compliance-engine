"""AI narrative drafter (Pattern 4): a memo grounded ENTIRELY in the engine's
computed numbers. Strictest grounding — an ungrounded figure withholds the memo,
and the draft is always a SYNTHETIC, advisory artifact (never a filing).
"""

from fastapi.testclient import TestClient

from govcon.ai.prompts import system_for
from govcon.api import create_app
from tests.ai.conftest import final_turn, tool_turn


def _client(session_factory, fake):
    return TestClient(create_app(session_factory=session_factory, llm_client=fake))


def test_narrative_system_prompt_states_grounding_and_synthetic():
    # built per-call; default (synthetic) mode → the limitations say SYNTHETIC
    sys = system_for("draft_narrative")
    assert "INTERFACE over a deterministic" in sys
    assert "SYNTHETIC" in sys and "memo" in sys


def test_narrative_grounds_memo_in_determination_with_banner(
    session_factory, make_fake, synthetic_mode
):
    fake = make_fake([
        tool_turn(("t1", "determine_cas_coverage",
                   {"award_date": "2026-05-15", "contract_value": "12000000.00",
                    "contractor_size": "other_than_small"})),
        final_turn("MEMO: This $12,000,000.00 award carries modified CAS coverage."),
    ])
    c = _client(session_factory, fake)
    body = c.post("/api/draft-narrative",
                  json={"instruction": "Draft a memo on CAS for a $12M May-2026 award"}).json()
    assert body["ai_available"] is True
    assert body["determinations"][0]["result"]["tier"] == "modified"
    assert body["grounding"]["verified"] is True
    assert "SYNTHETIC" in body["synthetic_banner"] and "NOT FOR FILING" in body["synthetic_banner"]


def test_narrative_withholds_ungrounded_figure(session_factory, make_fake, synthetic_mode):
    fake = make_fake([
        tool_turn(("t1", "determine_cas_coverage",
                   {"award_date": "2026-05-15", "contract_value": "12000000.00",
                    "contractor_size": "other_than_small"})),
        # a fabricated dollar figure the engine never returned
        final_turn("MEMO: the applicable threshold is $250,000,000, so full coverage applies."),
    ])
    c = _client(session_factory, fake)
    body = c.post("/api/draft-narrative", json={"instruction": "..."}).json()
    assert body["grounding"]["verified"] is False
    assert "could not be" in body["prose"].lower()  # memo withheld
    assert body["determinations"][0]["result"]["tier"] == "modified"  # truth kept


def test_narrative_gate_blocks_real_mode_before_any_llm_call(
    session_factory, make_fake, monkeypatch
):
    monkeypatch.setenv("GOVCON_DATA_MODE", "real")
    fake = make_fake([final_turn("should never be reached")])
    c = _client(session_factory, fake)
    body = c.post("/api/draft-narrative", json={"instruction": "..."}).json()
    assert body["ai_available"] is False
    assert fake.calls == []


def test_narrative_unavailable_when_no_client(session_factory):
    c = TestClient(create_app(session_factory=session_factory))  # llm_client=None
    body = c.post("/api/draft-narrative", json={"instruction": "..."}).json()
    assert body["ai_available"] is False
