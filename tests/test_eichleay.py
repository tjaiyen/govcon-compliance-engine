"""Phase 9b: Eichleay — known worked example, incomplete-when-undocumented,
reconciled-inputs precondition, and row-level reproducibility."""

import datetime
from decimal import Decimal

import pytest

from govcon.models import Contract, Voucher
from govcon.models.claims import ClaimStatus
from govcon.models.enums import AgencyType, CASCoverageType, ContractorSize, CostElement
from govcon.services.allowability import post_transaction
from govcon.services.eichleay import EichleayError, calculate_eichleay, verify_claim
from govcon.services.period_close import close_period
from tests.fixtures.synthetic_data import seed_all

D = datetime.date


def _setup(session, data, *, close=True):
    """Worked-example world: delayed contract bills $200,000, another
    contract bills $800,000 (total company $1,000,000), all tied GL=JCL so
    the period closes cleanly. Performance 2026-01-01..2026-10-27 = 300
    days inclusive."""
    from govcon.models import JCLEntry

    delayed = Contract(
        agency_type=AgencyType.DOD,
        award_date=D(2025, 12, 1),
        performance_start_date=D(2026, 1, 1),
        performance_end_date=D(2026, 10, 27),  # 300 days inclusive
        contract_value=Decimal("2000000.00"),
        tina_threshold_snapshot=Decimal("2500000.00"),
        cas_trigger_threshold_snapshot=Decimal("7500000.00"),
        cas_coverage_type=CASCoverageType.NONE,
        contractor_size=ContractorSize.OTHER_THAN_SMALL,
    )
    session.add(delayed)
    session.flush()
    other = data.contracts["post_ndaa"]

    for contract, amount, doc in (
        (delayed, Decimal("200000.00"), "delayed work"),
        (other, Decimal("800000.00"), "other work"),
    ):
        post_transaction(
            session,
            account_id=data.acct_direct_labor.account_id,
            contract_id=contract.contract_id,
            amount=amount,
            transaction_date=D(2026, 6, 15),
            period_id=data.period_open.period_id,
            source_document=doc,
        )
        session.add(
            JCLEntry(
                contract_id=contract.contract_id,
                clin_id="0001",
                wbs_id="9.1",
                cost_element=CostElement.LABOR,
                amount=amount,
                period_id=data.period_open.period_id,
            )
        )
        session.add(
            Voucher(
                contract_id=contract.contract_id,
                period_id=data.period_open.period_id,
                amount_billed=amount,
                billing_date=D(2026, 6, 30),
            )
        )
    session.flush()
    if close:
        close_period(session, data.period_open, closed_by="eichleay-test")
    return delayed


def test_worked_example(session):
    """(200,000 / 1,000,000) × 150,000 = 30,000 allocable; / 300 days =
    100.00/day; × 45 delay days = 4,500.00."""
    data = seed_all(session)
    delayed = _setup(session, data)
    claim, warnings = calculate_eichleay(
        session,
        delayed,
        delay_start=D(2026, 5, 1),
        delay_end=D(2026, 6, 14),  # 45 days inclusive
        total_home_office_overhead=Decimal("150000.00"),
        government_caused_delay=True,
        contractor_on_standby=True,
        no_replacement_work=True,
    )
    session.commit()
    assert claim.allocable_overhead == Decimal("30000.00")
    assert claim.daily_overhead_rate == Decimal("100.0000")
    assert claim.delay_days == 45
    assert claim.unabsorbed_overhead_claim == Decimal("4500.00")
    assert claim.status == ClaimStatus.COMPLETE
    assert warnings == []
    assert verify_claim(claim)  # reproducible from its own row


def test_undocumented_prerequisite_flags_incomplete_not_blocked(session):
    data = seed_all(session)
    delayed = _setup(session, data)
    claim, warnings = calculate_eichleay(
        session,
        delayed,
        delay_start=D(2026, 5, 1),
        delay_end=D(2026, 6, 14),
        total_home_office_overhead=Decimal("150000.00"),
        government_caused_delay=True,
        contractor_on_standby=None,  # undocumented
        no_replacement_work=True,
    )
    assert claim.status == ClaimStatus.INCOMPLETE
    assert claim.unabsorbed_overhead_claim == Decimal("4500.00")  # computed anyway
    assert any("UNDOCUMENTED" in w for w in warnings)


def test_change_order_only_delay_warns(session):
    data = seed_all(session)
    delayed = _setup(session, data)
    claim, warnings = calculate_eichleay(
        session,
        delayed,
        delay_start=D(2026, 5, 1),
        delay_end=D(2026, 6, 14),
        total_home_office_overhead=Decimal("150000.00"),
        government_caused_delay=False,
        contractor_on_standby=True,
        no_replacement_work=True,
    )
    assert claim.status == ClaimStatus.INCOMPLETE
    assert any("Community Heating" in w for w in warnings)


def test_unreconciled_inputs_refused(session):
    """Vouchers in an OPEN period → the calculator refuses (§10: inputs
    reconciled is a precondition, not an assumption)."""
    data = seed_all(session)
    delayed = _setup(session, data, close=False)  # period stays open
    with pytest.raises(EichleayError, match="OPEN periods"):
        calculate_eichleay(
            session,
            delayed,
            delay_start=D(2026, 5, 1),
            delay_end=D(2026, 6, 14),
            total_home_office_overhead=Decimal("150000.00"),
            government_caused_delay=True,
            contractor_on_standby=True,
            no_replacement_work=True,
        )


def test_missing_performance_dates_refused(session):
    data = seed_all(session)
    _setup(session, data)
    no_dates = data.contracts["pre_ndaa"]  # fixture carries no perf dates
    with pytest.raises(EichleayError, match="performance_start_date"):
        calculate_eichleay(
            session,
            no_dates,
            delay_start=D(2026, 5, 1),
            delay_end=D(2026, 6, 14),
            total_home_office_overhead=Decimal("150000.00"),
            government_caused_delay=True,
            contractor_on_standby=True,
            no_replacement_work=True,
        )
