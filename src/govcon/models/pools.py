"""indirect_pools per architecture spec §0.1.

"Currently-approved provisional rate" (§12) = the row with
rate_type=provisional and status=approved for a pool/fiscal year — a
queryable field, not a phrase. Rate revisions are new rows (superseded_by),
never edits. allocation_base_amount must be non-null and > 0 before any
rate calculation runs (checked at calculation time, Phase 4).
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from govcon.db.base import Base
from govcon.db.types import Money, Rate
from govcon.models.enums import PoolName, PoolStatus, RateType, db_enum


class IndirectPool(Base):
    __tablename__ = "indirect_pools"

    pool_id: Mapped[int] = mapped_column(primary_key=True)
    pool_name: Mapped[PoolName] = mapped_column(db_enum(PoolName, "pool_name"), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(nullable=False)
    rate_type: Mapped[RateType] = mapped_column(db_enum(RateType, "rate_type"), nullable=False)
    status: Mapped[PoolStatus] = mapped_column(
        db_enum(PoolStatus, "pool_status"), default=PoolStatus.PENDING
    )
    pool_balance: Mapped[Decimal | None] = mapped_column(Money())  # computed, Phase 4
    allocation_base_amount: Mapped[Decimal | None] = mapped_column(Money())
    calculated_rate: Mapped[Decimal | None] = mapped_column(Rate())  # computed, Phase 4
    calculated_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime)
    superseded_by: Mapped[int | None] = mapped_column(sa.ForeignKey("indirect_pools.pool_id"))
    # Canonical record of which FPRA authorizes a forward-pricing rate (§12).
    fpra_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("forward_pricing_rate_agreements.fpra_id")
    )
