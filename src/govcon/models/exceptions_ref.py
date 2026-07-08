"""Phase 2 reference tables per architecture spec §0.1:
contract_clause_exceptions (the fifth allowability test — award-specific
restrictive clauses overriding default allowability) and gsa_per_diem_rates
(the §4a travel-split reference table)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from govcon.db.base import Base
from govcon.db.types import Money


class ContractClauseException(Base):
    __tablename__ = "contract_clause_exceptions"

    exception_id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(
        sa.ForeignKey("contracts.contract_id"), nullable=False
    )
    # Which default allowability rule this clause overrides (FAR citation text).
    far_citation_overridden: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    override_reason: Mapped[str] = mapped_column(sa.Text, nullable=False)
    effective_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)


class GSAPerDiemRate(Base):
    __tablename__ = "gsa_per_diem_rates"

    rate_id: Mapped[int] = mapped_column(primary_key=True)
    location: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    lodging_rate: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    meals_incidentals_rate: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    effective_start_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    effective_end_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    # First-class reference table per §4a — populated manually (or a future
    # feed), never a hard-coded flat number. Synthetic fixture data only.
