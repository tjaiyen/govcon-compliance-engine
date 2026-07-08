"""gl_transactions / audit_trail are append-only — ORM guard + DB trigger,
asserted independently; plus the sanctioned reversing-entry path."""

from decimal import Decimal

import pytest
import sqlalchemy as sa

from govcon.core.errors import AppendOnlyViolation
from tests.fixtures.synthetic_data import seed_all


def test_orm_update_raises(session):
    data = seed_all(session)
    session.commit()
    data.txn_direct.amount = Decimal("999.99")
    with pytest.raises(AppendOnlyViolation, match="append-only"):
        session.flush()
    session.rollback()


def test_orm_delete_raises(session):
    data = seed_all(session)
    session.commit()
    session.delete(data.txn_direct)
    with pytest.raises(AppendOnlyViolation, match="never be deleted"):
        session.flush()
    session.rollback()


def test_raw_sql_update_blocked_by_trigger(session):
    data = seed_all(session)
    session.commit()
    with pytest.raises(sa.exc.IntegrityError, match="append-only"):
        session.execute(
            sa.text("UPDATE gl_transactions SET amount = '1.00' WHERE transaction_id = :t"),
            {"t": data.txn_direct.transaction_id},
        )
    session.rollback()


def test_raw_sql_delete_blocked_by_trigger(session):
    data = seed_all(session)
    session.commit()
    with pytest.raises(sa.exc.IntegrityError, match="append-only"):
        session.execute(
            sa.text("DELETE FROM gl_transactions WHERE transaction_id = :t"),
            {"t": data.txn_direct.transaction_id},
        )
    session.rollback()


def test_audit_trail_is_append_only_too(session):
    data = seed_all(session)
    session.commit()
    with pytest.raises(sa.exc.IntegrityError, match="append-only"):
        session.execute(sa.text("DELETE FROM audit_trail"))
    session.rollback()
    from govcon.models import AuditTrail

    row = session.execute(sa.select(AuditTrail).limit(1)).scalar_one()
    session.delete(row)
    with pytest.raises(AppendOnlyViolation):
        session.flush()
    session.rollback()


def test_sanctioned_correction_path(session):
    from govcon.services.corrections import post_correction

    data = seed_all(session)
    session.commit()
    reversal, replacement = post_correction(
        session, data.txn_direct, amount=Decimal("1300.00")
    )
    session.commit()
    assert reversal.amount == Decimal("-1250.00")
    assert reversal.superseded_by == data.txn_direct.transaction_id
    assert replacement.amount == Decimal("1300.00")
    assert replacement.superseded_by == data.txn_direct.transaction_id
