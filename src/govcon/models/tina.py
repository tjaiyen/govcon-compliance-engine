"""Phase 8 tables per architecture spec §0.1: tina_baselines (locked once
created), tina_baseline_assumptions (what the sweep compares against), and
tina_sweep_findings (one row per comparison — the full set of rows for a
baseline IS the reproducible sweep log, including HOW each match was made)."""

from __future__ import annotations

import datetime
import enum
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from govcon.db.base import Base
from govcon.db.types import Money
from govcon.models.enums import db_enum


class SweepStatus(str, enum.Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"


class CertificationStatus(str, enum.Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    CERTIFIED = "certified"


class AssumptionType(str, enum.Enum):
    VENDOR_QUOTE = "vendor_quote"
    LABOR_RATE = "labor_rate"
    INDIRECT_RATE = "indirect_rate"


class TINABaseline(Base):
    __tablename__ = "tina_baselines"

    baseline_id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(sa.ForeignKey("contracts.contract_id"), nullable=False)
    # The specific contract action this baseline prices (§8: applicability
    # evaluates per action); nullable only for a baseline on the award itself.
    action_id: Mapped[int | None] = mapped_column(sa.ForeignKey("contract_actions.action_id"))
    baseline_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    price_agreement_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    sweep_status: Mapped[SweepStatus] = mapped_column(
        db_enum(SweepStatus, "sweep_status"), default=SweepStatus.NOT_STARTED
    )
    certification_status: Mapped[CertificationStatus] = mapped_column(
        db_enum(CertificationStatus, "certification_status"),
        default=CertificationStatus.PENDING,
    )
    # baseline_date / price_agreement_date / contract_id / action_id are
    # locked once created (§0.1) — trg_tina_baselines_locked; statuses
    # advance through the workflow and stay updatable.


class TINABaselineAssumption(Base):
    __tablename__ = "tina_baseline_assumptions"

    assumption_id: Mapped[int] = mapped_column(primary_key=True)
    baseline_id: Mapped[int] = mapped_column(
        sa.ForeignKey("tina_baselines.baseline_id"), nullable=False
    )
    assumption_type: Mapped[AssumptionType] = mapped_column(
        db_enum(AssumptionType, "assumption_type"), nullable=False
    )
    description: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    baseline_value: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    source_document: Mapped[str | None] = mapped_column(sa.String(200))


class TINASweepFinding(Base):
    __tablename__ = "tina_sweep_findings"

    finding_id: Mapped[int] = mapped_column(primary_key=True)
    baseline_id: Mapped[int] = mapped_column(
        sa.ForeignKey("tina_baselines.baseline_id"), nullable=False
    )
    assumption_id: Mapped[int] = mapped_column(
        sa.ForeignKey("tina_baseline_assumptions.assumption_id"), nullable=False
    )
    # Nullable: an assumption with no matching subsequent activity still
    # gets a finding row, so the log is complete per assumption.
    subsequent_transaction_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("gl_transactions.transaction_id")
    )
    subsequent_value: Mapped[Decimal | None] = mapped_column(Money())
    variance_amount: Mapped[Decimal | None] = mapped_column(Money())
    # The actual threshold value used for THIS run — recorded, never a bare
    # hard-coded number.
    materiality_threshold_used: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    # HOW the assumption was matched — recorded per finding so the sweep is
    # reproducible end to end, not "reproducible except for matching" (§8).
    match_method: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    flagged: Mapped[bool] = mapped_column(nullable=False, default=False)
