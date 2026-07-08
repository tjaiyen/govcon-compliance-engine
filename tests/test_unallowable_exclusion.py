"""SF 1408 criterion D: an unallowable-coded transaction never appears in a
G&A pool-numerator calculation or a billing export (roadmap Phase 2 test),
plus the seed drift guard for the FAR 31.205 category table."""

from decimal import Decimal

import sqlalchemy as sa

from govcon.models import UnallowableCostCategory
from govcon.seeds.unallowable_categories import SEED_CATEGORIES
from govcon.services.exclusions import (
    billing_export_transactions,
    pool_numerator_total,
    pool_numerator_transactions,
)
from tests.fixtures.synthetic_data import seed_all


def test_unallowable_never_in_pool_numerator(session):
    data = seed_all(session)
    session.commit()
    txns = pool_numerator_transactions(session, data.pool)
    ids = {t.transaction_id for t in txns}
    assert data.txn_indirect.transaction_id in ids
    assert data.txn_unallowable.transaction_id not in ids
    # And the Decimal total reflects only the allowable indirect cost:
    assert pool_numerator_total(session, data.pool) == Decimal("400.00")


def test_unallowable_never_in_billing_export(session):
    data = seed_all(session)
    session.commit()
    exported = billing_export_transactions(
        session, data.contracts["pre_ndaa"].contract_id
    )
    ids = {t.transaction_id for t in exported}
    assert data.txn_direct.transaction_id in ids
    # The entertainment transaction carries the same contract — and is
    # excluded purely by its unallowable account coding (criterion D).
    assert data.txn_unallowable.transaction_id not in ids


def test_category_seed_matches_constants(session):
    """Drift guard: migration-0003-frozen rows == importable constants."""
    db_rows = session.execute(
        sa.select(UnallowableCostCategory).order_by(UnallowableCostCategory.category_id)
    ).scalars().all()
    assert len(db_rows) == len(SEED_CATEGORIES) == 18
    for db_row, const in zip(db_rows, SEED_CATEGORIES):
        assert db_row.far_citation == const["far_citation"]
        assert db_row.category_name == const["category_name"]
        assert db_row.detection_method.value == const["detection_method"]
        assert db_row.trap_logic_description == const["trap_logic_description"]
