"""Excess travel costs (FAR 31.205-46, spec §4a): at expense submission,
compare actuals to the applicable GSA per-diem rate and split into an
allowable portion (up to per diem) and an unallowable portion (the excess)
— two separate gl_transactions rows via the superseded_by reversing
pattern, not one row with a note. Real-time, never a nightly batch.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.core.decimal_config import quantize_money
from govcon.models import GLAccount, GLTransaction, GSAPerDiemRate
from govcon.services.allowability import post_transaction


def applicable_per_diem(
    session: Session, location: str, travel_date: datetime.date
) -> GSAPerDiemRate:
    """Rates vary by month and location — look up the row in force, never a
    flat number. Raises LookupError if the reference table has no row."""
    rate = session.execute(
        sa.select(GSAPerDiemRate)
        .where(GSAPerDiemRate.location == location)
        .where(GSAPerDiemRate.effective_start_date <= travel_date)
        .where(GSAPerDiemRate.effective_end_date >= travel_date)
        .limit(1)
    ).scalar_one_or_none()
    if rate is None:
        raise LookupError(
            f"no GSA per-diem rate for {location!r} on {travel_date.isoformat()} "
            "— populate gsa_per_diem_rates; do not invent a rate"
        )
    return rate


def split_travel_expense(
    session: Session,
    original: GLTransaction,
    *,
    location: str,
    nights: int,
    excess_account: GLAccount,
) -> tuple[GLTransaction, GLTransaction, GLTransaction] | None:
    """Split a submitted travel expense against per diem.

    Returns None when the expense is within per diem (no split needed).
    Otherwise posts, in one unit of work, all linked to the original via
    superseded_by: (1) a full reversing entry, (2) the allowable portion on
    the original travel account, (3) the excess on the unallowable account.
    Each new row gets its own allowability vector at capture.
    """
    rate = applicable_per_diem(session, location, original.transaction_date)
    cap = quantize_money((rate.lodging_rate + rate.meals_incidentals_rate) * nights)
    if original.amount <= cap:
        return None

    excess = quantize_money(original.amount - cap)
    common = dict(
        contract_id=original.contract_id,
        person_id=original.person_id,
        transaction_date=original.transaction_date,
        period_id=original.period_id,
        superseded_by=original.transaction_id,
    )
    reversal = post_transaction(
        session,
        account_id=original.account_id,
        amount=-original.amount,
        source_document=f"per-diem split: reversal of txn {original.transaction_id}",
        **common,
    )
    allowable = post_transaction(
        session,
        account_id=original.account_id,
        amount=cap,
        source_document=(
            f"per-diem split of txn {original.transaction_id}: allowable up to "
            f"gsa_per_diem_rates.rate_id={rate.rate_id} x {nights} nights"
        ),
        **common,
    )
    excess_txn = post_transaction(
        session,
        account_id=excess_account.account_id,
        amount=excess,
        source_document=(
            f"per-diem split of txn {original.transaction_id}: excess over per diem "
            f"(FAR 31.205-46)"
        ),
        **common,
    )
    return reversal, allowable, excess_txn
