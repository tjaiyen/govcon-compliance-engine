"""§4a executive-compensation cap: 80/90/100% escalation, auto-reclass of
the over-cap excess, and the never-invent-the-cap behavior. The cap row is
a CLEARLY-SYNTHETIC fixture — production seeds none until a verified value
exists."""

import datetime
from decimal import Decimal

import pytest

from govcon.models import GLAccount
from govcon.models.enums import CostType
from govcon.services.allowability import post_transaction
from govcon.services.compensation import exec_comp_status, reclassify_excess
from tests.fixtures.synthetic_data import (
    ga_pool,
    seed_all,
    seeded_category,
    synthetic_exec_comp_cap,
)

D = datetime.date
AS_OF = D(2026, 6, 20)


def _comp_setup(session, data):
    session.add(synthetic_exec_comp_cap())  # cap = 500,000.00 (synthetic)
    gapool = ga_pool()
    session.add(gapool)
    session.flush()
    comp_account = GLAccount(
        account_code="8100",
        account_name="Executive Compensation",
        cost_type=CostType.INDIRECT,
        pool_assignment=gapool.pool_id,
        is_compensation=True,  # only flagged accounts count toward YTD comp
    )
    unallowable_account = GLAccount(
        account_code="7920",
        account_name="Exec Comp Over Cap (Unallowable)",
        cost_type=CostType.UNALLOWABLE,
        far_31_205_citation=seeded_category(session, "31.205-6(p)").category_id,
    )
    session.add_all([comp_account, unallowable_account])
    session.flush()
    return comp_account, unallowable_account


def _pay(session, data, account, amount):
    return post_transaction(
        session,
        account_id=account.account_id,
        person_id=data.exec_person.person_id,
        amount=amount,
        transaction_date=AS_OF,
        period_id=data.period_open.period_id,
        source_document="PAYROLL-SYNTH",
    )


@pytest.mark.parametrize(
    ("ytd", "expected_level"),
    [
        (Decimal("300000.00"), "ok"),            # 60%
        (Decimal("400000.00"), "informational"), # 80% boundary
        (Decimal("450000.00"), "warning"),       # 90% boundary
        (Decimal("500000.00"), "exceeded"),      # 100% boundary
        (Decimal("520000.00"), "exceeded"),
    ],
)
def test_escalation_levels(session, ytd, expected_level):
    data = seed_all(session)
    comp_account, _ = _comp_setup(session, data)
    _pay(session, data, comp_account, ytd)
    status = exec_comp_status(session, data.exec_person, 2026, AS_OF)
    assert status.alert_level == expected_level


def test_excess_over_cap_auto_reclassifies(session):
    data = seed_all(session)
    comp_account, unallowable_account = _comp_setup(session, data)
    _pay(session, data, comp_account, Decimal("520000.00"))
    pair = reclassify_excess(
        session,
        data.exec_person,
        2026,
        as_of=AS_OF,
        period_id=data.period_open.period_id,
        comp_account=comp_account,
        unallowable_account=unallowable_account,
    )
    session.commit()
    assert pair is not None
    out, into = pair
    assert out.amount == Decimal("-20000.00")
    assert into.amount == Decimal("20000.00")
    assert into.account_id == unallowable_account.account_id
    assert into.allowability_vector["far_31_2_result"]["far_citation"] == "31.205-6(p)"
    # Idempotent: total comp paid is unchanged, but the excess is already
    # reclassified — a second run posts nothing.
    assert (
        reclassify_excess(
            session,
            data.exec_person,
            2026,
            as_of=AS_OF,
            period_id=data.period_open.period_id,
            comp_account=comp_account,
            unallowable_account=unallowable_account,
        )
        is None
    )


def test_under_cap_reclassifies_nothing(session):
    data = seed_all(session)
    comp_account, unallowable_account = _comp_setup(session, data)
    _pay(session, data, comp_account, Decimal("100000.00"))
    assert (
        reclassify_excess(
            session,
            data.exec_person,
            2026,
            as_of=AS_OF,
            period_id=data.period_open.period_id,
            comp_account=comp_account,
            unallowable_account=unallowable_account,
        )
        is None
    )


def test_unverified_year_raises_not_invents(session):
    """Migration 0012 seeds the VERIFIED CY2024/CY2025 caps ($646K/$671K,
    OMB/OFPP table) but deliberately leaves CY2026 open — no primary source
    had published it at verification time. A 2026 lookup without the
    synthetic test row raises rather than extrapolating."""
    data = seed_all(session)
    with pytest.raises(LookupError, match="do not invent"):
        exec_comp_status(session, data.exec_person, 2026, AS_OF)
