"""PBR ongoing monitoring (spec §12) — the during-the-year half the
year-end true-up doesn't cover. Real PBR deficiencies are usually caught
(or missed) DURING the year.

v1 monitoring model: for each currently-approved provisional pool of a
period's fiscal year, compare actual YTD pool costs (criterion-D filtered)
against what the provisional rate assumed for the elapsed portion of the
year (approved base × months elapsed / 12 × rate). A variance beyond the
configurable threshold creates a pbr_fluctuation_notes row — the
"explain it before an auditor asks" log; resolving one requires a named
resolver and a real explanation.

The 5% default is a commonly-cited STARTING POINT, explicitly not a
verified regulatory figure — DCAA practice is case-by-case fluctuation
analysis (§12).
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.core.decimal_config import quantize_money, quantize_rate
from govcon.core.errors import GovconError
from govcon.models import (
    ForwardPricingRateAgreement,
    GLAccount,
    GLTransaction,
    IndirectPool,
    PBRFluctuationNote,
    Period,
)
from govcon.models.enums import CostType, PoolStatus, RateType
from govcon.models.reference import ForwardPricingRateAgreement as FPRA  # noqa: F401

DEFAULT_VARIANCE_THRESHOLD_PCT = Decimal("0.05")  # configurable default, not regulation
MONTHS_PER_YEAR = Decimal("12")


class PBRMonitoringError(GovconError):
    pass


def _ytd_pool_costs(session: Session, pool: IndirectPool, through_period: Period) -> Decimal:
    amounts = session.execute(
        sa.select(GLTransaction.amount)
        .join(GLAccount, GLTransaction.account_id == GLAccount.account_id)
        .join(Period, GLTransaction.period_id == Period.period_id)
        .where(GLAccount.pool_assignment == pool.pool_id)
        .where(GLAccount.cost_type != CostType.UNALLOWABLE)
        .where(Period.fiscal_year == through_period.fiscal_year)
        .where(Period.period_number <= through_period.period_number)
    ).scalars()
    return sum((Decimal(a) for a in amounts), Decimal("0.00"))


def monitor_period(
    session: Session,
    period: Period,
    *,
    threshold_pct: Decimal = DEFAULT_VARIANCE_THRESHOLD_PCT,
) -> list[PBRFluctuationNote]:
    """Run the §12 monthly variance check for one period. Returns the
    fluctuation notes created (empty when everything is inside threshold)."""
    pools = session.execute(
        sa.select(IndirectPool)
        .where(IndirectPool.fiscal_year == period.fiscal_year)
        .where(IndirectPool.rate_type == RateType.PROVISIONAL)
        .where(IndirectPool.status == PoolStatus.APPROVED)
    ).scalars().all()

    notes: list[PBRFluctuationNote] = []
    for pool in pools:
        if pool.calculated_rate is None or pool.allocation_base_amount is None:
            continue  # nothing approved to monitor against
        actual = _ytd_pool_costs(session, pool, period)
        expected = quantize_money(
            pool.calculated_rate
            * pool.allocation_base_amount
            * Decimal(period.period_number)
            / MONTHS_PER_YEAR
        )
        if expected == 0:
            continue
        variance = quantize_money(actual - expected)
        variance_pct = quantize_rate(variance / expected)
        if abs(variance_pct) > threshold_pct:
            note = PBRFluctuationNote(
                pool_id=pool.pool_id,
                period_id=period.period_id,
                variance_amount=variance,
                variance_pct=variance_pct,
                explanation=(
                    f"auto-flagged {period.fiscal_year}-{period.period_number:02d}: "
                    f"YTD actual {actual} vs provisional-rate expectation {expected} "
                    f"({variance_pct} against threshold {threshold_pct}) — investigate "
                    "and resolve with an explanation before an auditor asks"
                ),
            )
            session.add(note)
            notes.append(note)
    session.flush()
    return notes


def resolve_note(
    session: Session, note: PBRFluctuationNote, *, resolved_by: str, explanation: str
) -> PBRFluctuationNote:
    if not explanation or not explanation.strip():
        raise PBRMonitoringError(
            "resolving a fluctuation note requires a real explanation — the running "
            "explanation log is the point (§12)"
        )
    note.explanation = explanation
    note.resolved_by = resolved_by
    note.resolved_date = datetime.date.today()
    session.flush()
    return note


def check_fpra_authorization(session: Session) -> list[str]:
    """§12: 'a rate typed forward_pricing' is not 'an FPRA that authorizes
    it'. Flags forward-pricing pool rows with no FPRA link, or one whose
    agreement is not in NEGOTIATED status — 'which agreement authorized
    this number' must have an answer."""
    findings: list[str] = []
    rows = session.execute(
        sa.select(IndirectPool).where(IndirectPool.rate_type == RateType.FORWARD_PRICING)
    ).scalars()
    for pool in rows:
        label = f"pool {pool.pool_id} ({pool.pool_name.value} FY{pool.fiscal_year})"
        if pool.fpra_id is None:
            findings.append(
                f"{label} is typed forward_pricing but references NO FPRA — no "
                "agreement authorizes this rate"
            )
            continue
        fpra = session.get(ForwardPricingRateAgreement, pool.fpra_id)
        if fpra is None or fpra.status.value != "negotiated":
            status = "missing" if fpra is None else fpra.status.value
            findings.append(
                f"{label} references FPRA {pool.fpra_id} whose status is {status!r} — "
                "only a NEGOTIATED agreement authorizes proposal use"
            )
    return findings
