"""Modified-CAS consistency tracking per architecture spec §7a / §0.1:
cost_accounting_practices (disclosed practices, versioned) and
practice_change_events (a practice CHANGE is a new row + a flagged event
with cost_impact_required defaulting true per 9903.201-6 — never an edit).
"""

from __future__ import annotations

import datetime
import enum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from govcon.db.base import Base
from govcon.models.enums import db_enum


class DisclosedTreatment(str, enum.Enum):
    DIRECT = "direct"
    INDIRECT = "indirect"


class ChangeEventStatus(str, enum.Enum):
    FLAGGED = "flagged"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"


class CostAccountingPractice(Base):
    __tablename__ = "cost_accounting_practices"

    practice_id: Mapped[int] = mapped_column(primary_key=True)
    practice_area: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    disclosed_treatment: Mapped[DisclosedTreatment] = mapped_column(
        db_enum(DisclosedTreatment, "disclosed_treatment"), nullable=False
    )
    # The v1 linkage: a practice governs the family of gl_accounts whose
    # account_code starts with this prefix.
    account_code_prefix: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, nullable=False)
    effective_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    superseded_by: Mapped[int | None] = mapped_column(
        sa.ForeignKey("cost_accounting_practices.practice_id")
    )


class PracticeChangeEvent(Base):
    __tablename__ = "practice_change_events"

    event_id: Mapped[int] = mapped_column(primary_key=True)
    practice_id: Mapped[int] = mapped_column(
        sa.ForeignKey("cost_accounting_practices.practice_id"), nullable=False
    )
    new_practice_id: Mapped[int] = mapped_column(
        sa.ForeignKey("cost_accounting_practices.practice_id"), nullable=False
    )
    detected_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    # Any change to a disclosed practice can trigger a cost-impact analysis
    # under 9903.201-6 — flag first; resolving requires a recorded reason.
    cost_impact_required: Mapped[bool] = mapped_column(default=True, nullable=False)
    status: Mapped[ChangeEventStatus] = mapped_column(
        db_enum(ChangeEventStatus, "change_event_status"),
        default=ChangeEventStatus.FLAGGED,
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(sa.Text)
