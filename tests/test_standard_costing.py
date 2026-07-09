"""Phase 12: the six variance formulas against classic worked examples,
the single sign convention with derived `favorable`, the SHA-not-actual-
hours overhead basis (the spec's own corrected error, pinned), and
independence from FAR allowability."""

import datetime
from decimal import Decimal

import pytest

from govcon.models import JCLEntry, OverheadBudget, StandardCost
from govcon.models.enums import CostElement
from govcon.models.standard_costing import StandardCostElement, VarianceType
from govcon.services.variances import (
    VarianceError,
    budgeted_overhead_at_sha,
    find_standard,
    labor_variances,
    material_variances,
    overhead_variances,
    record_variance,
    standard_hours_allowed,
)
from tests.fixtures.synthetic_data import seed_all

D = datetime.date

# Classic worked example: 500 units completed.
# Labor: std 2 hr/unit @ $30. Actual 1,050 hr @ $31 (amount 32,550).
#   Rate = (30−31)×1050 = −1,050 U.  Efficiency = (1000−1050)×30 = −1,500 U.
# Material: std 3 lb/unit @ $4. Actual 1,600 lb @ $3.90 (amount 6,240).
#   Price = (4−3.90)×1600 = +160 F.  Usage = (1500−1600)×4 = −400 U.
# Overhead: budget fixed 8,000 + $5/hr variable; std OH rate $13.50/SHA;
#   actual OH 13,300. SHA = 1,000.
#   Budgeted@SHA = 13,000. Spending = 13,000−13,300 = −300 U.
#   Applied = 13,500. Volume = 13,500−13,000 = +500 F.


def _standard(session, element, qty, rate, eff=D(2026, 1, 1)):
    row = StandardCost(
        cost_element=element,
        operation_or_product_code="1.2.3",  # the fixture wbs_id
        standard_quantity=Decimal(qty),
        standard_rate=Decimal(rate),
        effective_date=eff,
    )
    session.add(row)
    session.flush()
    return row


def _entry(session, data, element, amount, qty, units):
    entry = JCLEntry(
        contract_id=data.contracts["pre_ndaa"].contract_id,
        clin_id="0002",
        wbs_id="1.2.3",
        cost_element=element,
        amount=Decimal(amount),
        quantity=Decimal(qty),
        units_completed=Decimal(units),
        period_id=data.period_open.period_id,
    )
    session.add(entry)
    session.flush()
    return entry


def test_labor_rate_and_efficiency_worked_example(session):
    data = seed_all(session)
    standard = _standard(session, StandardCostElement.LABOR, "2", "30")
    entry = _entry(session, data, CostElement.LABOR, "32550.00", "1050", "500")
    rate, efficiency = labor_variances(session, entry, standard)
    session.commit()
    assert rate.variance_amount == Decimal("-1050.00") and rate.favorable is False
    assert efficiency.variance_amount == Decimal("-1500.00") and efficiency.favorable is False
    assert rate.variance_type == VarianceType.LABOR_RATE


def test_material_price_and_usage_worked_example(session):
    data = seed_all(session)
    standard = _standard(session, StandardCostElement.MATERIAL, "3", "4")
    entry = _entry(session, data, CostElement.MATERIAL, "6240.00", "1600", "500")
    price, usage = material_variances(session, entry, standard)
    assert price.variance_amount == Decimal("160.00") and price.favorable is True
    assert usage.variance_amount == Decimal("-400.00") and usage.favorable is False


def test_overhead_uses_sha_basis_never_actual_hours(session):
    """The spec's own corrected error, pinned: budgeted overhead at SHA
    (1,000 hrs → 13,000), NOT at actual hours (1,050 → 13,250 would give a
    −50 spending variance — the wrong number)."""
    data = seed_all(session)
    standard = _standard(session, StandardCostElement.OVERHEAD, "2", "13.50")
    budget = OverheadBudget(
        fiscal_year=2026,
        fixed_overhead_budget=Decimal("8000.00"),
        variable_overhead_rate=Decimal("5.0000"),
        effective_date=D(2026, 1, 1),
    )
    session.add(budget)
    session.flush()
    sha = standard_hours_allowed(Decimal("500"), standard)
    assert sha == Decimal("1000")
    assert budgeted_overhead_at_sha(budget, sha) == Decimal("13000.00")
    spending, volume = overhead_variances(
        session,
        standard=standard,
        budget=budget,
        period_id=data.period_open.period_id,
        units_completed=Decimal("500"),
        actual_overhead=Decimal("13300.00"),
    )
    assert spending.variance_amount == Decimal("-300.00")  # not −50
    assert spending.favorable is False
    assert volume.variance_amount == Decimal("500.00")
    assert volume.favorable is True


def test_favorable_is_derived_never_supplied(session):
    import inspect

    from govcon.services.variances import record_variance as rv

    assert "favorable" not in inspect.signature(rv).parameters
    data = seed_all(session)
    standard = _standard(session, StandardCostElement.LABOR, "2", "30")
    row = record_variance(
        session,
        standard=standard,
        period_id=data.period_open.period_id,
        variance_type=VarianceType.LABOR_RATE,
        standard_amount=Decimal("100.00"),
        actual_amount=Decimal("100.00"),
    )
    assert row.variance_amount == Decimal("0.00") and row.favorable is False  # zero ≠ favorable


def test_missing_quantity_or_units_refused(session):
    data = seed_all(session)
    standard = _standard(session, StandardCostElement.LABOR, "2", "30")
    bare = JCLEntry(
        contract_id=data.contracts["pre_ndaa"].contract_id,
        clin_id="0003",
        wbs_id="1.2.3",
        cost_element=CostElement.LABOR,
        amount=Decimal("100.00"),
        period_id=data.period_open.period_id,
    )
    session.add(bare)
    session.flush()
    with pytest.raises(VarianceError, match="no quantity"):
        labor_variances(session, bare, standard)


def test_standard_matching_is_dated_and_by_code(session):
    data = seed_all(session)
    old = _standard(session, StandardCostElement.LABOR, "2", "28")
    old.superseded_date = D(2026, 6, 1)  # supersession sets superseded_date (allowed)
    new = _standard(session, StandardCostElement.LABOR, "2", "30", eff=D(2026, 6, 1))
    session.flush()
    assert find_standard(
        session, code="1.2.3", cost_element=StandardCostElement.LABOR, on_date=D(2026, 5, 1)
    ).standard_rate == Decimal("28.0000")
    assert find_standard(
        session, code="1.2.3", cost_element=StandardCostElement.LABOR, on_date=D(2026, 7, 1)
    ).standard_rate == Decimal("30.0000")
    with pytest.raises(VarianceError, match="do not invent"):
        find_standard(
            session, code="9.9.9", cost_element=StandardCostElement.LABOR, on_date=D(2026, 7, 1)
        )


def test_variance_and_allowability_are_independent(session):
    """§14: a favorable variance never flips an unallowable actual cost.
    The fixture entertainment transaction stays unallowable in its vector
    while a favorable variance coexists on the same contract's JCL data."""
    from govcon.services.allowability import post_transaction

    data = seed_all(session)
    txn = post_transaction(
        session,
        account_id=data.acct_entertainment.account_id,
        contract_id=data.contracts["pre_ndaa"].contract_id,
        amount=Decimal("100.00"),
        transaction_date=D(2026, 6, 18),
        period_id=data.period_open.period_id,
    )
    standard = _standard(session, StandardCostElement.MATERIAL, "3", "4")
    entry = _entry(session, data, CostElement.MATERIAL, "5800.00", "1500", "500")
    price, usage = material_variances(session, entry, standard)
    session.commit()
    assert price.favorable is True  # (4 − 3.8667)×1500 > 0
    # ...and the unallowable determination is untouched by it:
    assert txn.allowability_vector["far_31_2_result"]["result"] == "unallowable"
