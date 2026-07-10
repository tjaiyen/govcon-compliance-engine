"""Hard-graded stress-test fixes for the AI safety layer (this pass).

Each test is the exact reproduction from the grading session — pre-registered
(B35): the grounding verifier no longer crashes and no longer leaks bare /
spelled / shorthand hallucinations; extreme money is rejected; the per-request
cost ceiling trips.
"""


from fastapi.testclient import TestClient

from govcon.ai.dispatch import GroundingLedger
from govcon.ai.grounding import GroundingVerifier
from govcon.api import create_app
from tests.ai.conftest import FakeLLMClient, tool_turn


def _ledger(values=(), citations=()):
    lg = GroundingLedger()
    lg.values.update(values)
    lg.citations.extend(citations)
    return lg


# --- grounding: no crash, no false-negatives ----------------------------------


def test_grounding_never_crashes_on_extreme_numbers():
    v = GroundingVerifier()
    empty = _ledger()
    for prose in ("the value is " + "9" * 400, "the bar is 1e400 dollars",
                  "$" + "1," * 3000 + "0", "NaN and Infinity dollars"):
        r = v.verify(prose, empty)  # must not raise
        assert isinstance(r.verified, bool)


def test_grounding_catches_bare_large_number():
    # a hallucinated threshold with no $/comma/decimal used to slip
    r = GroundingVerifier().verify("the trigger is 50000000", _ledger())
    assert not r.verified and any("50000000" in x for x in r.violations)


def test_grounding_catches_spelled_out_amount():
    r = GroundingVerifier().verify("the threshold is fifty million dollars", _ledger())
    assert not r.verified
    r2 = GroundingVerifier().verify("about one hundred fifty million", _ledger())
    assert not r2.verified


def test_grounding_grounds_spelled_amount_that_matches_ledger():
    lg = _ledger(values={"50000000.00"})
    r = GroundingVerifier().verify("roughly fifty million dollars", lg)
    assert r.verified, r.violations


def test_grounding_catches_citation_shorthand():
    r = GroundingVerifier().verify("governed by FAR15.403-4", _ledger())
    assert not r.verified and any("FAR15" in x for x in r.violations)


def test_grounding_still_ignores_years_and_counts():
    r = GroundingVerifier().verify(
        "In 2026 all four of the statutory exceptions were evaluated.", _ledger())
    assert r.verified, r.violations


def test_grounding_huge_bare_number_is_flagged_not_ignored():
    r = GroundingVerifier().verify("the value is " + "9" * 400, _ledger())
    assert not r.verified


# --- extreme money rejected at the boundary -----------------------------------


def test_extreme_money_rejected_by_cas_endpoint(session_factory):
    c = TestClient(create_app(session_factory=session_factory))
    for bad in ("1e400", "NaN", "Infinity", "999999999999999999999"):
        body = c.post("/api/cas", json={
            "award_date": "2026-07-15", "contract_value": bad,
            "contractor_size": "other_than_small",
        }).json()
        assert body["available"] is False and "range" in body["message"]


def test_extreme_money_rejected_in_ai_tool_dispatch(session):
    from govcon.ai.dispatch import dispatch
    r = dispatch(session, "determine_cas_coverage",
                 {"award_date": "2026-07-15", "contract_value": "1e400",
                  "contractor_size": "other_than_small"}, GroundingLedger())
    assert r.is_error and "range" in r.result["error"]


# --- AI cost ceiling enforced at the HTTP layer -------------------------------


def test_ask_enforces_cost_ceiling(session_factory, monkeypatch):
    monkeypatch.setenv("GOVCON_DATA_MODE", "synthetic")
    monkeypatch.setenv("GOVCON_AI_MAX_USD", "0.00")  # any real call exceeds it
    # a fake that would loop forever if uncapped: always requests a tool
    script = [
        tool_turn(("t1", "lookup_glossary", {"term": "cas"}),
                  input_tokens=100000, output_tokens=100000)
        for _ in range(10)
    ]
    fake = FakeLLMClient(script)
    c = TestClient(create_app(session_factory=session_factory, llm_client=fake))
    body = c.post("/api/ask", json={"question": "explain CAS forever"}).json()
    assert body.get("cost_exceeded") is True
    # the ceiling stopped the loop early — not all 10 scripted turns ran
    assert len(fake.calls) < 10


def test_default_cost_ceiling_allows_a_normal_answer(session_factory, monkeypatch):
    monkeypatch.setenv("GOVCON_DATA_MODE", "synthetic")
    monkeypatch.delenv("GOVCON_AI_MAX_USD", raising=False)  # default $0.50
    from tests.ai.conftest import final_turn
    fake = FakeLLMClient([
        tool_turn(("t1", "determine_cas_coverage",
                   {"award_date": "2026-05-15", "contract_value": "12000000.00",
                    "contractor_size": "other_than_small"})),
        final_turn("This $12,000,000.00 award has modified CAS coverage."),
    ])
    c = TestClient(create_app(session_factory=session_factory, llm_client=fake))
    body = c.post("/api/ask", json={"question": "does CAS apply?"}).json()
    assert body["ai_available"] is True and "cost_exceeded" not in body
    assert body["grounding"]["verified"] is True
