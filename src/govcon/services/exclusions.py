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
    """Transactions contributing to a pool's cost numerator.

    A pool's cost numerator is a function of its IDENTITY — pool_name +
    fiscal_year — NOT of the specific rate-version row. A stress test found
    that matching on pool_id alone gave the provisional row the whole
    numerator and the actual_final row (same pool/year) a spurious ZERO,
    because GL accounts carry a single pool_assignment FK. Constituent
    accounts are resolved to their pool's (name, year) so provisional and
    actual_final compute the same numerator from the same ledger.

    Unallowable codes structurally cannot carry a pool assignment (CHECK
    constraint); the filter excludes them anyway (belt + suspenders)."""
    # Also filter transactions to the pool's OWN fiscal year (via the
    # period) — a second stress-test finding: without it, a later-year
    # transaction on the same account contaminated the earlier year's
    # claimed rate (FY2027 costs inflating an FY2026 numerator).
    from govcon.models import Period

    sibling = sa.orm.aliased(IndirectPool)
    return list(
        session.execute(
            sa.select(GLTransaction)
            .join(GLAccount, GLTransaction.account_id == GLAccount.account_id)
            .join(sibling, GLAccount.pool_assignment == sibling.pool_id)
            .join(Period, GLTransaction.period_id == Period.period_id)
            .where(sibling.pool_name == pool.pool_name)
            .where(sibling.fiscal_year == pool.fiscal_year)
            .where(Period.fiscal_year == pool.fiscal_year)
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
