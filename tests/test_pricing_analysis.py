"""FAR 15.404 pricing-analysis determinations (grounded to 48 CFR 15.404-1 / -3).

15.404-1: cost analysis iff certified cost or pricing data are required; else price
analysis. 15.404-3(c)(1): subcontractor certified data required when the sub price is
BOTH > the dated threshold AND > 10% of the prime's price, OR ≥ $20M. Expected
outcomes pre-registered (B35).
"""

import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from govcon.api import create_app
from govcon.models import ContractAction
from govcon.models.enums import ContractActionType
from govcon.services.pricing_analysis import (
    compute_facilities_capital_profit,
    compute_weighted_guidelines_profit,
    determine_price_or_cost_analysis,
    determine_subcontract_certified_data,
)

D = datetime.date
_ON = D(2026, 7, 15)  # TINA threshold in force: $10,000,000 (post-2026-06-30)


def _action(value, **exc):
    return ContractAction(
        action_type=ContractActionType.OTHER_NEGOTIATED_ACTION,
        action_date=_ON,
        proposed_value=Decimal(value),
        **{f"tina_exception_{k}": v for k, v in exc.items()},
    )


# ------------------------------------------------ FAR 15.404-1: price vs cost analysis
def test_cost_analysis_required_when_certified_data_required(session):
    # above the $10M threshold, all exceptions evaluated & none apply → cost analysis
    d = determine_price_or_cost_analysis(session, _action(
        "12000000.00", adequate_price_competition=False,
        commercial_product_service=False, prices_set_by_law=False, waiver_granted=False))
    assert d.analysis_required == "cost_analysis" and d.certified_data_required is True
    assert any("COST analysis" in r for r in d.reasons)
    assert d.source_citation == "FAR 15.404-1"


def test_price_analysis_when_exception_applies(session):
    d = determine_price_or_cost_analysis(session, _action(
        "12000000.00", adequate_price_competition=True))
    assert d.analysis_required == "price_analysis" and d.certified_data_required is False


def test_price_analysis_below_threshold(session):
    d = determine_price_or_cost_analysis(session, _action("8000000.00"))
    assert d.analysis_required == "price_analysis" and d.certified_data_required is False


def test_pending_when_exceptions_unevaluated(session):
    # above threshold but exceptions not yet evaluated → the honest "pending" state
    d = determine_price_or_cost_analysis(session, _action("12000000.00"))
    assert d.analysis_required == "pending" and d.certified_data_required is None


# ------------------------------------------ FAR 15.404-3(c)(1): subcontract certified data
def test_sub_required_over_threshold_and_over_ten_percent(session):
    d = determine_subcontract_certified_data(
        session, prime_proposed_value=Decimal("100000000"),
        sub_proposed_value=Decimal("12000000"), on_date=_ON)
    assert d.certified_data_required is True
    assert d.exceeds_threshold and d.exceeds_ten_percent_of_prime
    assert d.threshold_value == Decimal("10000000.00")
    assert d.ten_percent_of_prime == Decimal("10000000.0")


def test_sub_not_required_below_threshold(session):
    # sub below the $10M threshold → not required even though it exceeds 10% of prime
    d = determine_subcontract_certified_data(
        session, prime_proposed_value=Decimal("10000000"),
        sub_proposed_value=Decimal("9000000"), on_date=_ON)
    assert d.certified_data_required is False
    assert d.exceeds_threshold is False and d.exceeds_ten_percent_of_prime is True


def test_sub_required_by_absolute_20m_even_when_under_ten_percent(session):
    # sub ≥ $20M triggers regardless of the 10% test (sub $25M < 10% of a $1B prime)
    d = determine_subcontract_certified_data(
        session, prime_proposed_value=Decimal("1000000000"),
        sub_proposed_value=Decimal("25000000"), on_date=_ON)
    assert d.certified_data_required is True and d.meets_absolute_20m is True
    assert d.exceeds_ten_percent_of_prime is False  # $25M is NOT > 10% of $1B ($100M)
    assert any("$20 million or more" in r for r in d.reasons)


def test_sub_not_required_over_threshold_but_under_ten_percent(session):
    # sub > $10M threshold but only 5% of a $300M prime, and < $20M → not required
    d = determine_subcontract_certified_data(
        session, prime_proposed_value=Decimal("300000000"),
        sub_proposed_value=Decimal("12000000"), on_date=_ON)
    assert d.certified_data_required is False
    assert d.exceeds_threshold is True and d.exceeds_ten_percent_of_prime is False


def test_sub_caveats_flag_co_discretion_and_prime_responsibility(session):
    d = determine_subcontract_certified_data(
        session, prime_proposed_value=Decimal("100000000"),
        sub_proposed_value=Decimal("1000000"), on_date=_ON)
    assert any("15.404-3(c)(2)" in c for c in d.caveats)  # CO should still require below
    assert any("15.404-3(a)" in c for c in d.caveats)     # prime remains responsible


# ---------------------------------------------------------------------- API surface
def test_api_pricing_analysis_and_subcontract(session_factory):
    c = TestClient(create_app(session_factory=session_factory))
    pa = c.post("/api/pricing-analysis", json={
        "action_date": "2026-07-15", "proposed_value": "12000000.00",
        "tina_exception_adequate_price_competition": True}).json()
    assert pa["available"] and pa["analysis_required"] == "price_analysis"

    sd = c.post("/api/subcontract-data", json={
        "on_date": "2026-07-15", "prime_proposed_value": "100000000",
        "sub_proposed_value": "12000000"}).json()
    assert sd["available"] and sd["certified_data_required"] is True
    assert sd["threshold_value"] == "10000000.00"


# ------------------------------------- FAR 15.404-4 / DFARS 215.404-71 weighted guidelines
def test_weighted_guidelines_objective_and_rate():
    d = compute_weighted_guidelines_profit(
        cost_base=Decimal("1000000"), contract_type="ffp_no_financing",
        technical_pct=Decimal("5"), management_pct=Decimal("5"),
        contract_type_risk_pct=Decimal("5"))
    # perf = (5%+5%)×1M = 100k; CTR = 5%×1M = 50k; total = 150k = 15% of the base
    assert d.performance_risk_profit == Decimal("100000.00")
    assert d.contract_type_risk_profit == Decimal("50000.00")
    assert d.total_profit_objective == Decimal("150000.00")
    assert d.profit_rate_pct == Decimal("15")
    assert all(f["in_range"] for f in d.factor_findings)
    assert d.source_citation == "DFARS 215.404-71 (FAR 15.404-4)"


def test_weighted_guidelines_flags_out_of_range_factor():
    # technical 9% is outside the normal 3–7% range (no technology incentive)
    d = compute_weighted_guidelines_profit(
        cost_base=Decimal("1000000"), contract_type="cpff",
        technical_pct=Decimal("9"), management_pct=Decimal("5"),
        contract_type_risk_pct=Decimal("0.5"))
    tech = next(f for f in d.factor_findings if f["factor"] == "technical risk")
    assert tech["in_range"] is False and tech["designated_range"] == "3% to 7%"
    assert any("OUTSIDE the DFARS designated range" in c for c in d.caveats)


def test_weighted_guidelines_technology_incentive_widens_range():
    d = compute_weighted_guidelines_profit(
        cost_base=Decimal("1000000"), contract_type="cpif",
        technical_pct=Decimal("9"), management_pct=Decimal("5"),
        contract_type_risk_pct=Decimal("1"), technology_incentive=True)
    tech = next(f for f in d.factor_findings if f["factor"] == "technical risk")
    assert tech["in_range"] is True and tech["designated_range"] == "7% to 11%"


def test_weighted_guidelines_includes_provided_facilities_capital():
    d = compute_weighted_guidelines_profit(
        cost_base=Decimal("1000000"), contract_type="cpff",
        technical_pct=Decimal("5"), management_pct=Decimal("5"),
        contract_type_risk_pct=Decimal("0.5"),
        facilities_capital_profit=Decimal("7500"))
    assert d.facilities_capital_profit == Decimal("7500")
    # 100k perf + 5k CTR (0.5% of 1M) + 7.5k FCCM = 112.5k
    assert d.total_profit_objective == Decimal("112500.00")


def test_weighted_guidelines_unknown_contract_type_raises():
    with pytest.raises(ValueError):
        compute_weighted_guidelines_profit(
            cost_base=Decimal("1000000"), contract_type="handshake",
            technical_pct=Decimal("5"), management_pct=Decimal("5"),
            contract_type_risk_pct=Decimal("5"))


def test_api_weighted_guidelines(session_factory):
    c = TestClient(create_app(session_factory=session_factory))
    wg = c.post("/api/weighted-guidelines", json={
        "cost_base": "1000000", "contract_type": "ffp_no_financing",
        "technical_pct": "5", "management_pct": "5", "contract_type_risk_pct": "5"}).json()
    assert wg["available"] and wg["total_profit_objective"] == "150000.00"
    assert wg["profit_rate_pct"] == "15.00"


# ---------------------------------------- DFARS 215.404-71-4 facilities capital employed
def test_facilities_capital_only_equipment_earns_profit():
    d = compute_facilities_capital_profit(
        equipment_capital=Decimal("2000000"),
        land_capital=Decimal("500000"), buildings_capital=Decimal("1000000"))
    # only equipment earns: $2M × 17.5% = $350,000; land + buildings = 0
    assert d.facilities_capital_profit == Decimal("350000.00")
    assert d.equipment_factor_pct == Decimal("17.5")
    assert next(f for f in d.factor_findings if f["factor"] == "land")["value_pct"] == "0"


def test_facilities_capital_flags_equipment_factor_out_of_range():
    d = compute_facilities_capital_profit(
        equipment_capital=Decimal("1000000"), equipment_factor_pct=Decimal("30"))
    eq = next(f for f in d.factor_findings if f["factor"] == "equipment")
    assert eq["in_range"] is False and eq["designated_range"] == "10% to 25%"
    assert any("OUTSIDE the DFARS 10%–25%" in c for c in d.caveats)


def test_facilities_capital_composes_into_weighted_guidelines(session_factory):
    c = TestClient(create_app(session_factory=session_factory))
    fc = c.post("/api/facilities-capital", json={"equipment_capital": "2000000"}).json()
    assert fc["available"] and fc["facilities_capital_profit"] == "350000.00"
    wg = c.post("/api/weighted-guidelines", json={
        "cost_base": "1000000", "contract_type": "cpff", "technical_pct": "5",
        "management_pct": "5", "contract_type_risk_pct": "0.5",
        "facilities_capital_profit": fc["facilities_capital_profit"]}).json()
    # 100k perf + 5k CTR (0.5%) + 350k facilities capital = 455k
    assert wg["total_profit_objective"] == "455000.00"


def test_workbench_has_far15_card(session_factory):
    html = TestClient(create_app(session_factory=session_factory)).get("/").text
    assert 'id="f-far15"' in html
    assert "/api/pricing-analysis" in html and "/api/subcontract-data" in html
    assert 'id="f-wg"' in html and "/api/weighted-guidelines" in html
    assert 'id="w-equip"' in html and "/api/facilities-capital" in html
