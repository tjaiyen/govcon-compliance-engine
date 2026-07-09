"""§11 period-close gating: reconciliation gates the close (ORM + trigger),
closed periods are irreversible, and a fully-closed fiscal year locks its
rates ("cannot regenerate a locked rate")."""

import datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa

from govcon.core.errors import PeriodCloseError, RateCalculationError
from govcon.models import GLTransaction, Voucher
from govcon.models.enums import PeriodStatus, PoolStatus, ReconciliationStatus
from govcon.services.period_close import close_period, three_way_reconciliation
from govcon.services.rates import calculate_pool_rate
from tests.fixtures.synthetic_data import seed_all

D = datetime.date


def test_close_blocked_when_gl_jcl_mismatch(session):
    """Fixture GL direct (1250) ties to JCL (1250); an extra unmatched GL
    direct transaction breaks the tie and blocks the close."""
    data = seed_all(session)
    session.add(
        GLTransaction(
            account_id=data.acct_direct_labor.account_id,
            contract_id=data.contracts["pre_ndaa"].contract_id,
            amount=Decimal("77.00"),
            transaction_date=D(2026, 6, 22),
            period_id=data.period_open.period_id,
        )
    )
    session.flush()
    with pytest.raises(PeriodCloseError, match="GL≠JCL"):
        close_period(session, data.period_open, closed_by="test")
    assert data.period_open.status == PeriodStatus.OPEN
    assert data.period_open.reconciliation_status == ReconciliationStatus.FAILED


def test_close_blocked_when_billing_exceeds_ledger(session):
    data = seed_all(session)
    session.add(
        Voucher(
            contract_id=data.contracts["pre_ndaa"].contract_id,
            period_id=data.period_open.period_id,
            amount_billed=Decimal("99999.00"),  # far beyond the 1250 ledger basis
            billing_date=D(2026, 6, 25),
        )
    )
    session.flush()
    with pytest.raises(PeriodCloseError, match="billed"):
        close_period(session, data.period_open, closed_by="test")


def test_clean_close_succeeds_and_is_gated_workflow(session):
    data = seed_all(session)
    result = close_period(session, data.period_open, closed_by="test")
    session.commit()
    assert result.passed
    assert data.period_open.status == PeriodStatus.CLOSED
    assert data.period_open.reconciliation_status == ReconciliationStatus.PASSED
    assert data.period_open.closed_by == "test"
    with pytest.raises(PeriodCloseError, match="already closed"):
        close_period(session, data.period_open, closed_by="test")


def test_db_trigger_blocks_flag_flip_close(session):
    """§11 item 2 at the DB layer: raw UPDATE cannot close a period whose
    reconciliation hasn't passed."""
    data = seed_all(session)
    session.commit()
    with pytest.raises(sa.exc.IntegrityError, match="reconciliation"):
        session.execute(
            sa.text("UPDATE periods SET status = 'closed' WHERE period_id = :p"),
            {"p": data.period_open.period_id},
        )
    session.rollback()


def test_db_trigger_blocks_reopen(session):
    data = seed_all(session)
    session.commit()
    with pytest.raises(sa.exc.IntegrityError, match="reopen"):
        session.execute(
            sa.text("UPDATE periods SET status = 'open' WHERE period_id = :p"),
            {"p": data.period_closed.period_id},
        )
    session.rollback()


def test_full_fiscal_year_close_locks_rates(session):
    """Fixture FY2026 has one closed and one open period; closing the open
    one completes the year → the approved fringe pool locks, and
    recalculation is refused (§11 item 4)."""
    data = seed_all(session)
    assert data.pool.status == PoolStatus.APPROVED
    close_period(session, data.period_open, closed_by="test")
    session.commit()
    assert data.pool.status == PoolStatus.LOCKED
    with pytest.raises(RateCalculationError, match="LOCKED"):
        calculate_pool_rate(session, data.pool)


def test_reconciliation_result_reports_variances(session):
    data = seed_all(session)
    result = three_way_reconciliation(session, data.period_open)
    assert result.passed
    assert result.describe() == "reconciliation passed"
