"""stress-test fixes: is_compensation + immutability triggers

Revision ID: 0013
Revises: 0012

Closes stress-test findings:
- is_compensation on gl_accounts (scopes the exec-comp YTD to real
  compensation, not every person transaction).
- DB-level immutability triggers for the tables that DOCUMENTED
  append-only/versioned semantics but had no enforcement — restoring the
  project's two-layer thesis (typed ORM error + DB trigger). A probe
  confirmed a raw UPDATE to regulatory_thresholds.value succeeded before
  this migration. Triggers are the SQLite RAISE(ABORT) form; a Postgres
  port would use REVOKE + plpgsql equivalents.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# gl_accounts is rebuilt by SQLite batch mode; the gl_transactions trigger
# that references it must be dropped and recreated verbatim around the alter
# (same dance as migration 0011).
DIRECT_NEEDS_CONTRACT_TRIGGER = """
CREATE TRIGGER trg_gl_transactions_direct_needs_contract BEFORE INSERT ON gl_transactions
FOR EACH ROW
WHEN NEW.contract_id IS NULL
  AND (SELECT cost_type FROM gl_accounts WHERE account_id = NEW.account_id) = 'direct'
BEGIN SELECT RAISE(ABORT, 'a direct-cost transaction must reference a contract (SF 1408 criterion B)'); END
"""

# Append-only: no UPDATE, no DELETE. Only tables that are genuinely
# write-once — regulatory_thresholds supersedes by seeding complete dated
# rows (never editing), and tina_sweep_findings is the immutable sweep log.
# NOT included: standard_costs / overhead_budgets set superseded_date on
# supersession (versioned, below); practice_change_events has a
# flagged→resolved workflow; audit_notifications/periods advance status.
APPEND_ONLY_TABLES = ("regulatory_thresholds", "tina_sweep_findings")

# Versioned: substantive fields frozen, but superseded_by / superseded_date
# / status lifecycle columns stay updatable. Per-table below.
IMMUTABILITY_TRIGGERS = [
    # standard_costs: a standard change is a new row; freeze the substance,
    # allow only superseded_date to be set when a successor supersedes it.
    """
    CREATE TRIGGER trg_standard_costs_frozen BEFORE UPDATE ON standard_costs
    FOR EACH ROW
    WHEN NEW.cost_element IS NOT OLD.cost_element
      OR NEW.operation_or_product_code IS NOT OLD.operation_or_product_code
      OR NEW.standard_quantity IS NOT OLD.standard_quantity
      OR NEW.standard_rate IS NOT OLD.standard_rate
      OR NEW.effective_date IS NOT OLD.effective_date
    BEGIN SELECT RAISE(ABORT, 'standard_costs substance is frozen; a change is a new dated row'); END
    """,
    # overhead_budgets: same discipline as standard_costs.
    """
    CREATE TRIGGER trg_overhead_budgets_frozen BEFORE UPDATE ON overhead_budgets
    FOR EACH ROW
    WHEN NEW.fixed_overhead_budget IS NOT OLD.fixed_overhead_budget
      OR NEW.variable_overhead_rate IS NOT OLD.variable_overhead_rate
      OR NEW.effective_date IS NOT OLD.effective_date
    BEGIN SELECT RAISE(ABORT, 'overhead_budgets substance is frozen; a change is a new dated row'); END
    """,
    # indirect_pools: identity (name/fy/rate_type) is ALWAYS frozen; the
    # computed rate/balance/base are recomputable while the pool is still
    # PENDING but frozen once the pool is LOCKED by period close (§11 item 4:
    # "rate calculations for a closed period are locked — no retroactive
    # recalculation"). status/superseded_by (the lifecycle) stay updatable.
    """
    CREATE TRIGGER trg_indirect_pools_identity_frozen BEFORE UPDATE ON indirect_pools
    FOR EACH ROW
    WHEN NEW.pool_name IS NOT OLD.pool_name
      OR NEW.fiscal_year IS NOT OLD.fiscal_year
      OR NEW.rate_type IS NOT OLD.rate_type
    BEGIN SELECT RAISE(ABORT, 'indirect_pools identity (name/fiscal_year/rate_type) is frozen'); END
    """,
    """
    CREATE TRIGGER trg_indirect_pools_locked_frozen BEFORE UPDATE ON indirect_pools
    FOR EACH ROW
    WHEN OLD.status = 'locked'
      AND (NEW.calculated_rate IS NOT OLD.calculated_rate
        OR NEW.pool_balance IS NOT OLD.pool_balance
        OR NEW.allocation_base_amount IS NOT OLD.allocation_base_amount)
    BEGIN SELECT RAISE(ABORT, 'a LOCKED rate cannot be recalculated (§11 item 4); a correction is a new period-adjustment row'); END
    """,
    # cost_accounting_practices: a disclosed-practice change is a new row +
    # a practice_change_events record; freeze the substance, allow only
    # superseded_by to point at the successor.
    """
    CREATE TRIGGER trg_cost_accounting_practices_frozen BEFORE UPDATE ON cost_accounting_practices
    FOR EACH ROW
    WHEN NEW.practice_area IS NOT OLD.practice_area
      OR NEW.disclosed_treatment IS NOT OLD.disclosed_treatment
      OR NEW.account_code_prefix IS NOT OLD.account_code_prefix
      OR NEW.effective_date IS NOT OLD.effective_date
    BEGIN SELECT RAISE(ABORT, 'cost_accounting_practices substance is frozen; a change is a new version row'); END
    """,
]


def _append_only_ddl(table: str) -> list[str]:
    return [
        f"CREATE TRIGGER trg_{table}_no_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} is append-only; a change is a new row'); END",
        f"CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} is append-only; rows are never deleted'); END",
    ]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":  # pragma: no cover - PG triggers land in 0017
        # The schema change is dialect-neutral; the SQLite trigger dance below
        # is not (and its plpgsql equivalents are created by migration 0017).
        with op.batch_alter_table("gl_accounts", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "is_compensation", sa.Boolean(), nullable=False, server_default=sa.false()
                )
            )
        return

    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_gl_transactions_direct_needs_contract"))
    with op.batch_alter_table("gl_accounts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_compensation", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.execute(sa.text(DIRECT_NEEDS_CONTRACT_TRIGGER))

    for table in APPEND_ONLY_TABLES:
        for ddl in _append_only_ddl(table):
            op.execute(sa.text(ddl))
    for ddl in IMMUTABILITY_TRIGGERS:
        op.execute(sa.text(ddl))


def downgrade() -> None:
    for ddl in IMMUTABILITY_TRIGGERS:
        name = ddl.split("CREATE TRIGGER ")[1].split(" ")[0]
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {name}"))
    for table in APPEND_ONLY_TABLES:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table}_no_update"))
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table}_no_delete"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_gl_transactions_direct_needs_contract"))
    with op.batch_alter_table("gl_accounts", schema=None) as batch_op:
        batch_op.drop_column("is_compensation")
    op.execute(sa.text(DIRECT_NEEDS_CONTRACT_TRIGGER))
