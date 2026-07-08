"""Central ORM-layer enforcement (before_flush) — the friendly, typed-error
layer. Every rule here has a DB-level backstop (trigger/CHECK) created in
migration 0001, so bypassing the ORM still cannot break the rules.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm import Session

from govcon.core.errors import (
    AppendOnlyViolation,
    ClosedPeriodError,
    DirectCostWithoutContractError,
    ImmutableFieldError,
)
from govcon.models import (
    CONTRACT_FROZEN_COLUMNS,
    AuditTrail,
    Contract,
    GLAccount,
    GLTransaction,
    JCLEntry,
    Period,
)
from govcon.models.enums import CostType, PeriodStatus

APPEND_ONLY_TYPES = (GLTransaction, AuditTrail)


def _check_append_only(session: Session) -> None:
    for obj in session.dirty:
        if isinstance(obj, APPEND_ONLY_TYPES) and session.is_modified(obj):
            raise AppendOnlyViolation(
                f"{obj.__class__.__name__} is append-only; corrections are "
                "reversing entries via superseded_by "
                "(services.corrections.post_correction)"
            )
    for obj in session.deleted:
        if isinstance(obj, APPEND_ONLY_TYPES):
            raise AppendOnlyViolation(
                f"{obj.__class__.__name__} rows can never be deleted"
            )


def _check_contract_immutability(session: Session) -> None:
    for obj in session.dirty:
        if not isinstance(obj, Contract):
            continue
        state = sa.inspect(obj)
        for col in CONTRACT_FROZEN_COLUMNS:
            if state.attrs[col].history.has_changes():
                raise ImmutableFieldError(
                    f"contracts.{col} is immutable after insert; create a new "
                    "contract version (services.versioning.supersede_contract)"
                )


def _check_open_period(session: Session) -> None:
    for obj in session.new:
        if isinstance(obj, (GLTransaction, JCLEntry)) and obj.period_id is not None:
            period = session.get(Period, obj.period_id)
            if period is not None and period.status != PeriodStatus.OPEN:
                raise ClosedPeriodError(
                    f"cannot post to closed period {period.fiscal_year}-"
                    f"{period.period_number:02d} (architecture spec §11)"
                )


def _check_direct_needs_contract(session: Session) -> None:
    for obj in session.new:
        if isinstance(obj, GLTransaction) and obj.contract_id is None:
            account = session.get(GLAccount, obj.account_id)
            if account is not None and account.cost_type == CostType.DIRECT:
                raise DirectCostWithoutContractError(
                    "a transaction on a direct-cost account must reference a "
                    "contract (SF 1408 criterion B)"
                )


@event.listens_for(Session, "before_flush")
def enforce_write_rules(session: Session, _ctx, _instances) -> None:
    _check_append_only(session)
    _check_contract_immutability(session)
    _check_open_period(session)
    _check_direct_needs_contract(session)
