"""FK-anchor reference tables per architecture spec §0.1: persons,
unallowable_cost_categories, forward_pricing_rate_agreements.

Created in Phase 1 as FK targets only (SQLite cannot add FKs to existing
tables without a rebuild); their business logic lands in Phases 2/11.
"""

from __future__ import annotations

import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from govcon.db.base import Base
from govcon.models.enums import DetectionMethod, FPRAStatus, db_enum


class Person(Base):
    __tablename__ = "persons"

    person_id: Mapped[int] = mapped_column(primary_key=True)
    person_name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    role: Mapped[str | None] = mapped_column(sa.String(80))  # free text for v1
    # Drives the §4a executive-compensation cap tracker (Phase 2).
    is_executive: Mapped[bool] = mapped_column(default=False)


class UnallowableCostCategory(Base):
    __tablename__ = "unallowable_cost_categories"

    category_id: Mapped[int] = mapped_column(primary_key=True)
    far_citation: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    category_name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    trap_logic_description: Mapped[str | None] = mapped_column(sa.Text)
    detection_method: Mapped[DetectionMethod] = mapped_column(
        db_enum(DetectionMethod, "detection_method")
    )


class ForwardPricingRateAgreement(Base):
    __tablename__ = "forward_pricing_rate_agreements"

    fpra_id: Mapped[int] = mapped_column(primary_key=True)
    fiscal_year_start: Mapped[int] = mapped_column(nullable=False)
    fiscal_year_end: Mapped[int] = mapped_column(nullable=False)
    negotiated_date: Mapped[datetime.date | None] = mapped_column(sa.Date)
    aco_name: Mapped[str | None] = mapped_column(sa.String(120))
    # Descriptive only — indirect_pools.fpra_id is the canonical link (§12).
    rates_covered: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[FPRAStatus] = mapped_column(
        db_enum(FPRAStatus, "fpra_status"), default=FPRAStatus.DRAFT
    )
    expiration_date: Mapped[datetime.date | None] = mapped_column(sa.Date)
