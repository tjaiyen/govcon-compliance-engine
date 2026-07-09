"""Phase 5 schedule generation: open-year gate, Schedule G/H/I real
reconciliation logic, Schedule L tolerance, Schedule N signer level, and
the SYNTHETIC banner on every schedule."""

import datetime
import json
from decimal import Decimal

import pytest

from govcon.core.errors import ScheduleGenerationError, SignerLevelError
from govcon.models import IndirectPool, PayrollRegister, Voucher
from govcon.models.billing import ScheduleType, SignerRole
from govcon.models.enums import PoolName, PoolStatus, RateType, ReconciliationStatus
from govcon.services.period_close import close_period
from tests.fixtures.synthetic_data import seed_all
from govcon.services.ice_schedules import BANNER, OUT_OF_SCOPE_SCHEDULES, generate_schedule

D = datetime.date


def _close_fy2026(session, data):
    close_period(session, data.period_open, closed_by="test")


def _final_pools(session, fringe="0.1000", overhead="0.2000", ga="0.0500"):
    pools = []
    for name, rate in ((PoolName.FRINGE, fringe), (PoolName.OVERHEAD, overhead), (PoolName.GA, ga)):
        pool = IndirectPool(
            pool_name=name, fiscal_year=2026, rate_type=RateType.ACTUAL_FINAL,
            status=PoolStatus.APPROVED, allocation_base_amount=Decimal("1.00"),
        )
        pool.calculated_rate = Decimal(rate)
        pools.append(pool)
    session.add_all(pools)
    session.flush()
    return pools


def test_open_fiscal_year_blocks_generation(session):
    seed_all(session)  # period 6 still open
    with pytest.raises(ScheduleGenerationError, match="not fully closed"):
        generate_schedule(session, 2026, ScheduleType.A)


def test_schedule_g_passes_and_carries_banner(session):
    data = seed_all(session)
    _close_fy2026(session, data)
    row = generate_schedule(session, 2026, ScheduleType.G)
    session.commit()
    assert row.reconciliation_status == ReconciliationStatus.PASSED
    content = json.loads(row.content)
    assert content["banner"] == BANNER
    assert all(line["passed"] for line in content["reconciliation_by_period"])


def test_schedule_h_burden_worked_example(session):
    """JCL labor 1250 at final rates 10/20/5% → 1250×1.1×1.2×1.05 = 1732.50."""
    data = seed_all(session)
    _final_pools(session)
    _close_fy2026(session, data)
    row = generate_schedule(session, 2026, ScheduleType.H)
    content = json.loads(row.content)
    line = next(
        c for c in content["contracts"]
        if c["contract_id"] == data.contracts["pre_ndaa"].contract_id
    )
    assert line["elements"]["labor"] == "1250.00"
    assert line["burdened_labor_at_claimed_rates"] == "1732.50"
    assert line["claimed_total"] == "1732.50"  # labor is the only element in fixture


def test_schedule_i_over_under_billing(session):
    """Claimed 1732.50 vs billed 1000.00 → under-billed +732.50, passes;
    billing beyond the claim fails the schedule."""
    data = seed_all(session)
    _final_pools(session)
    session.add(
        Voucher(
            contract_id=data.contracts["pre_ndaa"].contract_id,
            period_id=data.period_open.period_id,
            amount_billed=Decimal("1000.00"),
            billing_date=D(2026, 6, 28),
        )
    )
    session.flush()
    _close_fy2026(session, data)
    row = generate_schedule(session, 2026, ScheduleType.I)
    content = json.loads(row.content)
    line = next(
        c for c in content["contracts"]
        if c["contract_id"] == data.contracts["pre_ndaa"].contract_id
    )
    assert line["claimed_cumulative"] == "1732.50"
    assert line["billed_cumulative"] == "1000.00"
    assert line["over_under_billing"] == "732.50"
    assert row.reconciliation_status == ReconciliationStatus.PASSED


def test_schedule_l_tolerance(session):
    """Payroll 1250 vs distributed labor 1250 passes; a payroll register
    off by more than max($100, 0.1%) fails."""
    data = seed_all(session)
    session.add(
        PayrollRegister(
            period_id=data.period_open.period_id,
            total_gross_payroll=Decimal("1250.00"),
            source_document="941-SYNTH-2026Q2",
        )
    )
    session.flush()
    _close_fy2026(session, data)
    ok = generate_schedule(session, 2026, ScheduleType.L)
    content = json.loads(ok.content)
    june = next(l for l in content["reconciliation_by_period"] if l["period"] == "2026-06")
    assert june["passed"] is True
    # Now a badly-off register for the closed May period:
    session.add(
        PayrollRegister(
            period_id=data.period_closed.period_id,
            total_gross_payroll=Decimal("5000.00"),  # no labor distributed in May
            source_document="941-SYNTH-BAD",
        )
    )
    session.flush()
    bad = generate_schedule(session, 2026, ScheduleType.L)
    content = json.loads(bad.content)
    may = next(l for l in content["reconciliation_by_period"] if l["period"] == "2026-05")
    assert may["passed"] is False
    assert bad.reconciliation_status == ReconciliationStatus.FAILED


def test_schedule_n_signer_level(session):
    data = seed_all(session)
    _close_fy2026(session, data)
    with pytest.raises(SignerLevelError, match="no signer"):
        generate_schedule(session, 2026, ScheduleType.N)
    with pytest.raises(SignerLevelError, match="other"):
        generate_schedule(
            session, 2026, ScheduleType.N,
            signer_name="Sam Fixture", signer_role=SignerRole.OTHER,
        )
    row = generate_schedule(
        session, 2026, ScheduleType.N,
        signer_name="Alex Fixture", signer_role=SignerRole.CFO,
    )
    session.commit()
    content = json.loads(row.content)
    assert "certify" in content["certification"]
    assert content["banner"] == BANNER
    assert row.signer_role == SignerRole.CFO and row.signed_date is not None


def test_remaining_schedules_generate(session):
    data = seed_all(session)
    _final_pools(session)
    _close_fy2026(session, data)
    contents = {}
    for stype in (ScheduleType.A, ScheduleType.B, ScheduleType.C, ScheduleType.E, ScheduleType.O):
        row = generate_schedule(session, 2026, stype)
        contents[stype] = json.loads(row.content)
        assert contents[stype]["banner"] == BANNER
    # Schedule A carries the three claimed final rates:
    assert set(contents[ScheduleType.A]["claimed_rates"]) == {"fringe", "overhead", "ga"}
    # Out-of-scope schedules are acknowledged, not silently absent (§6):
    assert OUT_OF_SCOPE_SCHEDULES == ("D", "F", "J", "K", "M")
