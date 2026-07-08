"""Phase 4: pool rates from the ledger, burdened cost with stamping,
reconstruction from the stamp alone, and the provisional-to-final true-up
against known worked examples."""

import datetime
from decimal import Decimal

import pytest

from govcon.core.errors import RateCalculationError
from govcon.models import IndirectPool
from govcon.models.enums import PoolName, PoolStatus, RateType
from govcon.services.rates import (
    approve_rate,
    burdened_cost,
    calculate_pool_rate,
    compute_direct_labor_base,
    reconstruct_run,
    true_up,
)
from tests.fixtures.synthetic_data import seed_all

D = datetime.date


def _pool(name, rate_type, base, fy=2026, status=PoolStatus.PENDING):
    return IndirectPool(
        pool_name=name,
        fiscal_year=fy,
        rate_type=rate_type,
        status=status,
        allocation_base_amount=base,
    )


def test_pool_rate_from_ledger_worked_example(session):
    """Fixture fringe pool: one $400.00 allowable indirect txn, base
    $100,000 → rate 400/100000 = 0.0040. The unallowable fixture txn
    contributes nothing (criterion D numerator exclusion)."""
    data = seed_all(session)
    run = calculate_pool_rate(session, data.pool)
    session.commit()
    assert data.pool.pool_balance == Decimal("400.00")
    assert data.pool.calculated_rate == Decimal("0.0040")
    assert run.result_value == Decimal("0.0040")
    assert data.pool.calculated_at is not None


def test_missing_or_zero_base_fails_loudly(session):
    seed_all(session)
    no_base = _pool(PoolName.OVERHEAD, RateType.PROVISIONAL, None)
    zero_base = _pool(PoolName.GA, RateType.PROVISIONAL, Decimal("0.00"))
    session.add_all([no_base, zero_base])
    session.flush()
    for pool in (no_base, zero_base):
        with pytest.raises(RateCalculationError, match="criterion C"):
            calculate_pool_rate(session, pool)


def test_approve_supersedes_prior_approved_rate(session):
    data = seed_all(session)  # fixture fringe pool is already APPROVED
    revised = _pool(PoolName.FRINGE, RateType.PROVISIONAL, Decimal("100000.00"))
    session.add(revised)
    session.flush()
    calculate_pool_rate(session, revised)
    approve_rate(session, revised)
    session.commit()
    assert revised.status == PoolStatus.APPROVED
    assert data.pool.status == PoolStatus.SUPERSEDED
    assert data.pool.superseded_by == revised.pool_id  # a revision is a new row


def test_burdened_cost_worked_example(session):
    """DL 1000 × 1.10 × 1.20 × 1.05 = 1386.00 (§5)."""
    seed_all(session)
    pools = {}
    for name, rate in ((PoolName.FRINGE, "0.1000"), (PoolName.OVERHEAD, "0.2000"), (PoolName.GA, "0.0500")):
        pool = _pool(name, RateType.PROVISIONAL, Decimal("1.00"), fy=2027)
        pool.calculated_rate = Decimal(rate)
        pools[name] = pool
    session.add_all(pools.values())
    session.flush()
    result, run = burdened_cost(
        session,
        Decimal("1000.00"),
        fringe=pools[PoolName.FRINGE],
        overhead=pools[PoolName.OVERHEAD],
        ga=pools[PoolName.GA],
    )
    session.commit()
    assert result == Decimal("1386.00")
    assert run.result_value == Decimal("1386.00")


def test_burdened_cost_reconstructable_from_stamp_alone(session):
    """§5's audit-defense rule: after the pool rows move on, the stamped
    run still reproduces the historical number."""
    seed_all(session)
    pools = {}
    for name, rate in ((PoolName.FRINGE, "0.1000"), (PoolName.OVERHEAD, "0.2000"), (PoolName.GA, "0.0500")):
        pool = _pool(name, RateType.PROVISIONAL, Decimal("1.00"), fy=2027)
        pool.calculated_rate = Decimal(rate)
        pools[name] = pool
    session.add_all(pools.values())
    session.flush()
    result, run = burdened_cost(
        session, Decimal("1000.00"),
        fringe=pools[PoolName.FRINGE], overhead=pools[PoolName.OVERHEAD], ga=pools[PoolName.GA],
    )
    # Rates move on (new rows would normally supersede; mutate to simulate drift):
    pools[PoolName.FRINGE].calculated_rate = Decimal("0.9999")
    session.commit()
    assert reconstruct_run(session, run) == result == Decimal("1386.00")


def test_pool_rate_run_reconstructs(session):
    data = seed_all(session)
    run = calculate_pool_rate(session, data.pool)
    session.commit()
    assert reconstruct_run(session, run) == Decimal("0.0040")


def test_true_up_worked_examples(session):
    """Prov 10% vs final 12% on a $100,000 billed base → +$2,000 due;
    final 9% → −$1,000 credit."""
    seed_all(session)
    prov = _pool(PoolName.OVERHEAD, RateType.PROVISIONAL, Decimal("1.00"), fy=2027)
    prov.calculated_rate = Decimal("0.1000")
    final_up = _pool(PoolName.OVERHEAD, RateType.ACTUAL_FINAL, Decimal("1.00"), fy=2027)
    final_up.calculated_rate = Decimal("0.1200")
    final_down = _pool(PoolName.OVERHEAD, RateType.ACTUAL_FINAL, Decimal("1.00"), fy=2027)
    final_down.calculated_rate = Decimal("0.0900")
    session.add_all([prov, final_up, final_down])
    session.flush()

    row_up = true_up(session, provisional=prov, final=final_up, billed_base_amount=Decimal("100000.00"))
    row_down = true_up(session, provisional=prov, final=final_down, billed_base_amount=Decimal("100000.00"))
    session.commit()
    assert row_up.delta_amount == Decimal("2000.00")
    assert row_up.billing_impact_amount == Decimal("2000.00")
    assert row_down.delta_amount == Decimal("-1000.00")
    assert row_up.provisional_rate_snapshot == Decimal("0.1000")
    assert row_up.final_rate_snapshot == Decimal("0.1200")


def test_true_up_rejects_mismatched_rows(session):
    seed_all(session)
    prov = _pool(PoolName.OVERHEAD, RateType.PROVISIONAL, Decimal("1.00"), fy=2027)
    prov.calculated_rate = Decimal("0.10")
    wrong_type = _pool(PoolName.OVERHEAD, RateType.PROVISIONAL, Decimal("1.00"), fy=2027)
    wrong_type.calculated_rate = Decimal("0.12")
    session.add_all([prov, wrong_type])
    session.flush()
    with pytest.raises(RateCalculationError, match="actual_final"):
        true_up(session, provisional=prov, final=wrong_type, billed_base_amount=Decimal("1.00"))


def test_direct_labor_base_traceable(session):
    """§5 Direct Labor Base derives from jcl_entries cost_element=labor."""
    data = seed_all(session)
    assert compute_direct_labor_base(session, 2026) == Decimal("1250.00")
