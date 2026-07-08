"""Executive-compensation cap monitoring (FAR 31.205-6(p), spec §4a):
escalating signals across the fiscal year — 80% informational, 90% warning,
excess over 100% auto-reclassified to unallowable. A running calculation,
not a point-in-time check.

The statutory cap itself is a regulatory_thresholds row (EXEC_COMP_CAP).
Its value is NOT in the verified regulatory reference, so it is NOT seeded
— threshold_in_force() raises LookupError until a verified value is added
(flag the open question; never invent a number). Tests exercise the
tracker against a clearly-synthetic fixture cap row.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.core.decimal_config import quantize_money
from govcon.models import GLAccount, GLTransaction, Period, Person
from govcon.services.allowability import post_transaction
from govcon.services.thresholds import threshold_in_force

#: Escalation points per §4a — 80/90/100 are the spec's stated design, the
#: cap VALUE comes from regulatory_thresholds.
LEVELS = (
    (Decimal("1.00"), "exceeded"),
    (Decimal("0.90"), "warning"),
    (Decimal("0.80"), "informational"),
)


@dataclass
class ExecCompStatus:
    person_id: int
    fiscal_year: int
    ytd_compensation: Decimal
    cap: Decimal
    pct_of_cap: Decimal
    alert_level: str  # ok | informational | warning | exceeded
    excess: Decimal  # amount over cap (0.00 when under)


def ytd_compensation(session: Session, person: Person, fiscal_year: int) -> Decimal:
    amounts = session.execute(
        sa.select(GLTransaction.amount)
        .join(Period, GLTransaction.period_id == Period.period_id)
        .where(GLTransaction.person_id == person.person_id)
        .where(Period.fiscal_year == fiscal_year)
    ).scalars()
    return sum((Decimal(a) for a in amounts), Decimal("0.00"))


def exec_comp_status(
    session: Session, person: Person, fiscal_year: int, as_of: datetime.date
) -> ExecCompStatus:
    cap_row = threshold_in_force(session, "EXEC_COMP_CAP", as_of)  # raises if unseeded
    cap = cap_row.value
    ytd = ytd_compensation(session, person, fiscal_year)
    pct = (ytd / cap) if cap else Decimal(0)
    level = "ok"
    for cutoff, name in LEVELS:
        if pct >= cutoff:
            level = name
            break
    return ExecCompStatus(
        person_id=person.person_id,
        fiscal_year=fiscal_year,
        ytd_compensation=ytd,
        cap=cap,
        pct_of_cap=pct,
        alert_level=level,
        excess=quantize_money(max(ytd - cap, Decimal("0.00"))),
    )


def reclassify_excess(
    session: Session,
    person: Person,
    fiscal_year: int,
    *,
    as_of: datetime.date,
    period_id: int,
    comp_account: GLAccount,
    unallowable_account: GLAccount,
) -> tuple[GLTransaction, GLTransaction] | None:
    """Auto-reclassify the over-cap excess to unallowable (§4a): a negative
    row on the compensation account and a positive row on the unallowable
    account, both carrying the person for the audit trail. Returns None
    when there is nothing (left) to reclassify.

    Idempotent: the reclass pair nets to zero in the person's YTD (total
    comp actually paid doesn't change — only its classification does), so
    this nets out what prior runs already moved to the unallowable account
    and posts only the remaining delta."""
    status = exec_comp_status(session, person, fiscal_year, as_of)
    already = sum(
        (
            Decimal(a)
            for a in session.execute(
                sa.select(GLTransaction.amount)
                .join(Period, GLTransaction.period_id == Period.period_id)
                .where(GLTransaction.person_id == person.person_id)
                .where(GLTransaction.account_id == unallowable_account.account_id)
                .where(Period.fiscal_year == fiscal_year)
            ).scalars()
        ),
        Decimal("0.00"),
    )
    remaining = quantize_money(status.excess - already)
    if remaining <= 0:
        return None
    status.excess = remaining
    note = (
        f"FAR 31.205-6(p) auto-reclass: FY{fiscal_year} YTD "
        f"{status.ytd_compensation} exceeds cap {status.cap}"
    )
    out = post_transaction(
        session,
        account_id=comp_account.account_id,
        person_id=person.person_id,
        amount=-status.excess,
        transaction_date=as_of,
        period_id=period_id,
        source_document=note,
    )
    into = post_transaction(
        session,
        account_id=unallowable_account.account_id,
        person_id=person.person_id,
        amount=status.excess,
        transaction_date=as_of,
        period_id=period_id,
        source_document=note,
    )
    return out, into
