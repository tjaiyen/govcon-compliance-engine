"""contracts + contract_actions per architecture spec §0.1.

Frozen after insert on contracts (enforced by ORM guard + DB trigger):
award_date, tina_threshold_snapshot, tina_threshold_id,
cas_trigger_threshold_snapshot, cas_trigger_threshold_id. Any change is a
new contract *version* row via services.versioning.supersede_contract().
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from govcon.db.base import Base
from govcon.db.types import Money
from govcon.models.enums import (
    AgencyType,
    CASCoverageType,
    ContractActionType,
    ContractorSize,
    db_enum,
)

#: Columns immutable after insert (ORM guard + trg_contracts_immutable_cols).
CONTRACT_FROZEN_COLUMNS = (
    "award_date",
    "tina_threshold_snapshot",
    "tina_threshold_id",
    "cas_trigger_threshold_snapshot",
    "cas_trigger_threshold_id",
)


class Contract(Base):
    __tablename__ = "contracts"

    contract_id: Mapped[int] = mapped_column(primary_key=True)
    agency_type: Mapped[AgencyType] = mapped_column(db_enum(AgencyType, "agency_type"))
    award_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    performance_start_date: Mapped[datetime.date | None] = mapped_column(sa.Date)
    performance_end_date: Mapped[datetime.date | None] = mapped_column(sa.Date)
    contract_value: Mapped[Decimal] = mapped_column(Money())
    tina_threshold_snapshot: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    tina_threshold_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("regulatory_thresholds.threshold_id")
    )
    cas_trigger_threshold_snapshot: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    cas_trigger_threshold_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("regulatory_thresholds.threshold_id")
    )
    cas_coverage_type: Mapped[CASCoverageType] = mapped_column(
        db_enum(CASCoverageType, "cas_coverage_type")
    )
    disclosure_required: Mapped[bool] = mapped_column(default=False)
    contractor_size: Mapped[ContractorSize] = mapped_column(
        db_enum(ContractorSize, "contractor_size")
    )
    is_nontraditional_dc: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    # Old row points at the newer version row (opposite direction from
    # gl_transactions.superseded_by — see services/corrections.py docstring).
    superseded_by: Mapped[int | None] = mapped_column(
        sa.ForeignKey("contracts.contract_id")
    )


class ContractAction(Base):
    """A negotiated action under a contract vehicle — task order, mod, etc.

    TINA applicability evaluates per action, on the action's own date and
    value, never inherited from the parent vehicle (spec §8). The four
    statutory exceptions are four explicit nullable booleans (null = not
    yet evaluated), never a single "exempt" flag.
    """

    __tablename__ = "contract_actions"

    action_id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(
        sa.ForeignKey("contracts.contract_id"), nullable=False
    )
    action_type: Mapped[ContractActionType] = mapped_column(
        db_enum(ContractActionType, "contract_action_type")
    )
    description: Mapped[str | None] = mapped_column(sa.Text)
    action_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    proposed_value: Mapped[Decimal | None] = mapped_column(Money())
    tina_exception_adequate_price_competition: Mapped[bool | None] = mapped_column()
    tina_exception_commercial_product_service: Mapped[bool | None] = mapped_column()
    tina_exception_prices_set_by_law: Mapped[bool | None] = mapped_column()
    tina_exception_waiver_granted: Mapped[bool | None] = mapped_column()
