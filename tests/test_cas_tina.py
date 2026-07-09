"""Phase 7: dated-threshold branching (pre/post 2026-06-30), explicit
exemption paths, per-action TINA evaluation with no vehicle inheritance."""

import datetime
from decimal import Decimal

from govcon.models import Contract, ContractAction
from govcon.models.enums import (
    AgencyType,
    CASCoverageType,
    ContractActionType,
    ContractorSize,
)
from govcon.services.cas_tina import (
    determine_cas_coverage,
    determine_tina_applicability,
)
from tests.fixtures.synthetic_data import seed_all

D = datetime.date


def _contract(session, *, award, value, size=ContractorSize.OTHER_THAN_SMALL,
              nontrad=False, coverage=CASCoverageType.NONE,
              tina_snap="2500000.00", cas_snap="7500000.00"):
    contract = Contract(
        agency_type=AgencyType.DOD,
        award_date=award,
        contract_value=Decimal(value),
        tina_threshold_snapshot=Decimal(tina_snap),
        cas_trigger_threshold_snapshot=Decimal(cas_snap),
        cas_coverage_type=coverage,
        contractor_size=size,
        is_nontraditional_dc=nontrad,
    )
    session.add(contract)
    session.flush()
    return contract


def _action(session, contract, *, action_date, value, **exceptions):
    action = ContractAction(
        contract_id=contract.contract_id,
        action_type=ContractActionType.TASK_ORDER,
        action_date=action_date,
        proposed_value=None if value is None else Decimal(value),
        **exceptions,
    )
    session.add(action)
    session.flush()
    return action


# --- CAS coverage tiers -----------------------------------------------------


def test_small_business_exempt_regardless_of_value(session):
    seed_all(session)
    contract = _contract(session, award=D(2026, 7, 15), value="500000000.00",
                         size=ContractorSize.SMALL)
    result = determine_cas_coverage(session, contract)
    assert result.tier == "exempt_small_business"
    assert not result.requires_review


def test_nontraditional_is_flagged_for_review_not_silently_exempt(session):
    data = seed_all(session)
    result = determine_cas_coverage(session, data.contracts["nontrad"])
    assert result.tier == "review_nontraditional"
    assert result.requires_review is True
    assert any("REVIEW REQUIRED" in r for r in result.reasons)


def test_dated_threshold_branches_at_ndaa_boundary(session):
    """The Phase 7 branch test: the same $12M contract is MODIFIED coverage
    pre-2026-06-30 (trigger $7.5M) and NO coverage after (trigger $35M)."""
    seed_all(session)
    pre = _contract(session, award=D(2026, 5, 15), value="12000000.00")
    post = _contract(session, award=D(2026, 7, 15), value="12000000.00",
                     tina_snap="10000000.00", cas_snap="35000000.00")
    assert determine_cas_coverage(session, pre).tier == "modified"
    result_post = determine_cas_coverage(session, post)
    assert result_post.tier == "none"
    # Post-NDAA CAS rows are statute-with-proposed-reg — the caveat must ride along:
    assert any("statute" in c for c in result_post.caveats)


def test_full_coverage_single_award_and_disclosure(session):
    seed_all(session)
    pre_full = _contract(session, award=D(2026, 5, 1), value="60000000.00")
    post_not_full = _contract(session, award=D(2026, 7, 15), value="60000000.00",
                              tina_snap="10000000.00", cas_snap="35000000.00")
    result = determine_cas_coverage(session, pre_full)
    assert result.tier == "full" and result.disclosure_required
    # $60M is full pre-NDAA ($50M) but only modified post-NDAA ($100M full):
    assert determine_cas_coverage(session, post_not_full).tier == "modified"


def test_full_coverage_via_cumulative_prior_year_awards(session):
    seed_all(session)
    # Two CAS-covered 2026 awards totaling $110M...
    _contract(session, award=D(2026, 3, 1), value="60000000.00",
              coverage=CASCoverageType.MODIFIED)
    _contract(session, award=D(2026, 8, 1), value="50000000.00",
              coverage=CASCoverageType.MODIFIED,
              tina_snap="10000000.00", cas_snap="35000000.00")
    # ...push a 2027 $40M award over the $100M full-coverage threshold.
    new_award = _contract(session, award=D(2027, 2, 1), value="40000000.00",
                          tina_snap="10000000.00", cas_snap="35000000.00")
    result = determine_cas_coverage(session, new_award)
    assert result.tier == "full"
    assert any("cumulative" in r for r in result.reasons)


# --- TINA applicability per action -------------------------------------------


def test_tina_threshold_branches_at_ndaa_boundary(session):
    """$5M is ABOVE the $2.5M threshold on 2026-06-15 and BELOW the $10M
    threshold on 2026-07-15 — evaluated on the action's own date."""
    data = seed_all(session)
    vehicle = data.contracts["pre_ndaa"]
    pre = _action(session, vehicle, action_date=D(2026, 6, 15), value="5000000.00")
    post = _action(session, vehicle, action_date=D(2026, 7, 15), value="5000000.00")
    r_pre = determine_tina_applicability(session, pre)
    r_post = determine_tina_applicability(session, post)
    assert r_pre.above_threshold and r_pre.threshold_value == Decimal("2500000.00")
    assert not r_post.above_threshold and r_post.threshold_value == Decimal("10000000.00")
    assert r_post.certification_required is False
    # The $10M era is a class deviation — status caveat present:
    assert any("class_deviation" in c for c in r_post.caveats)


def test_exception_applied_is_recorded_by_name(session):
    data = seed_all(session)
    action = _action(
        session, data.contracts["pre_ndaa"],
        action_date=D(2026, 6, 15), value="5000000.00",
        tina_exception_adequate_price_competition=True,
        tina_exception_commercial_product_service=False,
        tina_exception_prices_set_by_law=False,
        tina_exception_waiver_granted=False,
    )
    result = determine_tina_applicability(session, action)
    assert result.certification_required is False
    assert result.exception_applied == "tina_exception_adequate_price_competition"


def test_unevaluated_exceptions_flag_pending_never_assume(session):
    data = seed_all(session)
    action = _action(session, data.contracts["pre_ndaa"],
                     action_date=D(2026, 6, 15), value="5000000.00")
    result = determine_tina_applicability(session, action)
    assert result.certification_required is None  # pending, not assumed either way
    assert len(result.unevaluated_exceptions) == 4


def test_all_exceptions_false_requires_certification(session):
    data = seed_all(session)
    action = _action(
        session, data.contracts["pre_ndaa"],
        action_date=D(2026, 6, 15), value="5000000.00",
        tina_exception_adequate_price_competition=False,
        tina_exception_commercial_product_service=False,
        tina_exception_prices_set_by_law=False,
        tina_exception_waiver_granted=False,
    )
    result = determine_tina_applicability(session, action)
    assert result.certification_required is True


def test_task_order_never_inherits_vehicle_exception(session):
    """The §8 scope note: the parent IDIQ's award action was competitively
    priced (exception True); a later sole-source-shaped task order with
    unset exceptions must NOT come out exempt."""
    data = seed_all(session)
    vehicle = data.contracts["pre_ndaa"]
    _action(  # the vehicle's own competitive award action
        session, vehicle, action_date=D(2026, 5, 15), value="12000000.00",
        tina_exception_adequate_price_competition=True,
        tina_exception_commercial_product_service=False,
        tina_exception_prices_set_by_law=False,
        tina_exception_waiver_granted=False,
    )
    task_order = _action(session, vehicle, action_date=D(2026, 6, 20),
                         value="4000000.00")
    result = determine_tina_applicability(session, task_order)
    assert result.certification_required is None  # pending ITS OWN evaluation
    assert result.exception_applied is None
    assert len(result.unevaluated_exceptions) == 4


def test_missing_proposed_value_flags(session):
    data = seed_all(session)
    action = _action(session, data.contracts["pre_ndaa"],
                     action_date=D(2026, 6, 15), value=None)
    result = determine_tina_applicability(session, action)
    assert result.certification_required is None
    assert any("no proposed_value" in r for r in result.reasons)
