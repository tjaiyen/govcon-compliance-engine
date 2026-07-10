"""FAR 15.404 pricing-analysis determinations (grounded to 48 CFR 15.404-1 / -3).

15.404-1: cost analysis iff certified cost or pricing data are required; else price
analysis. 15.404-3(c)(1): subcontractor certified data required when the sub price is
BOTH > the dated threshold AND > 10% of the prime's price, OR ≥ $20M. Expected
outcomes pre-registered (B35).
"""

import datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from govcon.api import create_app
from govcon.models import ContractAction
from govcon.models.enums import ContractActionType
from govcon.services.pricing_analysis import (
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


def test_workbench_has_far15_card(session_factory):
    html = TestClient(create_app(session_factory=session_factory)).get("/").text
    assert 'id="f-far15"' in html
    assert "/api/pricing-analysis" in html and "/api/subcontract-data" in html
