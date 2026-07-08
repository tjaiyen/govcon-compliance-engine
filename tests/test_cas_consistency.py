"""Phase 3: disclosed-vs-actual consistency check, practice-change events
(cost-impact flag per 9903.201-6), and CAS standard-status distinctions."""

import datetime

import pytest

from govcon.models import CostAccountingPractice, GLAccount
from govcon.models.enums import CostType
from govcon.models.practices import ChangeEventStatus, DisclosedTreatment
from govcon.services.cas_consistency import (
    MODIFIED_COVERAGE_STANDARDS,
    cas_standard_status,
    check_disclosed_vs_actual,
    current_practices,
    record_practice_change,
    resolve_change_event,
)
from tests.fixtures.synthetic_data import seed_all

D = datetime.date


def _disclose_labor_direct(session) -> CostAccountingPractice:
    practice = CostAccountingPractice(
        practice_area="Labor cost charging",
        disclosed_treatment=DisclosedTreatment.DIRECT,
        account_code_prefix="5",  # governs the 5xxx account family
        description="All 5xxx labor accounts are charged direct to contracts.",
        effective_date=D(2026, 1, 1),
    )
    session.add(practice)
    session.flush()
    return practice


def test_consistent_practice_yields_no_violations(session):
    seed_all(session)  # 5000 Direct Labor is coded direct
    _disclose_labor_direct(session)
    assert check_disclosed_vs_actual(session) == []


def test_consistency_violation_is_flagged_not_autofixed(session):
    """The roadmap's consistency-violation scenario: an account inside a
    disclosed-direct family gets coded indirect — flagged for review, and
    nothing about the account is changed."""
    data = seed_all(session)
    _disclose_labor_direct(session)
    rogue = GLAccount(
        account_code="5900",
        account_name="Labor Overheadish (miscoded)",
        cost_type=CostType.INDIRECT,
        pool_assignment=data.pool.pool_id,
    )
    session.add(rogue)
    session.flush()
    violations = check_disclosed_vs_actual(session)
    assert len(violations) == 1
    v = violations[0]
    assert v.account_code == "5900"
    assert v.disclosed_treatment == "direct"
    assert v.actual_cost_type == "indirect"
    assert "CAS 401/402" in v.describe()
    # Not auto-fixed:
    assert rogue.cost_type == CostType.INDIRECT


def test_unallowable_accounts_are_not_401_violations(session):
    """CAS 405 handling belongs to the allowability layer — an unallowable
    account inside a disclosed family is not a 401/402 violation."""
    seed_all(session)
    practice = CostAccountingPractice(
        practice_area="Misc expense charging",
        disclosed_treatment=DisclosedTreatment.INDIRECT,
        account_code_prefix="79",
        description="79xx misc expense accounts are indirect.",
        effective_date=D(2026, 1, 1),
    )
    session.add(practice)
    session.flush()  # fixture's 7900 Entertainment is UNALLOWABLE
    assert check_disclosed_vs_actual(session) == []


def test_practice_change_creates_version_and_flagged_event(session):
    seed_all(session)
    old = _disclose_labor_direct(session)
    new, event = record_practice_change(
        session,
        old,
        effective_date=D(2026, 9, 1),
        description="5xxx labor accounts charged direct; 59xx supervision reclassified.",
    )
    session.commit()
    assert old.superseded_by == new.practice_id
    assert event.cost_impact_required is True  # 9903.201-6 default
    assert event.status == ChangeEventStatus.FLAGGED
    # The consistency check now evaluates against the NEW current practice only.
    assert [p.practice_id for p in current_practices(session)] == [new.practice_id]


def test_resolving_event_requires_a_reason(session):
    seed_all(session)
    old = _disclose_labor_direct(session)
    _, event = record_practice_change(session, old, effective_date=D(2026, 9, 1))
    with pytest.raises(ValueError, match="recorded reason"):
        resolve_change_event(session, event, notes="   ")
    resolved = resolve_change_event(
        session, event, notes="Cost-impact analysis GD-2026-01: immaterial (<$1K)."
    )
    assert resolved.status == ChangeEventStatus.RESOLVED


def test_cas_407_distinguished_from_408_411(session):
    seed_all(session)
    assert cas_standard_status(session, "CAS_407").status.value == "proposed_rule"
    assert cas_standard_status(session, "CAS_408").status.value == "final_rule"
    assert cas_standard_status(session, "CAS_411").status.value == "final_rule"
    # CAS 401 has no tracked rulemaking — no status is inferred.
    with pytest.raises(LookupError, match="do not infer"):
        cas_standard_status(session, "CAS_401")


def test_modified_coverage_is_exactly_four_standards():
    assert MODIFIED_COVERAGE_STANDARDS == ("CAS 401", "CAS 402", "CAS 405", "CAS 406")
