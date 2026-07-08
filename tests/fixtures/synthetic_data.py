"""Hand-authored, deterministic synthetic fixtures (03_Tech_Stack_Decisions.md).

Everything here is invented test data — no real contracts, employers, or
people. Covers the roadmap Phase 0 fixture matrix: awards in all three TINA
eras (pre-2025-10-01 / pre-2026-06-30 / post), small vs other-than-small,
DoD vs civilian, one nontraditional-DC award, direct/indirect/deliberately-
unallowable accounts and transactions, open + closed periods, and a minimal
payroll dataset (as Decimal constants — payroll_registers is a Phase 5 table).
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from govcon.models import (
    Contract,
    GLAccount,
    GLTransaction,
    IndirectPool,
    JCLEntry,
    Period,
    Person,
    UnallowableCostCategory,
)
from govcon.models.enums import (
    AgencyType,
    CASCoverageType,
    ContractorSize,
    CostElement,
    CostType,
    DetectionMethod,
    PeriodStatus,
    PoolName,
    PoolStatus,
    RateType,
    ReconciliationStatus,
)

# --- payroll (Schedule L fixture data; its table lands in Phase 5) ---------
PAYROLL_TOTALS = {
    # fiscal period -> total gross payroll (conceptually the Form 941 total)
    (2026, 5): Decimal("48000.00"),
    (2026, 6): Decimal("52000.00"),
}


def contract_pre_2025() -> Contract:
    """DoD, other-than-small, awarded in the $2.0M TINA era."""
    return Contract(
        agency_type=AgencyType.DOD,
        award_date=datetime.date(2025, 6, 1),
        contract_value=Decimal("3500000.00"),
        tina_threshold_snapshot=Decimal("2000000.00"),
        cas_trigger_threshold_snapshot=Decimal("7500000.00"),
        cas_coverage_type=CASCoverageType.NONE,
        contractor_size=ContractorSize.OTHER_THAN_SMALL,
        performance_start_date=datetime.date(2025, 7, 1),
        performance_end_date=datetime.date(2027, 6, 30),
    )


def contract_pre_ndaa() -> Contract:
    """DoD, other-than-small, awarded 2026-05-15 — the $2.5M TINA band."""
    return Contract(
        agency_type=AgencyType.DOD,
        award_date=datetime.date(2026, 5, 15),
        contract_value=Decimal("12000000.00"),
        tina_threshold_snapshot=Decimal("2500000.00"),
        cas_trigger_threshold_snapshot=Decimal("7500000.00"),
        cas_coverage_type=CASCoverageType.MODIFIED,
        contractor_size=ContractorSize.OTHER_THAN_SMALL,
    )


def contract_post_ndaa() -> Contract:
    """DoD, other-than-small, awarded 2026-07-15 — the $10M TINA band."""
    return Contract(
        agency_type=AgencyType.DOD,
        award_date=datetime.date(2026, 7, 15),
        contract_value=Decimal("40000000.00"),
        tina_threshold_snapshot=Decimal("10000000.00"),
        cas_trigger_threshold_snapshot=Decimal("35000000.00"),
        cas_coverage_type=CASCoverageType.MODIFIED,
        contractor_size=ContractorSize.OTHER_THAN_SMALL,
    )


def contract_civilian_small() -> Contract:
    """Civilian agency, small business — CAS-exempt regardless of value."""
    return Contract(
        agency_type=AgencyType.CIVILIAN,
        award_date=datetime.date(2026, 2, 1),
        contract_value=Decimal("900000.00"),
        tina_threshold_snapshot=Decimal("2500000.00"),
        cas_trigger_threshold_snapshot=Decimal("7500000.00"),
        cas_coverage_type=CASCoverageType.NONE,
        contractor_size=ContractorSize.SMALL,
    )


def contract_nontrad_dc() -> Contract:
    """Nontraditional defense contractor — distinct exemption path (§7)."""
    return Contract(
        agency_type=AgencyType.DOD,
        award_date=datetime.date(2026, 7, 2),
        contract_value=Decimal("15000000.00"),
        tina_threshold_snapshot=Decimal("10000000.00"),
        cas_trigger_threshold_snapshot=Decimal("35000000.00"),
        cas_coverage_type=CASCoverageType.NONE,
        contractor_size=ContractorSize.OTHER_THAN_SMALL,
        is_nontraditional_dc=True,
    )


def open_period() -> Period:
    return Period(
        fiscal_year=2026,
        period_number=6,
        start_date=datetime.date(2026, 6, 1),
        end_date=datetime.date(2026, 6, 30),
        status=PeriodStatus.OPEN,
    )


def closed_period() -> Period:
    return Period(
        fiscal_year=2026,
        period_number=5,
        start_date=datetime.date(2026, 5, 1),
        end_date=datetime.date(2026, 5, 31),
        status=PeriodStatus.CLOSED,
        reconciliation_status=ReconciliationStatus.PASSED,
        closed_at=datetime.datetime(2026, 6, 5, 17, 0, 0),
        closed_by="fixture",
    )


def fringe_pool() -> IndirectPool:
    return IndirectPool(
        pool_name=PoolName.FRINGE,
        fiscal_year=2026,
        rate_type=RateType.PROVISIONAL,
        status=PoolStatus.APPROVED,
        allocation_base_amount=Decimal("100000.00"),
    )


def entertainment_category() -> UnallowableCostCategory:
    return UnallowableCostCategory(
        far_citation="31.205-14",
        category_name="Entertainment & Recreation",
        trap_logic_description="Keyword/category flag (tickets, parties, golf, social events)",
        detection_method=DetectionMethod.KEYWORD_PATTERN,
    )


def executive() -> Person:
    return Person(person_name="Alex Fixture", role="CEO", is_executive=True)


def staffer() -> Person:
    return Person(person_name="Sam Fixture", role="Cost Analyst", is_executive=False)


class SeededData:
    """Handles to everything seed_all() created, for direct use in tests."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def seed_all(session: Session) -> SeededData:
    """Seed the full deterministic matrix and return handles."""
    contracts = {
        "pre_2025": contract_pre_2025(),
        "pre_ndaa": contract_pre_ndaa(),
        "post_ndaa": contract_post_ndaa(),
        "civilian_small": contract_civilian_small(),
        "nontrad": contract_nontrad_dc(),
    }
    period_open = open_period()
    period_closed = closed_period()
    pool = fringe_pool()
    category = entertainment_category()
    exec_person = executive()
    staff_person = staffer()

    session.add_all(
        [*contracts.values(), period_open, period_closed, pool, category, exec_person, staff_person]
    )
    session.flush()

    acct_direct_labor = GLAccount(
        account_code="5000",
        account_name="Direct Labor",
        cost_type=CostType.DIRECT,
    )
    acct_fringe = GLAccount(
        account_code="6100",
        account_name="Fringe - Health Insurance",
        cost_type=CostType.INDIRECT,
        pool_assignment=pool.pool_id,
    )
    acct_entertainment = GLAccount(
        account_code="7900",
        account_name="Entertainment (Unallowable)",
        cost_type=CostType.UNALLOWABLE,
        far_31_205_citation=category.category_id,
    )
    session.add_all([acct_direct_labor, acct_fringe, acct_entertainment])
    session.flush()

    txn_direct = GLTransaction(
        account_id=acct_direct_labor.account_id,
        contract_id=contracts["pre_ndaa"].contract_id,
        person_id=staff_person.person_id,
        amount=Decimal("1250.00"),
        transaction_date=datetime.date(2026, 6, 10),
        period_id=period_open.period_id,
        source_document="TS-2026-06-10-001",
    )
    txn_indirect = GLTransaction(
        account_id=acct_fringe.account_id,
        amount=Decimal("400.00"),
        transaction_date=datetime.date(2026, 6, 12),
        period_id=period_open.period_id,
        source_document="INV-HC-4471",
    )
    txn_unallowable = GLTransaction(
        account_id=acct_entertainment.account_id,
        contract_id=contracts["pre_ndaa"].contract_id,
        amount=Decimal("300.00"),
        transaction_date=datetime.date(2026, 6, 15),
        period_id=period_open.period_id,
        source_document="RCPT-GOLF-0615",
    )
    jcl_labor = JCLEntry(
        contract_id=contracts["pre_ndaa"].contract_id,
        clin_id="0001",
        wbs_id="1.2.3",
        cost_element=CostElement.LABOR,
        amount=Decimal("1250.00"),
        quantity=Decimal("25.0000"),  # hours
        period_id=period_open.period_id,
    )
    session.add_all([txn_direct, txn_indirect, txn_unallowable, jcl_labor])
    session.flush()

    return SeededData(
        contracts=contracts,
        period_open=period_open,
        period_closed=period_closed,
        pool=pool,
        category=category,
        exec_person=exec_person,
        staff_person=staff_person,
        acct_direct_labor=acct_direct_labor,
        acct_fringe=acct_fringe,
        acct_entertainment=acct_entertainment,
        txn_direct=txn_direct,
        txn_indirect=txn_indirect,
        txn_unallowable=txn_unallowable,
        jcl_labor=jcl_labor,
    )
