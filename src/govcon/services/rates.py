"""Indirect rate engine + provisional-to-final true-up (spec §5, §0.1).

Every calculation here:
- uses decimal.Decimal end to end (ground rule 4a), quantizing rates to 4dp
  and money to the cent at the END of a computation, never mid-stream;
- validates its preconditions loudly (criterion C: a missing/zero base is an
  error, not a skip);
- stamps one rate_calculation_runs row with the exact inputs used, so any
  historical number can be reproduced from that row alone (§5's audit-
  defense rule) — reconstruct_run() IS that reproduction, used by tests.

Pool cost numerators come from the ledger via the criterion-D exclusion
filter (unallowable never in a numerator). Allocation BASES are provided on
the pool row for v1 (allocation_base_amount is an entered field in §0.1,
unlike pool_balance which is computed) — the §5 base *definitions* say what
to enter; deriving every base from the ledger needs labor identification on
indirect accounts, which the schema doesn't carry yet (flagged in the
session note, not silently approximated).
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.core.decimal_config import quantize_money, quantize_rate
from govcon.core.errors import RateCalculationError
from govcon.models import IndirectPool, JCLEntry, RateCalculationRun, RateTrueUp
from govcon.models.enums import CostElement, PoolStatus, RateType
from govcon.models.rate_runs import RunType


def _require_base(pool: IndirectPool) -> Decimal:
    if pool.allocation_base_amount is None or pool.allocation_base_amount <= 0:
        raise RateCalculationError(
            f"pool {pool.pool_id} ({pool.pool_name.value} FY{pool.fiscal_year}) has "
            "no positive allocation_base_amount — SF 1408 criterion C: define the "
            "base before any rate can be calculated"
        )
    return pool.allocation_base_amount


def compute_direct_labor_base(session: Session, fiscal_year: int) -> Decimal:
    """§5 'Direct Labor Base': direct labor only, from jcl_entries
    (cost_element = labor) for the fiscal year — traceable to schema fields."""
    from govcon.models import Period

    amounts = session.execute(
        sa.select(JCLEntry.amount)
        .join(Period, JCLEntry.period_id == Period.period_id)
        .where(Period.fiscal_year == fiscal_year)
        .where(JCLEntry.cost_element == CostElement.LABOR)
    ).scalars()
    return sum((Decimal(a) for a in amounts), Decimal("0.00"))


def compute_total_company_labor_base(session: Session, fiscal_year: int) -> Decimal:
    """§5 'Total Company Labor Base' (the Fringe denominator): ALL labor —
    direct, overhead, and G&A — identified by gl_accounts.is_labor (the
    v1.1 flag; nothing else can find labor among indirect accounts)."""
    from govcon.models import GLAccount, GLTransaction, Period

    amounts = session.execute(
        sa.select(GLTransaction.amount)
        .join(GLAccount, GLTransaction.account_id == GLAccount.account_id)
        .join(Period, GLTransaction.period_id == Period.period_id)
        .where(GLAccount.is_labor.is_(True))
        .where(Period.fiscal_year == fiscal_year)
    ).scalars()
    return sum((Decimal(a) for a in amounts), Decimal("0.00"))


def _approved_rate(
    session: Session, pool_name, fiscal_year: int, rate_type
) -> Decimal:
    from govcon.models.enums import PoolStatus as _PS

    rate = session.execute(
        sa.select(IndirectPool.calculated_rate)
        .where(IndirectPool.pool_name == pool_name)
        .where(IndirectPool.fiscal_year == fiscal_year)
        .where(IndirectPool.rate_type == rate_type)
        .where(IndirectPool.status.in_([_PS.APPROVED, _PS.LOCKED]))
        .limit(1)
    ).scalar_one_or_none()
    if rate is None:
        raise RateCalculationError(
            f"no approved {pool_name.value} {rate_type.value} rate for "
            f"FY{fiscal_year} — derive/approve upstream rates first (§5 chains "
            "fringe → overhead → G&A); do not substitute a guess"
        )
    return Decimal(rate)


def derive_pool_base(session: Session, pool: IndirectPool) -> Decimal:
    """Derive and SET allocation_base_amount from the ledger per the §5
    base definitions (v1.1 option — entered bases remain valid):

    fringe   → Total Company Labor Base (all is_labor accounts)
    overhead → Direct Labor Base + allocated fringe (fringe rate × DL base)
    ga       → Total Cost Input: DL + direct materials/ODCs (all other JCL
               elements) + allocated fringe + allocated overhead
    """
    from govcon.models.enums import PoolName

    direct_labor = compute_direct_labor_base(session, pool.fiscal_year)
    if pool.pool_name == PoolName.FRINGE:
        base = compute_total_company_labor_base(session, pool.fiscal_year)
    elif pool.pool_name == PoolName.OVERHEAD:
        fringe_rate = _approved_rate(session, PoolName.FRINGE, pool.fiscal_year, pool.rate_type)
        base = direct_labor + direct_labor * fringe_rate
    else:  # GA — Total Cost Input (§5)
        from govcon.models import Period

        other_direct = sum(
            (
                Decimal(a)
                for a in session.execute(
                    sa.select(JCLEntry.amount)
                    .join(Period, JCLEntry.period_id == Period.period_id)
                    .where(Period.fiscal_year == pool.fiscal_year)
                    .where(JCLEntry.cost_element != CostElement.LABOR)
                ).scalars()
            ),
            Decimal("0.00"),
        )
        fringe_rate = _approved_rate(session, PoolName.FRINGE, pool.fiscal_year, pool.rate_type)
        oh_rate = _approved_rate(session, PoolName.OVERHEAD, pool.fiscal_year, pool.rate_type)
        allocated_fringe = direct_labor * fringe_rate
        allocated_oh = (direct_labor + allocated_fringe) * oh_rate
        base = direct_labor + other_direct + allocated_fringe + allocated_oh

    pool.allocation_base_amount = quantize_money(base)
    session.flush()
    return pool.allocation_base_amount


def calculate_pool_rate(session: Session, pool: IndirectPool) -> RateCalculationRun:
    """Compute a pool's rate: numerator from the ledger (criterion-D
    filtered), denominator = the pool's defined base. Stamps the run."""
    from govcon.services.exclusions import pool_numerator_total

    if pool.status == PoolStatus.LOCKED:
        raise RateCalculationError(
            f"pool {pool.pool_id} ({pool.pool_name.value} FY{pool.fiscal_year}) is "
            "LOCKED by period close — no retroactive rate recalculation (§11 item 4); "
            "a post-close correction is a new period-adjustment entry, never an edit"
        )
    base = _require_base(pool)
    balance = pool_numerator_total(session, pool)
    rate = quantize_rate(balance / base)

    pool.pool_balance = balance
    pool.calculated_rate = rate
    pool.calculated_at = datetime.datetime.now(datetime.timezone.utc)
    run = RateCalculationRun(
        run_type=RunType.POOL_RATE,
        pool_id=pool.pool_id,
        calculated_at=pool.calculated_at,
        inputs_snapshot=json.dumps(
            {
                "pool_id": pool.pool_id,
                "pool_name": pool.pool_name.value,
                "fiscal_year": pool.fiscal_year,
                "rate_type": pool.rate_type.value,
                "pool_balance": format(balance, "f"),
                "allocation_base_amount": format(base, "f"),
            }
        ),
        result_value=rate,
    )
    session.add(run)
    session.flush()
    return run


def approve_rate(session: Session, pool: IndirectPool) -> IndirectPool:
    """Approve a calculated rate; any prior approved row for the same
    pool/fiscal-year/rate-type is superseded (a rate revision is a new row,
    never an edit — §0.1)."""
    if pool.calculated_rate is None:
        raise RateCalculationError(
            f"pool {pool.pool_id} has no calculated_rate to approve — run "
            "calculate_pool_rate first"
        )
    prior = session.execute(
        sa.select(IndirectPool)
        .where(IndirectPool.pool_name == pool.pool_name)
        .where(IndirectPool.fiscal_year == pool.fiscal_year)
        .where(IndirectPool.rate_type == pool.rate_type)
        .where(IndirectPool.status == PoolStatus.APPROVED)
        .where(IndirectPool.pool_id != pool.pool_id)
    ).scalars()
    for row in prior:
        row.status = PoolStatus.SUPERSEDED
        row.superseded_by = pool.pool_id
    pool.status = PoolStatus.APPROVED
    session.flush()
    return pool


def burdened_cost(
    session: Session,
    direct_labor: Decimal,
    *,
    fringe: IndirectPool,
    overhead: IndirectPool,
    ga: IndirectPool,
) -> tuple[Decimal, RateCalculationRun]:
    """Fully Burdened Cost = DL × (1+Fringe) × (1+OH) × (1+G&A), §5 —
    quantized to the cent at the END; stamped with the three specific pool
    row ids + rate values so the number is reconstructable 18 months later."""
    for pool in (fringe, overhead, ga):
        if pool.calculated_rate is None:
            raise RateCalculationError(
                f"pool {pool.pool_id} ({pool.pool_name.value}) has no calculated "
                "rate — a burdened cost cannot use an uncalculated pool"
            )
    result = quantize_money(
        Decimal(direct_labor)
        * (1 + fringe.calculated_rate)
        * (1 + overhead.calculated_rate)
        * (1 + ga.calculated_rate)
    )
    run = RateCalculationRun(
        run_type=RunType.BURDENED_COST,
        calculated_at=datetime.datetime.now(datetime.timezone.utc),
        inputs_snapshot=json.dumps(
            {
                "direct_labor": format(Decimal(direct_labor), "f"),
                "rates": {
                    name: {
                        "pool_id": pool.pool_id,
                        "rate_type": pool.rate_type.value,
                        "fiscal_year": pool.fiscal_year,
                        "calculated_rate": format(pool.calculated_rate, "f"),
                    }
                    for name, pool in (("fringe", fringe), ("overhead", overhead), ("ga", ga))
                },
            }
        ),
        result_value=result,
    )
    session.add(run)
    session.flush()
    return result, run


def reconstruct_run(session: Session, run: RateCalculationRun) -> Decimal:
    """Recompute a stamped calculation FROM ITS SNAPSHOT ALONE — the §5
    audit-defense reproduction. Never reads current pool rows, so it still
    reproduces the historical number after rates move on."""
    snapshot = json.loads(run.inputs_snapshot)
    if run.run_type == RunType.POOL_RATE:
        return quantize_rate(
            Decimal(snapshot["pool_balance"]) / Decimal(snapshot["allocation_base_amount"])
        )
    if run.run_type == RunType.BURDENED_COST:
        result = Decimal(snapshot["direct_labor"])
        for name in ("fringe", "overhead", "ga"):
            result *= 1 + Decimal(snapshot["rates"][name]["calculated_rate"])
        return quantize_money(result)
    raise RateCalculationError(f"unknown run_type {run.run_type!r}")


def true_up(
    session: Session,
    *,
    provisional: IndirectPool,
    final: IndirectPool,
    billed_base_amount: Decimal,
) -> RateTrueUp:
    """Provisional-to-final true-up (§5 rate lifecycle): the delta and its
    billing impact — the actual business value, not just two snapshots.

    billed_base_amount = the base dollars the provisional rate was applied
    to on interim billings. delta/billing impact convention:
    positive = under-billed at provisional (additional amount due);
    negative = over-billed (credit owed).
    """
    if provisional.rate_type != RateType.PROVISIONAL or final.rate_type != RateType.ACTUAL_FINAL:
        raise RateCalculationError(
            "true_up requires a provisional-rate row and an actual_final-rate row, "
            f"got {provisional.rate_type.value!r} and {final.rate_type.value!r}"
        )
    if provisional.calculated_rate is None or final.calculated_rate is None:
        raise RateCalculationError("both pool rows must carry calculated rates")
    if provisional.pool_name != final.pool_name or provisional.fiscal_year != final.fiscal_year:
        raise RateCalculationError(
            "true_up compares the same pool_name/fiscal_year across rate types"
        )
    delta = quantize_money(
        Decimal(billed_base_amount) * (final.calculated_rate - provisional.calculated_rate)
    )
    row = RateTrueUp(
        pool_id=provisional.pool_id,
        fiscal_year=provisional.fiscal_year,
        provisional_rate_snapshot=provisional.calculated_rate,
        final_rate_snapshot=final.calculated_rate,
        delta_amount=delta,
        billing_impact_amount=delta,  # v1: the signed billing adjustment IS the delta
        calculated_date=datetime.date.today(),
    )
    session.add(row)
    session.flush()
    return row
