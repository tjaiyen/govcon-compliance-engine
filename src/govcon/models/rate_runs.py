"""Phase 4 tables per architecture spec §0.1: rate_calculation_runs (the §5
stamping rule's landing place — one row per rate/burdened-cost calculation,
reproducible from its own snapshot) and rate_true_ups (the year-end
provisional-to-final settlement delta)."""

from __future__ import annotations

import datetime
import enum
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from govcon.db.base import Base
from govcon.db.types import Money, Rate, SafeNumeric
from govcon.models.enums import db_enum


class RunType(str, enum.Enum):
    POOL_RATE = "pool_rate"
    BURDENED_COST = "burdened_cost"


class RateCalculationRun(Base):
    __tablename__ = "rate_calculation_runs"

    run_id: Mapped[int] = mapped_column(primary_key=True)
    run_type: Mapped[RunType] = mapped_column(db_enum(RunType, "run_type"), nullable=False)
    pool_id: Mapped[int | None] = mapped_column(sa.ForeignKey("indirect_pools.pool_id"))
    calculated_at: Mapped[datetime.datetime] = mapped_column(sa.DateTime, nullable=False)
    # Canonical JSON of every input used — the §5 stamp; reconstruct_run()
    # reproduces the result from this field alone.
    inputs_snapshot: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Rate (4dp) or money (2dp) depending on run_type; 6dp column holds both
    # losslessly (Decimal comparison ignores trailing zeros).
    result_value: Mapped[Decimal] = mapped_column(SafeNumeric(18, 6), nullable=False)
    regulatory_threshold_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("regulatory_thresholds.threshold_id")
    )


class RateTrueUp(Base):
    __tablename__ = "rate_true_ups"

    true_up_id: Mapped[int] = mapped_column(primary_key=True)
    # The PROVISIONAL-rate row; the actual_final row is located by the same
    # pool_name/fiscal_year with rate_type = actual_final (§0.1).
    pool_id: Mapped[int] = mapped_column(sa.ForeignKey("indirect_pools.pool_id"), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(nullable=False)
    provisional_rate_snapshot: Mapped[Decimal] = mapped_column(Rate(), nullable=False)
    final_rate_snapshot: Mapped[Decimal] = mapped_column(Rate(), nullable=False)
    # Signed: positive = under-billed at provisional (additional amount due);
    # negative = over-billed (credit owed).
    delta_amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    billing_impact_amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    calculated_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
