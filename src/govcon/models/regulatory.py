"""regulatory_thresholds per architecture spec §0.1.

Seeded strictly from 02_Regulatory_Reference_Verified.md by migration 0002
(rows frozen in the migration; mirrored in govcon.seeds with a drift test).
Every numeric threshold in application logic traces to a row here — never a
hard-coded scalar (CLAUDE.md ground rule 2).
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from govcon.db.base import Base
from govcon.db.types import Money
from govcon.models.enums import ThresholdStatus, db_enum


class RegulatoryThreshold(Base):
    __tablename__ = "regulatory_thresholds"

    threshold_id: Mapped[int] = mapped_column(primary_key=True)
    rule_name: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    # Nullable for status-only rows (e.g. CAS_407_STATUS).
    value: Mapped[Decimal | None] = mapped_column(Money())
    effective_date: Mapped[datetime.date | None] = mapped_column(sa.Date)
    superseded_date: Mapped[datetime.date | None] = mapped_column(sa.Date)
    status: Mapped[ThresholdStatus] = mapped_column(
        db_enum(ThresholdStatus, "threshold_status"), nullable=False
    )
    source_citation: Mapped[str] = mapped_column(sa.Text, nullable=False)
