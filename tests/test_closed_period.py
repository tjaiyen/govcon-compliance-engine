"""No transaction posts to a closed period (spec §11 item 1) — both ledgers,
both enforcement layers."""

import datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa

from govcon.core.errors import ClosedPeriodError
from govcon.models import GLTransaction, JCLEntry
from govcon.models.enums import CostElement
from tests.fixtures.synthetic_data import seed_all


def test_orm_insert_into_closed_period_raises(session):
    data = seed_all(session)
    session.add(
        GLTransaction(
            account_id=data.acct_fringe.account_id,
            amount=Decimal("10.00"),
            transaction_date=datetime.date(2026, 5, 20),
            period_id=data.period_closed.period_id,
        )
    )
    with pytest.raises(ClosedPeriodError, match="closed period"):
        session.flush()
    session.rollback()


def test_raw_insert_into_closed_period_blocked_by_trigger(session):
    data = seed_all(session)
    session.commit()
    with pytest.raises(sa.exc.IntegrityError, match="closed period"):
        session.execute(
            sa.text(
                "INSERT INTO gl_transactions "
                "(account_id, amount, transaction_date, period_id) "
                "VALUES (:a, '10.00', '2026-05-20', :p)"
            ),
            {"a": data.acct_fringe.account_id, "p": data.period_closed.period_id},
        )
    session.rollback()


def test_jcl_entry_gated_too(session):
    data = seed_all(session)
    session.add(
        JCLEntry(
            contract_id=data.contracts["pre_ndaa"].contract_id,
            clin_id="0002",
            wbs_id="9.9.9",
            cost_element=CostElement.MATERIAL,
            amount=Decimal("55.00"),
            period_id=data.period_closed.period_id,
        )
    )
    with pytest.raises(ClosedPeriodError):
        session.flush()
    session.rollback()


def test_open_period_posting_succeeds(session):
    data = seed_all(session)
    session.add(
        GLTransaction(
            account_id=data.acct_fringe.account_id,
            amount=Decimal("10.00"),
            transaction_date=datetime.date(2026, 6, 20),
            period_id=data.period_open.period_id,
        )
    )
    session.commit()  # no exception
