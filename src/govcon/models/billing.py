"""Phase 5 tables per architecture spec §0.1: vouchers (SF 1408 criterion F
billing-to-ledger tie-out), payroll_registers (Schedule L source), and
ice_schedules (generated schedule records with Schedule N signer fields)."""

from __future__ import annotations

import datetime
import enum
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from govcon.db.base import Base
from govcon.db.types import Money
from govcon.models.enums import ReconciliationStatus, db_enum


class VoucherStatus(str, enum.Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    PAID = "paid"


class ScheduleType(str, enum.Enum):
    A = "A"
    B = "B"
    C = "C"
    E = "E"
    G = "G"
    H = "H"
    I = "I"  # noqa: E741 - the schedule really is named "I"
    L = "L"
    N = "N"
    O = "O"  # noqa: E741


class SignerRole(str, enum.Enum):
    CFO = "cfo"
    VP = "vp"
    OTHER = "other"


class Voucher(Base):
    __tablename__ = "vouchers"

    voucher_id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(sa.ForeignKey("contracts.contract_id"), nullable=False)
    period_id: Mapped[int] = mapped_column(sa.ForeignKey("periods.period_id"), nullable=False)
    amount_billed: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    billing_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    status: Mapped[VoucherStatus] = mapped_column(
        db_enum(VoucherStatus, "voucher_status"), default=VoucherStatus.DRAFT
    )
    # voucher_line_items (which ledger rows a voucher draws from) is
    # deliberately deferred per §0.1 until the reconciliation logic needs it.


class PayrollRegister(Base):
    __tablename__ = "payroll_registers"

    register_id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(sa.ForeignKey("periods.period_id"), nullable=False)
    # Conceptually the IRS Form 941 total for the period (synthetic fixture
    # data only) — Schedule L reconciles this against distributed labor.
    total_gross_payroll: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    source_document: Mapped[str | None] = mapped_column(sa.String(200))


class ICESchedule(Base):
    __tablename__ = "ice_schedules"

    schedule_id: Mapped[int] = mapped_column(primary_key=True)
    # Generation precondition: EVERY periods row for this fiscal_year has
    # status = closed (§11 item 3) — an annual schedule gates on all periods.
    fiscal_year: Mapped[int] = mapped_column(nullable=False)
    schedule_type: Mapped[ScheduleType] = mapped_column(
        db_enum(ScheduleType, "schedule_type"), nullable=False
    )
    generated_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    reconciliation_status: Mapped[ReconciliationStatus] = mapped_column(
        db_enum(ReconciliationStatus, "reconciliation_status"), nullable=False
    )
    # Structured schedule content (canonical JSON) — report *formats* are a
    # Phase 10 exporter concern; the data is the deliverable here.
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    signer_name: Mapped[str | None] = mapped_column(sa.String(120))  # Schedule N only
    signer_role: Mapped[SignerRole | None] = mapped_column(
        db_enum(SignerRole, "signer_role")
    )  # Schedule N must be signed no lower than VP/CFO level (reg-ref §5)
    signed_date: Mapped[datetime.date | None] = mapped_column(sa.Date)
