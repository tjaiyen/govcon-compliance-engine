"""Phase 9 tables per architecture spec §0.1: rea_cda_actions (+ line
items — certification totals are COMPUTED from line items, never entered)
and eichleay_claims (inputs stored alongside outputs so every claim is
reproducible from its own row)."""

from __future__ import annotations

import datetime
import enum
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from govcon.db.base import Base
from govcon.db.types import Money, SafeNumeric
from govcon.models.enums import db_enum
from govcon.models.tina import CertificationStatus


class REACDAType(str, enum.Enum):
    REA = "rea"
    CDA_CLAIM = "cda_claim"


class ClaimStatus(str, enum.Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class REACDAAction(Base):
    __tablename__ = "rea_cda_actions"

    action_id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(sa.ForeignKey("contracts.contract_id"), nullable=False)
    action_type: Mapped[REACDAType] = mapped_column(
        db_enum(REACDAType, "rea_cda_action_type"), nullable=False
    )
    # Computed by summing rea_cda_line_items — never entered directly (the
    # same computed-not-entered discipline as pool_balance). The decrease
    # total is stored as the (negative) sum of negative line items; the
    # certification test applies ABS to both, never the net.
    cost_increase_total: Mapped[Decimal] = mapped_column(Money(), default=Decimal("0.00"))
    cost_decrease_total: Mapped[Decimal] = mapped_column(Money(), default=Decimal("0.00"))
    certification_required: Mapped[bool | None] = mapped_column()  # None = not yet tested
    certification_status: Mapped[CertificationStatus] = mapped_column(
        db_enum(CertificationStatus, "rea_cda_certification_status"),
        default=CertificationStatus.PENDING,
    )
    submitted_date: Mapped[datetime.date | None] = mapped_column(sa.Date)
    # CDA claims: the CO-receipt anchor BOTH derived dates compute from.
    co_received_date: Mapped[datetime.date | None] = mapped_column(sa.Date)
    co_response_deadline: Mapped[datetime.date | None] = mapped_column(sa.Date)  # derived
    interest_accrual_start_date: Mapped[datetime.date | None] = mapped_column(sa.Date)  # derived


class REACDALineItem(Base):
    __tablename__ = "rea_cda_line_items"

    line_item_id: Mapped[int] = mapped_column(primary_key=True)
    action_id: Mapped[int] = mapped_column(
        sa.ForeignKey("rea_cda_actions.action_id"), nullable=False
    )
    gl_transaction_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("gl_transactions.transaction_id")
    )
    description: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    # Positive = cost increase, negative = cost decrease.
    amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)


class EichleayClaim(Base):
    __tablename__ = "eichleay_claims"

    claim_id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(sa.ForeignKey("contracts.contract_id"), nullable=False)
    delay_start_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    delay_end_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    delay_days: Mapped[int] = mapped_column(nullable=False)
    # Entitlement prerequisites — nullable booleans: None = undocumented,
    # which forces status=incomplete (never silently assumed).
    government_caused_delay: Mapped[bool | None] = mapped_column()
    contractor_on_standby: Mapped[bool | None] = mapped_column()
    no_replacement_work: Mapped[bool | None] = mapped_column()
    # INPUTS stored for reproducibility (handoff §6): the claim recomputes
    # from this row alone.
    contract_billings_amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    total_company_billings_amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    total_home_office_overhead: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    actual_performance_days: Mapped[int] = mapped_column(nullable=False)
    # Computed outputs (three steps, §10).
    allocable_overhead: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    daily_overhead_rate: Mapped[Decimal] = mapped_column(SafeNumeric(18, 4), nullable=False)
    unabsorbed_overhead_claim: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    status: Mapped[ClaimStatus] = mapped_column(
        db_enum(ClaimStatus, "eichleay_claim_status"), nullable=False
    )
