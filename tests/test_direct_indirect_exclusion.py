"""SF 1408 criterion A/B/C at the edge: direct/indirect segregation is a
schema constraint, not a report-time check."""

import datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa

from govcon.core.errors import DirectCostWithoutContractError
from govcon.models import GLAccount, GLTransaction
from govcon.models.enums import CostType
from tests.fixtures.synthetic_data import seed_all


def test_invalid_cost_type_rejected(session):
    seed_all(session)
    session.commit()
    with pytest.raises(sa.exc.IntegrityError):
        session.execute(
            sa.text(
                "INSERT INTO gl_accounts (account_code, account_name, cost_type) "
                "VALUES ('9999', 'Bogus', 'both_direct_and_indirect')"
            )
        )
    session.rollback()


def test_indirect_account_requires_pool(session):
    seed_all(session)
    session.add(
        GLAccount(account_code="6200", account_name="Orphan Indirect", cost_type=CostType.INDIRECT)
    )
    with pytest.raises(sa.exc.IntegrityError, match="indirect_requires_pool"):
        session.flush()
    session.rollback()


def test_direct_account_cannot_carry_pool(session):
    data = seed_all(session)
    session.add(
        GLAccount(
            account_code="5100",
            account_name="Direct With Pool (invalid)",
            cost_type=CostType.DIRECT,
            pool_assignment=data.pool.pool_id,
        )
    )
    with pytest.raises(sa.exc.IntegrityError, match="pool_only_if_indirect"):
        session.flush()
    session.rollback()


def test_direct_transaction_requires_contract_orm(session):
    data = seed_all(session)
    session.add(
        GLTransaction(
            account_id=data.acct_direct_labor.account_id,
            amount=Decimal("50.00"),
            transaction_date=datetime.date(2026, 6, 21),
            period_id=data.period_open.period_id,
        )
    )
    with pytest.raises(DirectCostWithoutContractError, match="SF 1408"):
        session.flush()
    session.rollback()


def test_direct_transaction_requires_contract_trigger(session):
    data = seed_all(session)
    session.commit()
    with pytest.raises(sa.exc.IntegrityError, match="SF 1408"):
        session.execute(
            sa.text(
                "INSERT INTO gl_transactions "
                "(account_id, amount, transaction_date, period_id) "
                "VALUES (:a, '50.00', '2026-06-21', :p)"
            ),
            {"a": data.acct_direct_labor.account_id, "p": data.period_open.period_id},
        )
    session.rollback()
