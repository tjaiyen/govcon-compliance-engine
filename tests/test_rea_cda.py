"""Phase 9a: REA/CDA comparator — computed totals, the ABS-not-net
certification test, derived CO dates, and the §9 comparator content."""

import datetime
from decimal import Decimal

from govcon.models import REACDAAction
from govcon.models.claims import REACDAType
from govcon.services.rea_cda import (
    add_line_item,
    certification_test,
    comparator,
    record_co_receipt,
)
from tests.fixtures.synthetic_data import seed_all

D = datetime.date


def _action(session, data, action_type=REACDAType.REA):
    action = REACDAAction(
        contract_id=data.contracts["pre_ndaa"].contract_id, action_type=action_type
    )
    session.add(action)
    session.flush()
    return action


def test_totals_computed_from_line_items(session):
    data = seed_all(session)
    action = _action(session, data)
    add_line_item(session, action, description="added scope", amount=Decimal("30000.00"))
    add_line_item(session, action, description="deleted scope", amount=Decimal("-12000.00"))
    session.commit()
    assert action.cost_increase_total == Decimal("30000.00")
    assert action.cost_decrease_total == Decimal("-12000.00")


def test_certification_uses_abs_never_net(session):
    """+$300K and −$100K: the NET ($200K) is under the $350K SAT, but the
    ABS sum ($400K) is over — certification required. This is the §9
    common-miscalculation case, proven."""
    data = seed_all(session)
    action = _action(session, data)
    add_line_item(session, action, description="up", amount=Decimal("300000.00"))
    add_line_item(session, action, description="down", amount=Decimal("-100000.00"))
    result = certification_test(session, action, D(2026, 7, 1))
    assert result["net"] == "200000.00"          # under threshold — irrelevant
    assert result["abs_magnitude"] == "400000.00"  # over threshold — decides
    assert result["threshold_value"] == "350000.00"
    assert result["certification_required"] is True
    assert "good faith" in result["certification_statement"]


def test_small_rea_needs_no_certification(session):
    data = seed_all(session)
    action = _action(session, data)
    add_line_item(session, action, description="minor", amount=Decimal("42000.00"))
    result = certification_test(session, action, D(2026, 7, 1))
    assert result["certification_required"] is False
    assert action.certification_required is False


def test_cda_claim_certifies_at_100k(session):
    data = seed_all(session)
    small = _action(session, data, REACDAType.CDA_CLAIM)
    add_line_item(session, small, description="claim", amount=Decimal("90000.00"))
    assert certification_test(session, small, D(2026, 7, 1))["certification_required"] is False
    big = _action(session, data, REACDAType.CDA_CLAIM)
    add_line_item(session, big, description="claim", amount=Decimal("150000.00"))
    result = certification_test(session, big, D(2026, 7, 1))
    assert result["certification_required"] is True
    assert result["threshold_rule"] == "CDA_CLAIM_CERT"


def test_cda_co_receipt_derives_both_dates(session):
    data = seed_all(session)
    claim = _action(session, data, REACDAType.CDA_CLAIM)
    add_line_item(session, claim, description="claim", amount=Decimal("90000.00"))
    record_co_receipt(session, claim, D(2026, 7, 1))
    session.commit()
    assert claim.interest_accrual_start_date == D(2026, 7, 1)   # 41 U.S.C. 7109
    assert claim.co_response_deadline == D(2026, 8, 30)          # +60 days (≤ $100K)
    # A larger claim gets NO computed deadline — the CO sets a firm date:
    big = _action(session, data, REACDAType.CDA_CLAIM)
    add_line_item(session, big, description="claim", amount=Decimal("500000.00"))
    record_co_receipt(session, big, D(2026, 7, 1))
    assert big.co_response_deadline is None
    assert big.interest_accrual_start_date == D(2026, 7, 1)


def test_rea_never_accrues_interest_or_deadline(session):
    data = seed_all(session)
    rea = _action(session, data, REACDAType.REA)
    add_line_item(session, rea, description="rea", amount=Decimal("50000.00"))
    record_co_receipt(session, rea, D(2026, 7, 1))
    assert rea.co_received_date == D(2026, 7, 1)
    assert rea.interest_accrual_start_date is None
    assert rea.co_response_deadline is None


def test_comparator_content(session):
    table = comparator()
    assert "31.205-47" in table["prep_costs"]["cda_claim"]
    assert "recoverable" in table["prep_costs"]["rea"]
    assert "7109" in table["interest_accrual"]["cda_claim"]
    assert table["co_response_deadline"]["rea"] == "none statutory"
