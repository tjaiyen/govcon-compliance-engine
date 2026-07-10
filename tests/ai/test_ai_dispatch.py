"""Tool dispatch: the AI path calls the REAL services and cannot drift from the
engine — a dispatched tool result equals the direct service call."""

import datetime
from decimal import Decimal

from govcon.ai.dispatch import GroundingLedger, dispatch
from govcon.models import Contract
from govcon.models.enums import AgencyType, CASCoverageType, ContractorSize
from govcon.services.cas_tina import determine_cas_coverage

D = datetime.date


def test_cas_dispatch_equals_direct_service_call(session):
    ledger = GroundingLedger()
    dispatched = dispatch(
        session, "determine_cas_coverage",
        {"award_date": "2026-05-15", "contract_value": "12000000.00",
         "contractor_size": "other_than_small"},
        ledger,
    )
    # the same transient contract, called directly
    direct = determine_cas_coverage(session, Contract(
        award_date=D(2026, 5, 15), contract_value=Decimal("12000000.00"),
        contractor_size=ContractorSize.OTHER_THAN_SMALL, is_nontraditional_dc=False,
        agency_type=AgencyType.DOD, cas_coverage_type=CASCoverageType.NONE,
    ))
    assert dispatched.result["tier"] == direct.tier == "modified"
    assert dispatched.result["reasons"] == direct.reasons
    assert dispatched.result["provenance"] == direct.provenance
    # the ledger absorbed the tier + the threshold value the answer may cite
    assert "modified" in ledger.values


def test_tina_dispatch_tristate_pending(session):
    ledger = GroundingLedger()
    r = dispatch(session, "determine_tina_applicability",
                 {"action_date": "2026-07-15", "proposed_value": "12000000.00"}, ledger)
    # omitted exceptions => pending (None), never an assumed "required"
    assert r.result["certification_required"] is None
    assert r.result["threshold_value"] == "10000000.00"
    assert "10000000.00" in ledger.values


def test_threshold_dispatch_records_citation(session):
    ledger = GroundingLedger()
    r = dispatch(session, "threshold_in_force",
                 {"rule": "TINA_THRESHOLD", "on": "2026-07-15"}, ledger)
    assert r.result["value"] == "10000000.00"
    assert r.result["status"] == "class_deviation"
    assert ledger.citations  # source_citation captured for grounding


def test_unknown_tool_raises():
    import pytest

    from govcon.ai.errors import ToolDispatchError
    with pytest.raises(ToolDispatchError):
        dispatch(None, "nope", {}, GroundingLedger())


def test_malformed_input_is_error_result_not_crash(session):
    ledger = GroundingLedger()
    r = dispatch(session, "determine_cas_coverage",
                 {"award_date": "not-a-date", "contract_value": "x"}, ledger)
    assert r.is_error and "error" in r.result
