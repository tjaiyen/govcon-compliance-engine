"""§4a travel split: an expense over per diem splits at submission into an
allowable row and an unallowable-coded excess row via the reversing
pattern — two correctly-coded transactions, not one row with a note."""

import datetime
from decimal import Decimal

import pytest

from govcon.models import GLAccount
from govcon.models.enums import CostType
from govcon.services.allowability import post_transaction
from govcon.services.travel import applicable_per_diem, split_travel_expense
from tests.fixtures.synthetic_data import per_diem_rate_seattle, seed_all, seeded_category

D = datetime.date


def _travel_setup(session, data):
    session.add(per_diem_rate_seattle())
    travel_account = GLAccount(
        account_code="5200", account_name="Direct Travel", cost_type=CostType.DIRECT
    )
    excess_account = GLAccount(
        account_code="7910",
        account_name="Excess Travel (Unallowable)",
        cost_type=CostType.UNALLOWABLE,
        far_31_205_citation=seeded_category(session, "31.205-46").category_id,
    )
    session.add_all([travel_account, excess_account])
    session.flush()
    return travel_account, excess_account


def test_over_per_diem_splits_into_two_coded_rows(session):
    data = seed_all(session)
    travel_account, excess_account = _travel_setup(session, data)
    original = post_transaction(
        session,
        account_id=travel_account.account_id,
        contract_id=data.contracts["pre_ndaa"].contract_id,
        amount=Decimal("700.00"),  # 2 nights @ (199+79)=278 → cap 556.00
        transaction_date=D(2026, 6, 12),
        period_id=data.period_open.period_id,
        source_document="EXP-SEA-0612",
    )
    result = split_travel_expense(
        session, original, location="Seattle, WA", nights=2, excess_account=excess_account
    )
    session.commit()
    assert result is not None
    reversal, allowable, excess = result
    assert reversal.amount == Decimal("-700.00")
    assert allowable.amount == Decimal("556.00")
    assert excess.amount == Decimal("144.00")
    # Correct coding: allowable stays on the travel account, excess lands on
    # the unallowable account and its vector cites 31.205-46.
    assert allowable.account_id == travel_account.account_id
    assert excess.account_id == excess_account.account_id
    assert excess.allowability_vector["far_31_2_result"]["far_citation"] == "31.205-46"
    # All three link back to the original submission (reversing pattern).
    assert {reversal.superseded_by, allowable.superseded_by, excess.superseded_by} == {
        original.transaction_id
    }
    # The split nets to the original amount — nothing appears or vanishes.
    assert reversal.amount + allowable.amount + excess.amount == Decimal("0.00")


def test_within_per_diem_is_not_split(session):
    data = seed_all(session)
    travel_account, excess_account = _travel_setup(session, data)
    original = post_transaction(
        session,
        account_id=travel_account.account_id,
        contract_id=data.contracts["pre_ndaa"].contract_id,
        amount=Decimal("500.00"),  # under the 556.00 cap
        transaction_date=D(2026, 6, 12),
        period_id=data.period_open.period_id,
    )
    assert (
        split_travel_expense(
            session, original, location="Seattle, WA", nights=2, excess_account=excess_account
        )
        is None
    )


def test_missing_per_diem_rate_raises_not_invents(session):
    seed_all(session)
    with pytest.raises(LookupError, match="do not invent"):
        applicable_per_diem(session, "Nowhere, ZZ", D(2026, 6, 12))
