"""Every write lands in the hash-chained audit trail; the chain verifies;
an out-of-band tamper is detected at the right row."""

import datetime
import json
from decimal import Decimal

import sqlalchemy as sa

from govcon.db.audit import GENESIS_HASH, verify_audit_chain
from govcon.models import AuditTrail, GLTransaction, Person
from govcon.models.enums import PeriodStatus
from tests.fixtures.synthetic_data import seed_all


def _audit_rows(session, table, action):
    return session.execute(
        sa.select(AuditTrail)
        .where(AuditTrail.table_name == table)
        .where(AuditTrail.action == action)
        .order_by(AuditTrail.trail_id)
    ).scalars().all()


def test_inserts_are_audited(session):
    data = seed_all(session)
    session.commit()
    rows = _audit_rows(session, "gl_transactions", "insert")
    assert len(rows) == 3  # direct, indirect, unallowable fixtures
    new_values = json.loads(rows[0].new_values)
    assert new_values["amount"] == "1250.00"  # canonical Decimal text
    assert _audit_rows(session, "contracts", "insert")  # every table captured


def test_update_and_delete_are_audited(session):
    data = seed_all(session)
    session.commit()
    # update (periods is not append-only; reconciliation must pass first —
    # the §11 close-gate trigger from migration 0006 enforces it)
    from govcon.models.enums import ReconciliationStatus

    data.period_open.reconciliation_status = ReconciliationStatus.PASSED
    data.period_open.status = PeriodStatus.CLOSED
    session.commit()
    updates = _audit_rows(session, "periods", "update")
    assert len(updates) == 1
    old = json.loads(updates[0].old_values)
    new = json.loads(updates[0].new_values)
    assert old["status"] == "open" and new["status"] == "closed"
    # delete (persons is not append-only; exec_person is referenced by no
    # transaction, so the FK pragma allows the delete)
    session.delete(data.exec_person)
    session.commit()
    deletes = _audit_rows(session, "persons", "delete")
    assert len(deletes) == 1 and deletes[0].new_values is None


def test_genesis_row_uses_constant(session):
    seed_all(session)
    session.commit()
    first = session.execute(
        sa.select(AuditTrail).order_by(AuditTrail.trail_id).limit(1)
    ).scalar_one()
    assert first.previous_entry_hash == GENESIS_HASH


def test_chain_verifies_and_detects_tamper(session, engine):
    data = seed_all(session)
    session.commit()
    ok, bad = verify_audit_chain(session)
    assert ok and bad is None
    # Simulate an out-of-band edit: drop the protective triggers, then alter
    # a mid-chain row directly (exactly what the hash chain exists to catch).
    victim = session.execute(
        sa.select(AuditTrail.trail_id).order_by(AuditTrail.trail_id).offset(2).limit(1)
    ).scalar_one()
    with engine.connect() as conn:
        conn.execute(sa.text("DROP TRIGGER trg_audit_trail_no_update"))
        conn.execute(
            sa.text("UPDATE audit_trail SET new_values = '{\"amount\":\"9999.99\"}' WHERE trail_id = :t"),
            {"t": victim},
        )
        conn.commit()
    session.expire_all()
    ok, bad = verify_audit_chain(session)
    assert not ok
    assert bad == victim


def test_chain_recomputable_from_stored_text_alone(session):
    """The stored timestamp/values are the exact hashed content — a fresh
    session recomputes the identical chain."""
    seed_all(session)
    session.commit()
    ok, _ = verify_audit_chain(session)
    assert ok
