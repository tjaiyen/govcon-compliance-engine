"""Period-close control mechanism (spec §11) — the system's temporal
integrity. The close is a gated workflow action:

1. no transaction posts to a closed period (Phase 1 triggers + guard);
2. a period cannot close until the GL/JCL/billing three-way reconciliation
   passes (this module; also a DB trigger backstop from migration 0006);
3. ICE schedules cannot generate for an open fiscal year (ice_schedules
   service checks fiscal_year_fully_closed);
4. rates lock on close — when the LAST period of a fiscal year closes,
   approved pool rows for that year become status=locked, and
   calculate_pool_rate refuses locked pools.

Run the GL-to-JCL tie-out monthly as each period closes (continuous, not
year-end — §11's closing advice), which is exactly what close_period does.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.core.errors import PeriodCloseError
from govcon.models import (
    GLAccount,
    GLTransaction,
    IndirectPool,
    JCLEntry,
    Period,
    Voucher,
)
from govcon.models.enums import CostType, PeriodStatus, PoolStatus, ReconciliationStatus

#: Billing tie-out tolerance — engineering default, not a regulatory figure.
DEFAULT_TOLERANCE = Decimal("0.00")


@dataclass
class ReconciliationResult:
    period_id: int
    passed: bool
    gl_jcl_variances: list[dict] = field(default_factory=list)  # Schedule G logic
    billing_variances: list[dict] = field(default_factory=list)  # criterion F

    def describe(self) -> str:
        problems = [
            f"GL≠JCL for contract {v['contract_id']}: GL {v['gl_total']} vs JCL {v['jcl_total']}"
            for v in self.gl_jcl_variances
        ] + [
            f"billed > direct-cost basis for contract {v['contract_id']}: "
            f"billed {v['billed_total']} vs ledger {v['gl_total']}"
            for v in self.billing_variances
        ]
        return "; ".join(problems) or "reconciliation passed"


def _direct_gl_totals(session: Session, period: Period) -> dict[int, Decimal]:
    rows = session.execute(
        sa.select(GLTransaction.contract_id, GLTransaction.amount)
        .join(GLAccount, GLTransaction.account_id == GLAccount.account_id)
        .where(GLTransaction.period_id == period.period_id)
        .where(GLAccount.cost_type == CostType.DIRECT)
        .where(GLTransaction.contract_id.is_not(None))
    ).all()
    totals: dict[int, Decimal] = {}
    for contract_id, amount in rows:
        totals[contract_id] = totals.get(contract_id, Decimal("0.00")) + Decimal(amount)
    return totals


def _jcl_totals(session: Session, period: Period) -> dict[int, Decimal]:
    rows = session.execute(
        sa.select(JCLEntry.contract_id, JCLEntry.amount).where(
            JCLEntry.period_id == period.period_id
        )
    ).all()
    totals: dict[int, Decimal] = {}
    for contract_id, amount in rows:
        totals[contract_id] = totals.get(contract_id, Decimal("0.00")) + Decimal(amount)
    return totals


def _billed_totals(session: Session, period: Period) -> dict[int, Decimal]:
    rows = session.execute(
        sa.select(Voucher.contract_id, Voucher.amount_billed).where(
            Voucher.period_id == period.period_id
        )
    ).all()
    totals: dict[int, Decimal] = {}
    for contract_id, amount in rows:
        totals[contract_id] = totals.get(contract_id, Decimal("0.00")) + Decimal(amount)
    return totals


def three_way_reconciliation(
    session: Session, period: Period, tolerance: Decimal = DEFAULT_TOLERANCE
) -> ReconciliationResult:
    """GL ↔ JCL ↔ billing for one period (the Schedule G logic, run monthly).

    GL↔JCL: per-contract direct-cost balances must tie exactly (balance-level
    matching per spec §1). Billing: a period's billed amount for a contract
    must not exceed its ledger direct-cost basis (a full claimed-vs-billed
    comparison with indirect applied is Schedule I's annual job — the
    period-level tie-out catches billing with no ledger basis at all).
    """
    gl = _direct_gl_totals(session, period)
    jcl = _jcl_totals(session, period)
    billed = _billed_totals(session, period)
    result = ReconciliationResult(period_id=period.period_id, passed=True)

    for contract_id in sorted(set(gl) | set(jcl)):
        gl_total = gl.get(contract_id, Decimal("0.00"))
        jcl_total = jcl.get(contract_id, Decimal("0.00"))
        if abs(gl_total - jcl_total) > tolerance:
            result.gl_jcl_variances.append(
                dict(contract_id=contract_id, gl_total=str(gl_total), jcl_total=str(jcl_total),
                     variance=str(gl_total - jcl_total))
            )
    for contract_id, billed_total in sorted(billed.items()):
        gl_total = gl.get(contract_id, Decimal("0.00"))
        if billed_total > gl_total + tolerance:
            result.billing_variances.append(
                dict(contract_id=contract_id, billed_total=str(billed_total), gl_total=str(gl_total))
            )
    result.passed = not result.gl_jcl_variances and not result.billing_variances
    return result


def fiscal_year_fully_closed(session: Session, fiscal_year: int) -> bool:
    open_count = session.execute(
        sa.select(sa.func.count())
        .select_from(Period)
        .where(Period.fiscal_year == fiscal_year)
        .where(Period.status != PeriodStatus.CLOSED)
    ).scalar_one()
    any_period = session.execute(
        sa.select(sa.func.count()).select_from(Period).where(Period.fiscal_year == fiscal_year)
    ).scalar_one()
    return any_period > 0 and open_count == 0


def _lock_fiscal_year_rates(session: Session, fiscal_year: int) -> int:
    """§11 item 4: once the fiscal year is fully closed, approved rates for
    it are locked — no retroactive recalculation, ever."""
    pools = session.execute(
        sa.select(IndirectPool)
        .where(IndirectPool.fiscal_year == fiscal_year)
        .where(IndirectPool.status == PoolStatus.APPROVED)
    ).scalars()
    count = 0
    for pool in pools:
        pool.status = PoolStatus.LOCKED
        count += 1
    return count


def close_period(session: Session, period: Period, *, closed_by: str) -> ReconciliationResult:
    """The gated close: reconciliation must pass first; on success the
    period closes, and if that completes the fiscal year, its approved
    rates lock."""
    if period.status == PeriodStatus.CLOSED:
        raise PeriodCloseError(f"period {period.period_id} is already closed")
    result = three_way_reconciliation(session, period)
    if not result.passed:
        period.reconciliation_status = ReconciliationStatus.FAILED
        session.flush()
        raise PeriodCloseError(
            f"period {period.fiscal_year}-{period.period_number:02d} cannot close: "
            + result.describe()
        )
    period.reconciliation_status = ReconciliationStatus.PASSED
    period.status = PeriodStatus.CLOSED
    period.closed_at = datetime.datetime.now(datetime.timezone.utc)
    period.closed_by = closed_by
    session.flush()
    if fiscal_year_fully_closed(session, period.fiscal_year):
        _lock_fiscal_year_rates(session, period.fiscal_year)
        session.flush()
    return result
