"""gl_accounts, gl_transactions, jcl_entries per architecture spec §0.1.

Compliance at the edge (§0): the direct/indirect segregation rules are
schema constraints + triggers, not report-time checks. gl_transactions is
append-only — corrections are reversing entries via superseded_by (the NEW
row points at the original; UPDATE is fully blocked by trigger).

GL↔JCL relate at the balance level (contract_id, period_id, cost element/
type) — deliberately no row-level FK between them; Schedule G reconciles.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from govcon.db.base import Base
from govcon.db.types import Money, SafeNumeric
from govcon.models.enums import CostElement, CostType, db_enum


class GLAccount(Base):
    __tablename__ = "gl_accounts"
    __table_args__ = (
        # SF 1408 criterion A/C pairing: indirect accounts must carry a pool,
        # non-indirect accounts must not. Split into two named CHECKs.
        sa.CheckConstraint(
            "NOT (cost_type = 'indirect' AND pool_assignment IS NULL)",
            name="indirect_requires_pool",
        ),
        sa.CheckConstraint(
            "NOT (cost_type != 'indirect' AND pool_assignment IS NOT NULL)",
            name="pool_only_if_indirect",
        ),
    )

    account_id: Mapped[int] = mapped_column(primary_key=True)
    account_code: Mapped[str] = mapped_column(sa.String(20), unique=True, nullable=False)
    account_name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    cost_type: Mapped[CostType] = mapped_column(
        db_enum(CostType, "cost_type"), nullable=False
    )
    # Labor identification ACROSS cost types (§0.1, v1.1): the §5 "Total
    # Company Labor Base" sums labor everywhere — direct, overhead, and G&A —
    # and only this flag can find labor among indirect accounts.
    is_labor: Mapped[bool] = mapped_column(default=False, nullable=False)
    pool_assignment: Mapped[int | None] = mapped_column(
        sa.ForeignKey("indirect_pools.pool_id")
    )
    far_31_205_citation: Mapped[int | None] = mapped_column(
        sa.ForeignKey("unallowable_cost_categories.category_id")
    )
    gaap_treatment: Mapped[str | None] = mapped_column(sa.Text)  # §4b dual tracking
    cas_treatment: Mapped[str | None] = mapped_column(sa.Text)  # §4b dual tracking


class GLTransaction(Base):
    __tablename__ = "gl_transactions"

    transaction_id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        sa.ForeignKey("gl_accounts.account_id"), nullable=False
    )
    # Nullable only for indirect transactions; direct-needs-contract is
    # enforced by ORM guard + trg_gl_transactions_direct_needs_contract.
    contract_id: Mapped[int | None] = mapped_column(sa.ForeignKey("contracts.contract_id"))
    # Populated on compensation transactions (§4a exec-comp YTD tracker).
    person_id: Mapped[int | None] = mapped_column(sa.ForeignKey("persons.person_id"))
    amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    transaction_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    period_id: Mapped[int] = mapped_column(sa.ForeignKey("periods.period_id"), nullable=False)
    # Structured allowability vector (§3a) — populated by Phase 2.
    allowability_vector: Mapped[dict | None] = mapped_column(sa.JSON)
    source_document: Mapped[str | None] = mapped_column(sa.String(200))
    # The reversing/replacement row points at the ORIGINAL transaction
    # ("this row supersedes that one") — forced by the no-UPDATE trigger.
    superseded_by: Mapped[int | None] = mapped_column(
        sa.ForeignKey("gl_transactions.transaction_id")
    )


class JCLEntry(Base):
    __tablename__ = "jcl_entries"
    __table_args__ = (
        # NOTE: task_order_id is nullable; SQLite/Postgres treat NULLs as
        # distinct in unique constraints, so rows differing only by a NULL
        # task_order_id do not collide. Acceptable for v1; documented.
        sa.UniqueConstraint(
            "contract_id",
            "clin_id",
            "task_order_id",
            "wbs_id",
            "cost_element",
            "period_id",
            name="jcl_accumulation_key",
        ),
    )

    entry_id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(
        sa.ForeignKey("contracts.contract_id"), nullable=False
    )
    clin_id: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    # Populated when the entry accumulates under a task order (an
    # action_type = task_order row); nullable — non-IDIQ contracts have none.
    task_order_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("contract_actions.action_id")
    )
    wbs_id: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    cost_element: Mapped[CostElement] = mapped_column(
        db_enum(CostElement, "cost_element"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    # Units of material or hours of labor — required whenever the entry will
    # be compared against a standard cost (§14).
    quantity: Mapped[Decimal | None] = mapped_column(SafeNumeric(18, 4))
    # End-product output completed this period — the "actual output" base
    # for standard-hours-allowed (§14).
    units_completed: Mapped[Decimal | None] = mapped_column(SafeNumeric(18, 4))
    period_id: Mapped[int] = mapped_column(sa.ForeignKey("periods.period_id"), nullable=False)
