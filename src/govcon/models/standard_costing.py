"""Phase 12 tables per architecture spec §0.1: standard_costs (dated/
versioned standards matched to jcl_entries by operation_or_product_code —
wbs_id for v1, never a 1:1 FK), cost_variances (single sign convention;
favorable ALWAYS derived), and overhead_budgets (the flexible-budget
components neither standard_costs nor anything else could supply)."""

from __future__ import annotations

import datetime
import enum
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from govcon.db.base import Base
from govcon.db.types import Money, SafeNumeric
from govcon.models.enums import db_enum


class StandardCostElement(str, enum.Enum):
    MATERIAL = "material"
    LABOR = "labor"
    OVERHEAD = "overhead"


class VarianceType(str, enum.Enum):
    MATERIAL_PRICE = "material_price"
    MATERIAL_USAGE = "material_usage"
    LABOR_RATE = "labor_rate"
    LABOR_EFFICIENCY = "labor_efficiency"
    OVERHEAD_VOLUME = "overhead_volume"
    OVERHEAD_SPENDING = "overhead_spending"


class StandardCost(Base):
    __tablename__ = "standard_costs"

    standard_cost_id: Mapped[int] = mapped_column(primary_key=True)
    cost_element: Mapped[StandardCostElement] = mapped_column(
        db_enum(StandardCostElement, "standard_cost_element"), nullable=False
    )
    # Nullable: a standard can be job-specific or company-wide.
    contract_id: Mapped[int | None] = mapped_column(sa.ForeignKey("contracts.contract_id"))
    # Matches jcl_entries by shared code (wbs_id for v1) — one standard is
    # compared against many entries; deliberately NOT a FK to one entry.
    operation_or_product_code: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    standard_quantity: Mapped[Decimal] = mapped_column(SafeNumeric(18, 4), nullable=False)
    standard_rate: Mapped[Decimal] = mapped_column(SafeNumeric(18, 4), nullable=False)
    effective_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    superseded_date: Mapped[datetime.date | None] = mapped_column(sa.Date)
    # A standard-cost change is a new row, never an edit to history.


class CostVariance(Base):
    __tablename__ = "cost_variances"

    variance_id: Mapped[int] = mapped_column(primary_key=True)
    standard_cost_id: Mapped[int] = mapped_column(
        sa.ForeignKey("standard_costs.standard_cost_id"), nullable=False
    )
    period_id: Mapped[int] = mapped_column(sa.ForeignKey("periods.period_id"), nullable=False)
    jcl_entry_id: Mapped[int | None] = mapped_column(sa.ForeignKey("jcl_entries.entry_id"))
    variance_type: Mapped[VarianceType] = mapped_column(
        db_enum(VarianceType, "variance_type"), nullable=False
    )
    standard_amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    actual_amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    # THE convention, stated once (§14): standard − actual; positive = favorable.
    variance_amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    # ALWAYS derived as (variance_amount > 0) at write time, never set
    # independently — two fields for one fact is a drift risk otherwise.
    favorable: Mapped[bool] = mapped_column(nullable=False)


class OverheadBudget(Base):
    __tablename__ = "overhead_budgets"

    budget_id: Mapped[int] = mapped_column(primary_key=True)
    fiscal_year: Mapped[int] = mapped_column(nullable=False)
    contract_id: Mapped[int | None] = mapped_column(sa.ForeignKey("contracts.contract_id"))
    fixed_overhead_budget: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    variable_overhead_rate: Mapped[Decimal] = mapped_column(SafeNumeric(18, 4), nullable=False)
    effective_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    superseded_date: Mapped[datetime.date | None] = mapped_column(sa.Date)
    # "Budgeted Overhead (at standard hours allowed)" = fixed_overhead_budget
    # + variable_overhead_rate × SHA (§14 flexible budget).
