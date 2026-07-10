"""Postgres enforcement layer — plpgsql port of all 26 SQLite triggers
(enterprise Phase 4b; the docs/POSTGRES.md §1 plan, executed).

No-op on SQLite (its triggers were created by 0001/0006/0013/0015/0016 and
stay authoritative there). On Postgres this migration creates the complete
equivalent enforcement layer:

  * one shared ``govcon_block()`` trigger function (RAISE EXCEPTION with the
    message passed as a trigger argument) serves every unconditional block
    trigger AND every conditional trigger whose predicate is pure NEW/OLD
    column comparison — Postgres allows a WHEN clause on the trigger itself
    (SQLite ``x IS NOT y`` becomes ``x IS DISTINCT FROM y``, the exact
    null-safe semantic match);
  * the three cross-table gates (open-period posts, direct-needs-contract)
    need the subquery INSIDE a dedicated plpgsql function, because a
    trigger WHEN clause cannot contain subqueries.

Messages are byte-identical to the SQLite versions — the business-rule
tests assert on them, and one wording is one truth.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, Sequence[str], None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: ERRCODE 23000 (integrity_constraint_violation): psycopg maps SQLSTATE
#: class 23 to IntegrityError — the same SQLAlchemy exception SQLite's
#: RAISE(ABORT) produces, so every existing business-rule test asserts the
#: identical class + message on both backends.
_BLOCK_FN = """
CREATE FUNCTION govcon_block() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '%', TG_ARGV[0] USING ERRCODE = '23000';
END $$ LANGUAGE plpgsql
"""

#: (trigger_name, table, timing/ops, message) — unconditional blocks.
_UNCONDITIONAL = [
    ("trg_audit_trail_no_update", "audit_trail", "BEFORE UPDATE",
     "audit_trail is append-only"),
    ("trg_audit_trail_no_delete", "audit_trail", "BEFORE DELETE",
     "audit_trail is append-only"),
    ("trg_gl_transactions_no_update", "gl_transactions", "BEFORE UPDATE",
     "gl_transactions is append-only; post a reversing entry"),
    ("trg_gl_transactions_no_delete", "gl_transactions", "BEFORE DELETE",
     "gl_transactions is append-only; rows can never be deleted"),
    ("trg_regulatory_thresholds_no_update", "regulatory_thresholds", "BEFORE UPDATE",
     "regulatory_thresholds is append-only; a change is a new row"),
    ("trg_regulatory_thresholds_no_delete", "regulatory_thresholds", "BEFORE DELETE",
     "regulatory_thresholds is append-only; rows are never deleted"),
    ("trg_decision_tables_no_update", "decision_tables", "BEFORE UPDATE",
     "decision_tables is append-only; a change is a new row"),
    ("trg_decision_tables_no_delete", "decision_tables", "BEFORE DELETE",
     "decision_tables is append-only; rows are never deleted"),
    ("trg_decision_rules_no_update", "decision_rules", "BEFORE UPDATE",
     "decision_rules is append-only; a change is a new row"),
    ("trg_decision_rules_no_delete", "decision_rules", "BEFORE DELETE",
     "decision_rules is append-only; rows are never deleted"),
    ("trg_tina_sweep_findings_no_update", "tina_sweep_findings", "BEFORE UPDATE",
     "tina_sweep_findings is append-only; a change is a new row"),
    ("trg_tina_sweep_findings_no_delete", "tina_sweep_findings", "BEFORE DELETE",
     "tina_sweep_findings is append-only; rows are never deleted"),
    ("trg_regulatory_suggestions_no_delete", "regulatory_suggestions", "BEFORE DELETE",
     "regulatory_suggestions rows are never deleted; dismiss instead"),
]

#: (trigger_name, table, timing/ops, WHEN condition, message) — pure NEW/OLD.
_CONDITIONAL = [
    ("trg_contracts_immutable_cols", "contracts", "BEFORE UPDATE",
     "NEW.award_date IS DISTINCT FROM OLD.award_date "
     "OR NEW.tina_threshold_snapshot IS DISTINCT FROM OLD.tina_threshold_snapshot "
     "OR NEW.tina_threshold_id IS DISTINCT FROM OLD.tina_threshold_id "
     "OR NEW.cas_trigger_threshold_snapshot IS DISTINCT FROM OLD.cas_trigger_threshold_snapshot "
     "OR NEW.cas_trigger_threshold_id IS DISTINCT FROM OLD.cas_trigger_threshold_id",
     "contract award/threshold fields are immutable after insert; create a new contract version"),
    ("trg_cost_accounting_practices_frozen", "cost_accounting_practices", "BEFORE UPDATE",
     "NEW.practice_area IS DISTINCT FROM OLD.practice_area "
     "OR NEW.disclosed_treatment IS DISTINCT FROM OLD.disclosed_treatment "
     "OR NEW.account_code_prefix IS DISTINCT FROM OLD.account_code_prefix "
     "OR NEW.effective_date IS DISTINCT FROM OLD.effective_date",
     "cost_accounting_practices substance is frozen; a change is a new version row"),
    ("trg_indirect_pools_identity_frozen", "indirect_pools", "BEFORE UPDATE",
     "NEW.pool_name IS DISTINCT FROM OLD.pool_name "
     "OR NEW.fiscal_year IS DISTINCT FROM OLD.fiscal_year "
     "OR NEW.rate_type IS DISTINCT FROM OLD.rate_type",
     "indirect_pools identity (name/fiscal_year/rate_type) is frozen"),
    ("trg_indirect_pools_locked_frozen", "indirect_pools", "BEFORE UPDATE",
     "OLD.status = 'locked' AND (NEW.calculated_rate IS DISTINCT FROM OLD.calculated_rate "
     "OR NEW.pool_balance IS DISTINCT FROM OLD.pool_balance "
     "OR NEW.allocation_base_amount IS DISTINCT FROM OLD.allocation_base_amount)",
     "a LOCKED rate cannot be recalculated (§11 item 4); a correction is a new period-adjustment row"),
    ("trg_overhead_budgets_frozen", "overhead_budgets", "BEFORE UPDATE",
     "NEW.fixed_overhead_budget IS DISTINCT FROM OLD.fixed_overhead_budget "
     "OR NEW.variable_overhead_rate IS DISTINCT FROM OLD.variable_overhead_rate "
     "OR NEW.effective_date IS DISTINCT FROM OLD.effective_date",
     "overhead_budgets substance is frozen; a change is a new dated row"),
    ("trg_standard_costs_frozen", "standard_costs", "BEFORE UPDATE",
     "NEW.cost_element IS DISTINCT FROM OLD.cost_element "
     "OR NEW.operation_or_product_code IS DISTINCT FROM OLD.operation_or_product_code "
     "OR NEW.standard_quantity IS DISTINCT FROM OLD.standard_quantity "
     "OR NEW.standard_rate IS DISTINCT FROM OLD.standard_rate "
     "OR NEW.effective_date IS DISTINCT FROM OLD.effective_date",
     "standard_costs substance is frozen; a change is a new dated row"),
    ("trg_tina_baselines_locked", "tina_baselines", "BEFORE UPDATE",
     "NEW.baseline_date IS DISTINCT FROM OLD.baseline_date "
     "OR NEW.price_agreement_date IS DISTINCT FROM OLD.price_agreement_date "
     "OR NEW.contract_id IS DISTINCT FROM OLD.contract_id "
     "OR NEW.action_id IS DISTINCT FROM OLD.action_id",
     "tina baseline is locked once created"),
    ("trg_periods_close_requires_reconciliation", "periods", "BEFORE UPDATE",
     "NEW.status = 'closed' AND OLD.status != 'closed' "
     "AND NEW.reconciliation_status != 'passed'",
     "period cannot close until the three-way reconciliation passes"),
    ("trg_periods_no_reopen", "periods", "BEFORE UPDATE",
     "OLD.status = 'closed' AND NEW.status != 'closed'",
     "a closed period cannot reopen (no v1 reopen procedure)"),
    ("trg_audit_notifications_review_gate", "audit_notifications", "BEFORE UPDATE",
     "NEW.status = 'submitted' AND (NEW.reviewed_by IS NULL OR NEW.reviewed_at IS NULL)",
     "cannot submit without a management-review sign-off"),
]

#: Cross-table gates: the subquery must live INSIDE a plpgsql function
#: (a trigger WHEN clause cannot contain subqueries).
_GATE_FNS = [
    """
CREATE FUNCTION govcon_gate_open_period() RETURNS trigger AS $$
BEGIN
  IF (SELECT status FROM periods WHERE period_id = NEW.period_id) != 'open' THEN
    RAISE EXCEPTION 'cannot post to a closed period' USING ERRCODE = '23000';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql
""",
    """
CREATE FUNCTION govcon_gate_direct_needs_contract() RETURNS trigger AS $$
BEGIN
  IF NEW.contract_id IS NULL
     AND (SELECT cost_type FROM gl_accounts WHERE account_id = NEW.account_id) = 'direct' THEN
    RAISE EXCEPTION 'a direct-cost transaction must reference a contract (SF 1408 criterion B)' USING ERRCODE = '23000';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql
""",
]
_GATE_TRIGGERS = [
    ("trg_gl_transactions_open_period", "gl_transactions",
     "govcon_gate_open_period"),
    ("trg_jcl_entries_open_period", "jcl_entries",
     "govcon_gate_open_period"),
    ("trg_gl_transactions_direct_needs_contract", "gl_transactions",
     "govcon_gate_direct_needs_contract"),
]


def _q(msg: str) -> str:
    return msg.replace("'", "''")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite's own triggers (0001/0006/0013/0015/0016) are authoritative there
    op.execute(sa.text(_BLOCK_FN))
    for fn in _GATE_FNS:
        op.execute(sa.text(fn))
    for name, table, timing, msg in _UNCONDITIONAL:
        op.execute(sa.text(
            f"CREATE TRIGGER {name} {timing} ON {table} FOR EACH ROW "
            f"EXECUTE FUNCTION govcon_block('{_q(msg)}')"
        ))
    for name, table, timing, when, msg in _CONDITIONAL:
        op.execute(sa.text(
            f"CREATE TRIGGER {name} {timing} ON {table} FOR EACH ROW "
            f"WHEN ({when}) EXECUTE FUNCTION govcon_block('{_q(msg)}')"
        ))
    for name, table, fn in _GATE_TRIGGERS:
        op.execute(sa.text(
            f"CREATE TRIGGER {name} BEFORE INSERT ON {table} FOR EACH ROW "
            f"EXECUTE FUNCTION {fn}()"
        ))
    # 13 + 10 + 3 = 26 — the full census in docs/POSTGRES.md §1.
    assert len(_UNCONDITIONAL) + len(_CONDITIONAL) + len(_GATE_TRIGGERS) == 26


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for name, table, *_ in _UNCONDITIONAL + _CONDITIONAL:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {name} ON {table}"))
    for name, table, _fn in _GATE_TRIGGERS:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {name} ON {table}"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS govcon_block()"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS govcon_gate_open_period()"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS govcon_gate_direct_needs_contract()"))
