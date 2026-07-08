"""The sanctioned correction path for the append-only gl_transactions table.

UPDATE is fully blocked (trigger + guard), so the superseded_by pointer is
written on the NEW rows: the reversing entry carries
superseded_by = original.transaction_id ("this row supersedes that one").
NOTE the deliberate asymmetry with contracts, where the OLD row's
superseded_by points at the new version — legal there because contracts are
not wholesale-frozen, only five columns are.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from govcon.models import GLTransaction


def post_correction(
    session: Session, original: GLTransaction, **replacement_fields
) -> tuple[GLTransaction, GLTransaction]:
    """Correct a posted transaction in one unit of work.

    Inserts (1) a reversing entry (negated amount, superseded_by=original)
    and (2) a replacement row with the corrected fields. Returns
    (reversal, replacement). Caller commits.
    """
    reversal = GLTransaction(
        account_id=original.account_id,
        contract_id=original.contract_id,
        person_id=original.person_id,
        amount=-original.amount,
        transaction_date=original.transaction_date,
        period_id=original.period_id,
        source_document=f"reversal of txn {original.transaction_id}",
        superseded_by=original.transaction_id,
    )
    replacement_defaults = dict(
        account_id=original.account_id,
        contract_id=original.contract_id,
        person_id=original.person_id,
        amount=original.amount,
        transaction_date=original.transaction_date,
        period_id=original.period_id,
        source_document=f"replaces txn {original.transaction_id}",
        superseded_by=original.transaction_id,
    )
    replacement_defaults.update(replacement_fields)
    replacement = GLTransaction(**replacement_defaults)
    session.add_all([reversal, replacement])
    session.flush()
    return reversal, replacement
