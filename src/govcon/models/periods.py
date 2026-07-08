"""periods per architecture spec §0.1 / §11.

A period cannot close while reconciliation has not passed (full close
workflow is Phase 5); no transaction can post to a closed period (enforced
now, Phase 1: ORM guard + DB triggers on gl_transactions and jcl_entries).
"""

from __future__ import annotations

import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from govcon.db.base import Base
from govcon.models.enums import PeriodStatus, ReconciliationStatus, db_enum


class Period(Base):
    __tablename__ = "periods"
    __table_args__ = (
        sa.UniqueConstraint("fiscal_year", "period_number", name="fiscal_year_period"),
    )

    period_id: Mapped[int] = mapped_column(primary_key=True)
    fiscal_year: Mapped[int] = mapped_column(nullable=False)
    period_number: Mapped[int] = mapped_column(nullable=False)  # fiscal month 1-12
    start_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    end_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    status: Mapped[PeriodStatus] = mapped_column(
        db_enum(PeriodStatus, "period_status"), default=PeriodStatus.OPEN
    )
    reconciliation_status: Mapped[ReconciliationStatus] = mapped_column(
        db_enum(ReconciliationStatus, "reconciliation_status"),
        default=ReconciliationStatus.PENDING,
    )
    closed_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime)
    closed_by: Mapped[str | None] = mapped_column(sa.String(80))
