"""AI rule-authoring (Pattern 3): the AI DRAFTS a decision-table rule and
validates it structurally; it can apply NOTHING (B53). Two layers:
  * unit — the pure validator (grammar parse, reference extraction, no execution).
  * endpoint — the AI drafts + validates; the response carries the draft and
    requires_human_migration; the DB is provably unchanged (zero rows written).
"""

import inspect

import sqlalchemy as sa
from fastapi.testclient import TestClient

from govcon.ai.registry import DRAFT_RULE_TOOLS
from govcon.api import create_app
from govcon.models import DecisionRule, DecisionTable
from govcon.services import rule_authoring
from tests.ai.conftest import final_turn, tool_turn

VALID_DRAFT = {
    "rule_key": "above_new_threshold",
    "when_ast": {
        "all": [
            {"op": "ge", "lhs": {"input": "contract_value"}, "rhs": {"threshold": "cas_full"}},
            {"op": "is_false", "lhs": {"input": "is_small"}},
        ]
    },
    "outcome": {"tier": "full"},
    "reason_template": "Value {contract_value} at or above the {cas_full} bar",
    "stop": True,
}


def _client(session_factory, fake):
    return TestClient(create_app(session_factory=session_factory, llm_client=fake))


# --------------------------------------------------------------- unit: validator
def test_valid_when_ast_parses_and_extracts_references():
    errors, refs = rule_authoring.validate_when_ast(VALID_DRAFT["when_ast"])
    assert errors == []
    assert refs == {"inputs": ["contract_value", "is_small"], "thresholds": ["cas_full"]}


def test_null_when_ast_is_a_valid_default_row():
    assert rule_authoring.validate_when_ast(None) == ([], {"inputs": [], "thresholds": []})


def test_unknown_op_is_caught():
    errors, _ = rule_authoring.validate_when_ast(
        {"op": "between", "lhs": {"input": "v"}, "rhs": 5}
    )
    assert any("unknown predicate op" in e for e in errors)


def test_comparison_missing_operand_is_caught():
    errors, _ = rule_authoring.validate_when_ast({"op": "ge", "lhs": {"input": "v"}})
    assert any("requires 'rhs'" in e for e in errors)


def test_malformed_operand_is_caught():
    errors, _ = rule_authoring.validate_when_ast(
        {"op": "eq", "lhs": {"input": "v", "threshold": "t"}, "rhs": 1}
    )
    assert any("malformed operand" in e for e in errors)


def test_empty_group_is_caught():
    errors, _ = rule_authoring.validate_when_ast({"all": []})
    assert any("non-empty list" in e for e in errors)


def test_reason_template_unbalanced_brace_is_caught():
    assert rule_authoring.validate_reason_template("value {x")  # stray '{'
    assert rule_authoring.validate_reason_template("value x}")  # stray '}'
    assert rule_authoring.validate_reason_template("value {x}") == []  # ok


def test_validate_draft_rule_requires_key_and_always_flags_migration():
    good = rule_authoring.validate_draft_rule(VALID_DRAFT)
    assert good["valid"] is True and good["requires_human_migration"] is True

    bad = rule_authoring.validate_draft_rule({"when_ast": None, "stop": "yes"})
    assert bad["valid"] is False
    assert any("rule_key" in e for e in bad["errors"])
    assert any("stop" in e for e in bad["errors"])
    assert bad["requires_human_migration"] is True  # even an invalid draft


# ----------------------------------------------------- B53: no write / no apply
def test_rule_authoring_has_no_write_or_migration_path():
    src = inspect.getsource(rule_authoring)
    for forbidden in ("alembic", "session.add", ".commit(", "op.create", "op.execute", "op.batch"):
        assert forbidden not in src, f"rule_authoring must not reference {forbidden!r}"


def test_draft_rule_tools_have_no_write_or_evaluate_tool():
    for name in DRAFT_RULE_TOOLS:
        assert not any(x in name for x in ("write", "apply", "create", "evaluate", "save"))


# ------------------------------------------------------------------- endpoint
def test_draft_rule_returns_validated_draft_for_human_migration(
    session_factory, make_fake, synthetic_mode
):
    fake = make_fake([
        tool_turn(("t1", "validate_draft_rule", {"rule": VALID_DRAFT})),
        final_turn("This draft rule fires for large non-small awards; it needs a "
                   "human-reviewed migration before it can take effect."),
    ])
    c = _client(session_factory, fake)
    body = c.post("/api/draft-rule",
                  json={"instruction": "Add a rule for the new CAS full-coverage bar"}).json()
    assert body["ai_available"] is True
    assert body["requires_human_migration"] is True
    assert body["validation"]["valid"] is True
    assert body["draft"]["rule_key"] == "above_new_threshold"


def test_draft_rule_surfaces_validation_errors(session_factory, make_fake, synthetic_mode):
    invalid = {"rule_key": "x", "when_ast": {"op": "between", "lhs": {"input": "v"}, "rhs": 5}}
    fake = make_fake([
        tool_turn(("t1", "validate_draft_rule", {"rule": invalid})),
        final_turn("Proposed a draft; the validator flagged it — needs fixing then a migration."),
    ])
    c = _client(session_factory, fake)
    body = c.post("/api/draft-rule", json={"instruction": "..."}).json()
    assert body["validation"]["valid"] is False and body["validation"]["errors"]
    assert body["requires_human_migration"] is True


def test_draft_rule_writes_nothing(session_factory, make_fake, synthetic_mode):
    def counts():
        with session_factory() as s:
            return (
                s.execute(sa.select(sa.func.count()).select_from(DecisionTable)).scalar(),
                s.execute(sa.select(sa.func.count()).select_from(DecisionRule)).scalar(),
            )

    before = counts()
    fake = make_fake([
        tool_turn(("t1", "validate_draft_rule", {"rule": VALID_DRAFT})),
        final_turn("draft only; requires a human migration"),
    ])
    c = _client(session_factory, fake)
    c.post("/api/draft-rule", json={"instruction": "..."})
    assert counts() == before  # B53: the AI path wrote nothing to the rule tables


def test_draft_rule_gate_blocks_real_mode_before_any_llm_call(
    session_factory, make_fake, monkeypatch
):
    monkeypatch.setenv("GOVCON_DATA_MODE", "real")
    fake = make_fake([final_turn("should never be reached")])
    c = _client(session_factory, fake)
    body = c.post("/api/draft-rule", json={"instruction": "..."}).json()
    assert body["ai_available"] is False
    assert fake.calls == []


def test_draft_rule_unavailable_when_no_client(session_factory):
    c = TestClient(create_app(session_factory=session_factory))  # llm_client=None
    body = c.post("/api/draft-rule", json={"instruction": "..."}).json()
    assert body["ai_available"] is False
