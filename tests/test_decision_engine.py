"""Decision-engine mechanics (Phase 1): safe predicate evaluation, dated
table versioning, threshold resolution modes, and loud authoring errors.

Synthetic tables are INSERTed directly (append-only permits inserts); the
seeded production tables are covered by test_rules_parity / the seed drift
test."""

import datetime

import pytest
import sqlalchemy as sa

from govcon.models import DecisionRule, DecisionTable
from govcon.services.decision_engine import evaluate_table, table_in_force

D = datetime.date


def _mk_table(session, *, name="TOY", version=1, eff=None, sup=None,
              context=None, resolution="eager", initial=None):
    table = DecisionTable(
        table_name=name,
        version=version,
        effective_date=eff,
        superseded_date=sup,
        source_citation="synthetic test table",
        threshold_context=context,
        threshold_resolution=resolution,
        initial_outcome=initial,
    )
    session.add(table)
    session.flush()
    return table


def _mk_rule(session, table, order, key, *, when=None, outcome=None,
             reason=None, stop=True, status=None, citation=None):
    rule = DecisionRule(
        decision_table_id=table.decision_table_id,
        rule_order=order,
        rule_key=key,
        when_ast=when,
        outcome=outcome,
        reason_template=reason,
        stop=stop,
        status=status,
        source_citation=citation,
    )
    session.add(rule)
    session.flush()
    return rule


def test_first_match_stop_and_cascade_continue(session):
    t = _mk_table(session, initial={"grade": "none"})
    _mk_rule(session, t, 1, "warm",
             when={"lhs": {"input": "x"}, "op": "ge", "rhs": 10},
             outcome={"grade": "warm"}, stop=False)
    _mk_rule(session, t, 2, "hot",
             when={"lhs": {"input": "x"}, "op": "ge", "rhs": 100},
             outcome={"grade": "hot"}, stop=True)
    _mk_rule(session, t, 3, "never", when=None, outcome={"grade": "cold"}, stop=True)
    # cascade: rule 1 sets warm, rule 2 upgrades to hot and stops before rule 3
    assert evaluate_table(session, "TOY", D(2026, 1, 1), {"x": 500}).outcome["grade"] == "hot"
    # rule 1 fires, rule 2 doesn't, rule 3 (always) downgrades — order is law
    assert evaluate_table(session, "TOY", D(2026, 1, 1), {"x": 50}).outcome["grade"] == "cold"
    ev = evaluate_table(session, "TOY", D(2026, 1, 1), {"x": 5})
    assert ev.outcome["grade"] == "cold" and ev.fired_rules == ["never"]


def test_predicate_groups_and_unary_ops(session):
    t = _mk_table(session, initial={"hit": False})
    _mk_rule(session, t, 1, "combo",
             when={"any": [
                 {"lhs": {"input": "a"}, "op": "is_true"},
                 {"all": [
                     {"lhs": {"input": "b"}, "op": "is_null"},
                     {"lhs": {"input": "c"}, "op": "ne", "rhs": "x"},
                 ]},
             ]},
             outcome={"hit": True})
    def hit(**inp):
        return evaluate_table(session, "TOY", D(2026, 1, 1), inp).outcome["hit"]
    assert hit(a=True, b=1, c="x") is True          # first any-branch
    assert hit(a=False, b=None, c="y") is True      # all-branch
    assert hit(a=False, b=None, c="x") is False     # all-branch fails on ne
    assert hit(a=False, b=2, c="y") is False        # neither


def test_dated_versions_select_by_window(session):
    t1 = _mk_table(session, version=1, sup=D(2026, 7, 1))
    t2 = _mk_table(session, version=2, eff=D(2026, 7, 1))
    _mk_rule(session, t1, 1, "v1", when=None, outcome={"v": 1})
    _mk_rule(session, t2, 1, "v2", when=None, outcome={"v": 2})
    assert evaluate_table(session, "TOY", D(2026, 6, 30), {}).outcome["v"] == 1
    assert evaluate_table(session, "TOY", D(2026, 7, 1), {}).outcome["v"] == 2
    assert table_in_force(session, "TOY", D(2026, 6, 30)).version == 1


def test_missing_and_overlapping_tables_raise_lookup_error(session):
    with pytest.raises(LookupError, match="no 'NOPE' decision table in force"):
        evaluate_table(session, "NOPE", D(2026, 1, 1), {})
    _mk_table(session, name="DUP", version=1)
    _mk_table(session, name="DUP", version=2)
    with pytest.raises(LookupError, match="overlapping"):
        evaluate_table(session, "DUP", D(2026, 1, 1), {})


def test_authoring_errors_fail_loudly(session):
    t = _mk_table(session)
    _mk_rule(session, t, 1, "bad_op",
             when={"lhs": {"input": "x"}, "op": "spaceship", "rhs": 1})
    with pytest.raises(ValueError, match="unknown predicate op"):
        evaluate_table(session, "TOY", D(2026, 1, 1), {"x": 1})

    t2 = _mk_table(session, name="TOY2")
    _mk_rule(session, t2, 1, "missing_input",
             when={"lhs": {"input": "ghost"}, "op": "eq", "rhs": 1})
    with pytest.raises(ValueError, match="unknown input 'ghost'"):
        evaluate_table(session, "TOY2", D(2026, 1, 1), {"x": 1})

    t3 = _mk_table(session, name="TOY3")
    _mk_rule(session, t3, 1, "none_compare",
             when={"lhs": {"input": "x"}, "op": "gt", "rhs": 1})
    with pytest.raises(ValueError, match="compares against None"):
        evaluate_table(session, "TOY3", D(2026, 1, 1), {"x": None})

    t4 = _mk_table(session, name="TOY4")
    _mk_rule(session, t4, 1, "bad_template", when=None,
             outcome={}, reason=("value is {ghost}"))
    with pytest.raises(ValueError, match="unavailable name"):
        evaluate_table(session, "TOY4", D(2026, 1, 1), {"x": 1})

    t5 = _mk_table(session, name="TOY5", context={"th": "TINA_THRESHOLD"},
                   resolution="sometimes")
    _mk_rule(session, t5, 1, "r", when=None, outcome={})
    with pytest.raises(ValueError, match="unknown threshold_resolution"):
        evaluate_table(session, "TOY5", D(2026, 1, 1), {})

    t6 = _mk_table(session, name="TOY6")
    _mk_rule(session, t6, 1, "undeclared_threshold",
             when={"lhs": {"input": "x"}, "op": "ge", "rhs": {"threshold": "th"}})
    with pytest.raises(ValueError, match="not declared in the table's threshold_context"):
        evaluate_table(session, "TOY6", D(2026, 1, 1), {"x": 1})


def test_eager_vs_on_first_use_threshold_resolution(session):
    """Pre-registered: eager emits the threshold caveat even when the first
    rule stops without referencing it; on_first_use emits nothing when no
    reached rule references a threshold — the CAS small-business semantics."""
    eager = _mk_table(session, name="EAGER", context={"th": "TINA_THRESHOLD"},
                      resolution="eager")
    _mk_rule(session, eager, 1, "early_exit", when=None, outcome={})
    ev = evaluate_table(session, "EAGER", D(2026, 7, 15), {})
    assert str(ev.threshold_values["th"]) == "10000000.00"
    assert any("class_deviation" in c for c in ev.caveats)

    lazy = _mk_table(session, name="LAZY", context={"th": "TINA_THRESHOLD"},
                     resolution="on_first_use")
    _mk_rule(session, lazy, 1, "early_exit", when=None, outcome={}, stop=True)
    _mk_rule(session, lazy, 2, "uses_th",
             when={"lhs": {"input": "x"}, "op": "ge", "rhs": {"threshold": "th"}},
             outcome={})
    ev = evaluate_table(session, "LAZY", D(2026, 7, 15), {"x": 1})
    assert ev.threshold_ids == {} and ev.caveats == []


def test_on_first_use_resolves_whole_context_as_a_unit(session):
    """When any rule references one alias, EVERY declared alias resolves (and
    caveats) together — matching the coded CAS behavior of caveatting both
    trigger and full even when the below-trigger rule stops evaluation."""
    t = _mk_table(session, name="UNIT",
                  context={"trigger": "CAS_CONTRACT_TRIGGER",
                           "full": "CAS_FULL_COVERAGE"},
                  resolution="on_first_use")
    _mk_rule(session, t, 1, "below",
             when={"lhs": {"input": "v"}, "op": "lt", "rhs": {"threshold": "trigger"}},
             outcome={}, stop=True)
    _mk_rule(session, t, 2, "full",
             when={"lhs": {"input": "v"}, "op": "ge", "rhs": {"threshold": "full"}},
             outcome={})
    from decimal import Decimal
    ev = evaluate_table(session, "UNIT", D(2026, 7, 15), {"v": Decimal("1.00")})
    assert set(ev.threshold_ids) == {"trigger", "full"}
    assert len(ev.caveats) == 2  # both post-NDAA rows are statute → 2 caveats


def test_non_final_rule_status_emits_provenance_caveat(session):
    from govcon.models.enums import ThresholdStatus

    t = _mk_table(session, name="PROV")
    _mk_rule(session, t, 1, "encoded_from_nprm", when=None, outcome={},
             status=ThresholdStatus.PROPOSED_RULE, citation="91 FR 00001")
    ev = evaluate_table(session, "PROV", D(2026, 1, 1), {})
    assert any("proposed_rule authority" in c and "91 FR 00001" in c
               for c in ev.caveats)

    t2 = _mk_table(session, name="PROV2")
    _mk_rule(session, t2, 1, "settled", when=None, outcome={},
             status=ThresholdStatus.FINAL_RULE)
    assert evaluate_table(session, "PROV2", D(2026, 1, 1), {}).caveats == []


def test_decision_tables_are_append_only(session):
    """The DB-level guarantee the whole design leans on: rules cannot be
    edited in place — a change is a new version row via a migration."""
    row = session.execute(sa.select(DecisionRule).limit(1)).scalar_one()
    row.rule_key = "tampered"
    with pytest.raises(sa.exc.DatabaseError, match="append-only"):
        session.flush()
    session.rollback()
    table = session.execute(sa.select(DecisionTable).limit(1)).scalar_one()
    session.delete(table)
    with pytest.raises(sa.exc.DatabaseError, match="append-only"):
        session.flush()
    session.rollback()
