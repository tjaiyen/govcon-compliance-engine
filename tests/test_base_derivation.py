"""v1.1: §5 allocation-base derivation from the ledger via the is_labor
flag — fringe (total company labor incl. indirect labor), overhead
(DL + allocated fringe), G&A (Total Cost Input)."""

import datetime
from decimal import Decimal

import pytest

from govcon.core.errors import RateCalculationError
from govcon.models import GLAccount, GLTransaction, IndirectPool, JCLEntry
from govcon.models.enums import CostElement, CostType, PoolName, PoolStatus, RateType
from govcon.services.rates import (
    compute_total_company_labor_base,
    derive_pool_base,
)
from tests.fixtures.synthetic_data import ga_pool, seed_all

D = datetime.date


def _labor_world(session, data):
    """Direct labor 1250 (fixture) + G&A labor 400 on an is_labor indirect
    account; JCL adds 300 of material for the TCI test."""
    gapool = ga_pool()
    session.add(gapool)
    session.flush()
    ga_labor = GLAccount(
        account_code="8100", account_name="G&A Salaries",
        cost_type=CostType.INDIRECT, is_labor=True, pool_assignment=gapool.pool_id,
    )
    session.add(ga_labor)
    session.flush()
    session.add(
        GLTransaction(
            account_id=ga_labor.account_id, amount=Decimal("400.00"),
            transaction_date=D(2026, 6, 16), period_id=data.period_open.period_id,
        )
    )
    session.add(
        JCLEntry(
            contract_id=data.contracts["pre_ndaa"].contract_id,
            clin_id="0001", wbs_id="2.1", cost_element=CostElement.MATERIAL,
            amount=Decimal("300.00"), period_id=data.period_open.period_id,
        )
    )
    session.flush()
    return gapool


def test_total_company_labor_base_includes_indirect_labor(session):
    data = seed_all(session)
    _labor_world(session, data)
    # 1250 direct labor + 400 G&A labor; the 400 fringe txn is NOT labor.
    assert compute_total_company_labor_base(session, 2026) == Decimal("1650.00")


def test_derive_fringe_base(session):
    data = seed_all(session)
    _labor_world(session, data)
    assert derive_pool_base(session, data.pool) == Decimal("1650.00")
    assert data.pool.allocation_base_amount == Decimal("1650.00")


def test_derive_overhead_base_chains_fringe_rate(session):
    """OH base = DL (1250) + allocated fringe at the approved 10% rate
    (125) = 1375.00."""
    data = seed_all(session)
    _labor_world(session, data)
    data.pool.calculated_rate = Decimal("0.1000")  # approved fringe rate
    oh = IndirectPool(
        pool_name=PoolName.OVERHEAD, fiscal_year=2026,
        rate_type=RateType.PROVISIONAL, status=PoolStatus.PENDING,
    )
    session.add(oh)
    session.flush()
    assert derive_pool_base(session, oh) == Decimal("1375.00")


def test_derive_ga_base_is_total_cost_input(session):
    """TCI = DL 1250 + other direct 300 + fringe 125 (10%) + OH 275
    ((1250+125)×20%) = 1950.00."""
    data = seed_all(session)
    gapool = _labor_world(session, data)
    data.pool.calculated_rate = Decimal("0.1000")
    oh = IndirectPool(
        pool_name=PoolName.OVERHEAD, fiscal_year=2026,
        rate_type=RateType.PROVISIONAL, status=PoolStatus.APPROVED,
        allocation_base_amount=Decimal("1375.00"),
    )
    oh.calculated_rate = Decimal("0.2000")
    session.add(oh)
    session.flush()
    assert derive_pool_base(session, gapool) == Decimal("1950.00")


def test_derivation_fails_loudly_without_upstream_rate(session):
    data = seed_all(session)
    _labor_world(session, data)
    oh = IndirectPool(
        pool_name=PoolName.OVERHEAD, fiscal_year=2027,  # no approved fringe FY2027
        rate_type=RateType.PROVISIONAL, status=PoolStatus.PENDING,
    )
    session.add(oh)
    session.flush()
    with pytest.raises(RateCalculationError, match="do not substitute"):
        derive_pool_base(session, oh)
