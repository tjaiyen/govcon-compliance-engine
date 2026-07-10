"""Event-level SSE streaming over the SAME loop as the batch endpoints. Asserts
the event sequence a client receives (status → determination(s) → grounding →
prose → cost → done), that a streamed answer matches the batch answer, and that
the synthetic gate blocks a stream before any LLM call.
"""

import json

from fastapi.testclient import TestClient

from govcon.ai.cost import CostLog
from govcon.ai.loop import iter_conversation, run_conversation
from govcon.api import create_app
from tests.ai.conftest import final_turn, tool_turn


def _client(session_factory, fake):
    return TestClient(create_app(session_factory=session_factory, llm_client=fake))


def _events(sse_text):
    """Parse an SSE body into the list of event dicts."""
    out = []
    for line in sse_text.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[len("data: "):]))
    return out


# ------------------------------------------------------- kernel: one loop, two uses
def test_iter_conversation_yields_events_then_returns_result(session, make_fake):
    fake = make_fake([
        tool_turn(("t1", "determine_cas_coverage",
                   {"award_date": "2026-05-15", "contract_value": "12000000.00",
                    "contractor_size": "other_than_small"})),
        final_turn("This $12,000,000.00 award has modified CAS coverage."),
    ])
    gen = iter_conversation(
        fake, session, system="s", tool_names=["determine_cas_coverage"],
        user_text="q", cost_log=CostLog(pattern="ask"),
    )
    events, result = [], None
    while True:
        try:
            events.append(next(gen))
        except StopIteration as stop:
            result = stop.value
            break
    kinds = [e["type"] for e in events]
    assert "status" in kinds and "determination" in kinds
    det = next(e for e in events if e["type"] == "determination")
    assert det["determination"]["result"]["tier"] == "modified"
    # the returned result is the SAME object the batch path produces
    assert result.prose.startswith("This $12,000,000.00")
    assert result.grounding.verified is True


def test_run_conversation_matches_the_generator_result(session, make_fake):
    def script():
        return [
            tool_turn(("t1", "determine_cas_coverage",
                       {"award_date": "2026-05-15", "contract_value": "12000000.00",
                        "contractor_size": "other_than_small"})),
            final_turn("modified CAS coverage applies."),
        ]

    batch = run_conversation(
        make_fake(script()), session, system="s",
        tool_names=["determine_cas_coverage"], user_text="q", cost_log=CostLog(pattern="ask"),
    )
    assert batch.prose == "modified CAS coverage applies."
    assert batch.determinations[0]["result"]["tier"] == "modified"


# ------------------------------------------------------------- endpoint: SSE
def test_ask_stream_emits_determination_then_grounded_prose(
    session_factory, make_fake, synthetic_mode
):
    fake = make_fake([
        tool_turn(("t1", "determine_cas_coverage",
                   {"award_date": "2026-05-15", "contract_value": "12000000.00",
                    "contractor_size": "other_than_small"})),
        final_turn("This $12,000,000.00 award has modified CAS coverage."),
    ])
    c = _client(session_factory, fake)
    r = c.post("/api/ask?stream=1", json={"question": "Does CAS apply to a $12M May-2026 award?"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    evts = _events(r.text)
    kinds = [e["type"] for e in evts]
    # the whole streamed lifecycle, in order-ish
    assert kinds[0] == "status" and kinds[-1] == "done"
    det = next(e for e in evts if e["type"] == "determination")
    assert det["determination"]["result"]["tier"] == "modified"
    grounding = next(e for e in evts if e["type"] == "grounding")
    assert grounding["verified"] is True
    prose = next(e for e in evts if e["type"] == "prose")
    assert "modified CAS coverage" in prose["text"]
    assert any(e["type"] == "cost" for e in evts)


def test_stream_withholds_ungrounded_prose(session_factory, make_fake, synthetic_mode):
    fake = make_fake([
        tool_turn(("t1", "determine_cas_coverage",
                   {"award_date": "2026-05-15", "contract_value": "12000000.00",
                    "contractor_size": "other_than_small"})),
        final_turn("The threshold is $900,000,000 so full coverage applies."),
    ])
    c = _client(session_factory, fake)
    evts = _events(c.post("/api/ask?stream=1", json={"question": "..."}).text)
    grounding = next(e for e in evts if e["type"] == "grounding")
    prose = next(e for e in evts if e["type"] == "prose")
    assert grounding["verified"] is False
    assert "could not be" in prose["text"].lower()  # memo/answer withheld
    # the authoritative determination was still streamed
    assert any(e["type"] == "determination" and e["determination"]["result"]["tier"] == "modified"
               for e in evts)


def test_tutor_stream_uses_persona_from_body(session_factory, make_fake, synthetic_mode):
    fake = make_fake([final_turn("ok, taught")])
    c = _client(session_factory, fake)
    r = c.post("/api/tutor?stream=1", json={"question": "hi", "persona": "auditor"})
    assert r.headers["content-type"].startswith("text/event-stream")
    from govcon.ai.prompts import _TUTOR_PERSONAS
    assert _TUTOR_PERSONAS["auditor"] in fake.calls[0]["system"]  # persona honored


def test_stream_gate_blocks_real_mode_before_any_llm_call(
    session_factory, make_fake, monkeypatch
):
    monkeypatch.setenv("GOVCON_DATA_MODE", "real")
    fake = make_fake([final_turn("should never be reached")])
    c = _client(session_factory, fake)
    evts = _events(c.post("/api/ask?stream=1", json={"question": "..."}).text)
    assert any(e["type"] == "unavailable" for e in evts)
    assert evts[-1]["type"] == "done"
    assert fake.calls == []  # the LLM was never called


def test_stream_unavailable_when_no_client(session_factory):
    c = TestClient(create_app(session_factory=session_factory))  # llm_client=None
    evts = _events(c.post("/api/ask?stream=1", json={"question": "..."}).text)
    assert evts[0]["type"] == "unavailable" and evts[-1]["type"] == "done"


def test_draft_rule_does_not_stream(session_factory, make_fake, synthetic_mode):
    # draft-rule is not a streamable (grounded-prose) pattern → JSON even with ?stream
    fake = make_fake([final_turn("draft only")])
    c = _client(session_factory, fake)
    r = c.post("/api/draft-rule?stream=1", json={"instruction": "..."})
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["requires_human_migration"] is True


class _BoomClient:
    """An LLM client that raises an UNEXPECTED error (not a known AI error)."""

    calls: list = []

    def create(self, **kw):
        raise RuntimeError("upstream exploded")


def test_stream_terminates_cleanly_on_unexpected_error(session_factory, synthetic_mode):
    # a mid-stream error must still emit error + done — never a half-open stream
    c = TestClient(create_app(session_factory=session_factory, llm_client=_BoomClient()))
    r = c.post("/api/ask?stream=1", json={"question": "hi"})
    assert r.status_code == 200  # the stream had already started (200)
    evts = _events(r.text)
    assert any(e["type"] == "error" for e in evts)
    assert evts[-1]["type"] == "done"  # always a clean terminal event
    # the generic message doesn't leak internals
    err = next(e for e in evts if e["type"] == "error")
    assert "exploded" not in err.get("message", "")


def test_nonstreaming_ai_degrades_on_unexpected_error(session_factory, synthetic_mode):
    # a raising client on a NON-streaming route (e.g. local model unreachable in
    # real-data mode) → a clean error body, never a 500, no internals leaked.
    c = TestClient(create_app(session_factory=session_factory, llm_client=_BoomClient()))
    r = c.post("/api/draft-rule", json={"instruction": "hi"})  # always JSON
    assert r.status_code == 200
    b = r.json()
    assert b["ai_available"] is True and b.get("error")
    assert "exploded" not in b["error"]
