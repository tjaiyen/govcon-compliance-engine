"""contracts frozen columns are immutable after insert; supersede_contract()
is the only sanctioned change path."""

import datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa

from govcon.core.errors import ImmutableFieldError
from govcon.models import CONTRACT_FROZEN_COLUMNS
from tests.fixtures.synthetic_data import seed_all

FROZEN_NEW_VALUES = {
    "award_date": datetime.date(2030, 1, 1),
    "tina_threshold_snapshot": Decimal("1.00"),
    "tina_threshold_id": None,  # replaced per-test below
    "cas_trigger_threshold_snapshot": Decimal("1.00"),
    "cas_trigger_threshold_id": None,
}


@pytest.mark.parametrize("column", [c for c in CONTRACT_FROZEN_COLUMNS if not c.endswith("_id")])
def test_orm_blocks_each_frozen_column(session, column):
    data = seed_all(session)
    session.commit()
    setattr(data.contracts["pre_ndaa"], column, FROZEN_NEW_VALUES[column])
    with pytest.raises(ImmutableFieldError, match="immutable"):
        session.flush()
    session.rollback()


def test_orm_blocks_threshold_id_change(session):
    from govcon.models import RegulatoryThreshold

    data = seed_all(session)
    session.commit()
    some_id = session.execute(
        sa.select(RegulatoryThreshold.threshold_id).limit(1)
    ).scalar_one()
    data.contracts["pre_ndaa"].tina_threshold_id = some_id
    with pytest.raises(ImmutableFieldError):
        session.flush()
    session.rollback()


def test_raw_sql_blocked_by_trigger(session):
    data = seed_all(session)
    session.commit()
    with pytest.raises(sa.exc.IntegrityError, match="immutable"):
        session.execute(
            sa.text("UPDATE contracts SET award_date = '2030-01-01' WHERE contract_id = :c"),
            {"c": data.contracts["pre_ndaa"].contract_id},
        )
    session.rollback()


def test_mutable_columns_still_updatable(session):
    data = seed_all(session)
    session.commit()
    data.contracts["pre_ndaa"].performance_end_date = datetime.date(2028, 12, 31)
    session.commit()  # no exception — only the five frozen columns are locked


def test_supersede_contract_versioning(session):
    from govcon.services.versioning import supersede_contract

    data = seed_all(session)
    session.commit()
    old = data.contracts["pre_ndaa"]
    new = supersede_contract(session, old, contract_value=Decimal("13000000.00"))
    session.commit()
    assert new.version == old.version + 1
    assert old.superseded_by == new.contract_id
    assert new.superseded_by is None
    assert new.award_date == old.award_date  # frozen fields carried over
    assert new.contract_value == Decimal("13000000.00")
