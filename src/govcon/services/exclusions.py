"""SF 1408 criterion D (spec §2): unallowable-coded transactions are
automatically excluded from indirect-pool NUMERATORS and from billing
exports — as query-level filters, so every consumer inherits the exclusion.

NOTE (flagged for Phase 4, not silently resolved here): CAS 405 treats
pool numerators and allocation BASES differently — unallowable costs come
out of the claimed pool/numerator but can remain in an allocation base
(the reg-ref §5 Schedule E deficiency is unallowables NOT carried through
into the G&A base). These helpers implement the numerator/billing side;
the base-side nuance belongs to the Phase 4 rate engine.

Aggregation is Python-side over Decimal by design — SQL SUM() on SQLite
TEXT-decimals would round-trip through float (see db/types.py).
"""

from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.models import GLAccount, GLTransaction, IndirectPool
from govcon.models.enums import CostType


def _not_unallowable():
    return GLAccount.cost_type != CostType.UNALLOWABLE


def pool_numerator_transactions(session: Session, pool: IndirectPool) -> list[GLTransaction]:
    """Transactions contributing to a pool's cost numerator — unallowable
    codes structurally cannot carry a pool assignment (CHECK constraint),
    and this filter excludes them explicitly anyway (belt + suspenders)."""
    return list(
        session.execute(
            sa.select(GLTransaction)
            .join(GLAccount, GLTransaction.account_id == GLAccount.account_id)
            .where(GLAccount.pool_assignment == pool.pool_id)
            .where(_not_unallowable())
        ).scalars()
    )


def pool_numerator_total(session: Session, pool: IndirectPool) -> Decimal:
    return sum(
        (t.amount for t in pool_numerator_transactions(session, pool)), Decimal("0.00")
    )


def billing_export_transactions(session: Session, contract_id: int) -> list[GLTransaction]:
    """Transactions eligible for a billing export for one contract —
    unallowable-coded rows never appear (criterion D)."""
    return list(
        session.execute(
            sa.select(GLTransaction)
            .join(GLAccount, GLTransaction.account_id == GLAccount.account_id)
            .where(GLTransaction.contract_id == contract_id)
            .where(_not_unallowable())
        ).scalars()
    )
