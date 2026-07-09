"""Eichleay unabsorbed-overhead calculator (spec §10) — the exclusive
federal method once entitlement is established (Wickham, Fed. Cir. 1994).

Three steps:
  1. allocable  = (contract billings / total company billings) × home-office OH
  2. daily rate = allocable / actual days of contract performance
  3. unabsorbed = daily rate × delay days

Discipline this module enforces (spec §10, all three):
- INPUTS RECONCILED FIRST: refuses to run while any voucher feeding the
  billings sits in an OPEN period (an open period hasn't passed the
  Schedule G three-way reconciliation — the figure would inherit whatever
  error exists upstream).
- ENTITLEMENT AS EXPLICIT PRE-CHECKS: three boolean prerequisites; any
  undocumented (None) or failed one completes the calculation but flags
  status=incomplete with warnings — never blocks entirely, never presents
  a number as if entitlement were settled.
- REPRODUCIBLE FROM ITS OWN ROW: all four inputs are stored; verify_claim()
  recomputes the three steps from the stored row alone.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.core.decimal_config import ROUNDING, quantize_money
from govcon.core.errors import GovconError
from govcon.models import Contract, EichleayClaim, Period, Voucher
from govcon.models.claims import ClaimStatus
from govcon.models.enums import PeriodStatus

DAILY_QUANTUM = Decimal("0.0001")


class EichleayError(GovconError):
    pass


def _billings(session: Session, contract_id: int | None = None) -> Decimal:
    stmt = sa.select(Voucher.amount_billed)
    if contract_id is not None:
        stmt = stmt.where(Voucher.contract_id == contract_id)
    return sum(
        (Decimal(a) for a in session.execute(stmt).scalars()), Decimal("0.00")
    )


def _check_inputs_reconciled(session: Session) -> None:
    unreconciled = session.execute(
        sa.select(sa.func.count())
        .select_from(Voucher)
        .join(Period, Voucher.period_id == Period.period_id)
        .where(Period.status != PeriodStatus.CLOSED)
    ).scalar_one()
    if unreconciled:
        raise EichleayError(
            f"{unreconciled} voucher(s) sit in OPEN periods — billings are not "
            "reconciled (Schedule G discipline); close the periods first. "
            "'Inputs reconciled' is a precondition the calculator refuses to "
            "skip (spec §10)."
        )


def calculate_eichleay(
    session: Session,
    contract: Contract,
    *,
    delay_start: datetime.date,
    delay_end: datetime.date,
    total_home_office_overhead: Decimal,
    government_caused_delay: bool | None,
    contractor_on_standby: bool | None,
    no_replacement_work: bool | None,
) -> tuple[EichleayClaim, list[str]]:
    """Run the three-step calculation. Returns (claim, warnings).

    delay_days and actual_performance_days are both inclusive day counts
    ((end - start).days + 1) — one convention, stated once.
    """
    if contract.performance_start_date is None or contract.performance_end_date is None:
        raise EichleayError(
            "contract has no performance_start_date/performance_end_date — "
            "'actual days of contract performance' (Step 2) cannot be computed; "
            "set the dates, do not estimate"
        )
    _check_inputs_reconciled(session)

    contract_billings = _billings(session, contract.contract_id)
    total_billings = _billings(session)
    if total_billings <= 0:
        raise EichleayError("total company billings are zero — nothing to allocate")

    performance_days = (
        contract.performance_end_date - contract.performance_start_date
    ).days + 1
    delay_days = (delay_end - delay_start).days + 1
    if delay_days <= 0:
        raise EichleayError("delay_end_date precedes delay_start_date")

    allocable = quantize_money(
        contract_billings / total_billings * Decimal(total_home_office_overhead)
    )
    daily = (allocable / performance_days).quantize(DAILY_QUANTUM, rounding=ROUNDING)
    unabsorbed = quantize_money(daily * delay_days)

    warnings: list[str] = []
    prerequisites = {
        "government_caused_delay": government_caused_delay,
        "contractor_on_standby": contractor_on_standby,
        "no_replacement_work": no_replacement_work,
    }
    for name, value in prerequisites.items():
        if value is None:
            warnings.append(f"entitlement prerequisite {name} is UNDOCUMENTED")
        elif value is False:
            warnings.append(f"entitlement prerequisite {name} is NOT MET")
    if government_caused_delay is False:
        warnings.append(
            "delay not government-caused: a delay arising solely from change "
            "orders may NOT support unabsorbed-overhead recovery (Community "
            "Heating & Plumbing v. Kelso) — this number does not imply entitlement"
        )

    claim = EichleayClaim(
        contract_id=contract.contract_id,
        delay_start_date=delay_start,
        delay_end_date=delay_end,
        delay_days=delay_days,
        government_caused_delay=government_caused_delay,
        contractor_on_standby=contractor_on_standby,
        no_replacement_work=no_replacement_work,
        contract_billings_amount=contract_billings,
        total_company_billings_amount=total_billings,
        total_home_office_overhead=Decimal(total_home_office_overhead),
        actual_performance_days=performance_days,
        allocable_overhead=allocable,
        daily_overhead_rate=daily,
        unabsorbed_overhead_claim=unabsorbed,
        status=ClaimStatus.INCOMPLETE if warnings else ClaimStatus.COMPLETE,
    )
    session.add(claim)
    session.flush()
    return claim, warnings


def verify_claim(claim: EichleayClaim) -> bool:
    """Recompute the three steps FROM THE STORED ROW ALONE (handoff §6
    reproducibility) — True when the stored outputs recompute exactly."""
    allocable = quantize_money(
        Decimal(claim.contract_billings_amount)
        / Decimal(claim.total_company_billings_amount)
        * Decimal(claim.total_home_office_overhead)
    )
    daily = (allocable / claim.actual_performance_days).quantize(
        DAILY_QUANTUM, rounding=ROUNDING
    )
    unabsorbed = quantize_money(daily * claim.delay_days)
    return (
        allocable == Decimal(claim.allocable_overhead)
        and daily == Decimal(claim.daily_overhead_rate)
        and unabsorbed == Decimal(claim.unabsorbed_overhead_claim)
    )
