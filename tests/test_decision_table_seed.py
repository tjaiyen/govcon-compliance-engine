"""Seed drift test (mirrors test_threshold_seed.py): the frozen literals in
migration 0015 and the importable constants in seeds/decision_tables.py can
never diverge silently — DB rows (from the migration) must equal the
constants field-for-field."""

import sqlalchemy as sa

from govcon.models import DecisionRule, DecisionTable
from govcon.seeds.decision_tables import DECISION_TABLE_SEEDS


def test_seeded_tables_match_constants(session):
    db_tables = (
        session.execute(
            sa.select(DecisionTable).order_by(DecisionTable.decision_table_id)
        )
        .scalars()
        .all()
    )
    assert len(db_tables) == len(DECISION_TABLE_SEEDS) == 2
    for row, spec in zip(db_tables, DECISION_TABLE_SEEDS):
        assert row.table_name == spec["table_name"]
        assert row.version == spec["version"]
        assert row.effective_date == spec["effective_date"]
        assert row.superseded_date == spec["superseded_date"]
        assert row.source_citation == spec["source_citation"]
        assert row.threshold_context == spec["threshold_context"]
        assert row.threshold_resolution == spec["threshold_resolution"]
        assert row.initial_outcome == spec["initial_outcome"]


def test_seeded_rules_match_constants(session):
    db_tables = (
        session.execute(
            sa.select(DecisionTable).order_by(DecisionTable.decision_table_id)
        )
        .scalars()
        .all()
    )
    total = 0
    for row, spec in zip(db_tables, DECISION_TABLE_SEEDS):
        db_rules = (
            session.execute(
                sa.select(DecisionRule)
                .where(DecisionRule.decision_table_id == row.decision_table_id)
                .order_by(DecisionRule.rule_order)
            )
            .scalars()
            .all()
        )
        assert len(db_rules) == len(spec["rules"])
        for db_rule, rule_spec in zip(db_rules, spec["rules"]):
            assert db_rule.rule_order == rule_spec["rule_order"]
            assert db_rule.rule_key == rule_spec["rule_key"]
            assert db_rule.when_ast == rule_spec["when_ast"]
            assert db_rule.outcome == rule_spec["outcome"]
            assert db_rule.reason_template == rule_spec["reason_template"]
            assert db_rule.stop == rule_spec["stop"]
            status = None if db_rule.status is None else db_rule.status.value
            assert status == rule_spec["status"]
            assert db_rule.source_citation == rule_spec["source_citation"]
        total += len(db_rules)
    assert total == 15  # CAS 6 + TINA 9 — restated so shrinkage fails loudly


def test_the_one_non_final_rule_rides_the_reverify_watch(session):
    """Pre-registered: exactly one seeded rule is non-final (the CAS
    cumulative window, PROPOSED 9903.201-2) and it appears on the
    reverification watch list as a non_final_decision_rule item."""
    import datetime

    from govcon.services.reverification import reverification_items

    items = [
        i
        for i in reverification_items(session, datetime.date(2026, 7, 9))
        if i.kind == "non_final_decision_rule"
    ]
    assert len(items) == 1
    assert "full_coverage_cumulative" in items[0].description
    assert "9903.201-2" in items[0].description
    assert items[0].due is False  # standing watch, never blocks
