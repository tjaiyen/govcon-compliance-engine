"""Ground rule 4a boundary tests: penny-exact storage through SQLite,
float rejection, and Python-side aggregation exactness."""

import datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa

from tests.fixtures.synthetic_data import seed_all


def test_money_roundtrips_exactly(session):
    data = seed_all(session)
    session.commit()
    stored = session.execute(
        sa.select(sa.text("amount")).select_from(sa.text("gl_transactions")).where(
            sa.text("transaction_id = :tid")
        ),
        {"tid": data.txn_direct.transaction_id},
    ).scalar()
    # Raw column value is canonical TEXT, no float artifacts.
    assert stored == "1250.00"
    session.expire_all()
    reloaded = session.get(type(data.txn_direct), data.txn_direct.transaction_id)
    assert reloaded.amount == Decimal("1250.00")
    assert isinstance(reloaded.amount, Decimal)


def test_float_is_rejected_at_bind_time(session):
    from govcon.models import Period, GLTransaction
    from tests.fixtures.synthetic_data import seed_all

    data = seed_all(session)
    txn = GLTransaction(
        account_id=data.acct_fringe.account_id,
        amount=0.1 + 0.2,  # a float — forbidden
        transaction_date=datetime.date(2026, 6, 20),
        period_id=data.period_open.period_id,
    )
    session.add(txn)
    with pytest.raises(sa.exc.StatementError, match="float is forbidden"):
        session.flush()


def test_penny_sum_is_exact_in_python(session):
    """10,000 pennies sum to exactly $100.00 when aggregated over Decimal.
    (SQL SUM() on SQLite TEXT would go through float — that's why financial
    aggregation is Python-side by design.)"""
    total = sum([Decimal("0.01")] * 10_000, Decimal("0"))
    assert total == Decimal("100.00")


def test_quantization_helpers():
    from govcon.core.decimal_config import quantize_money, quantize_rate

    assert quantize_money(Decimal("1.005")) == Decimal("1.01")  # HALF_UP
    assert quantize_rate(Decimal("0.12345")) == Decimal("0.1235")
    assert quantize_money(Decimal("-2.675")) == Decimal("-2.68")
    assert quantize_money(Decimal("0")) == Decimal("0.00")
    assert quantize_money(Decimal("123456789012.999")) == Decimal("123456789013.00")
