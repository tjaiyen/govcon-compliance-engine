"""ICS / ICE schedule generation (spec §6) — Schedules A, B, C, E, G, H, I,
L, N, O as STRUCTURED DATA with real reconciliation logic in G/I/L (per the
research review, that's where real ICS audit findings occur — the point is
the checks, not the report layout; formats are the Phase 10 exporter's job).

Hard preconditions (spec §11 item 3): every period of the fiscal year is
closed. Schedules D, F, J, K, M are acknowledged out-of-v1-scope gaps
(§6) — this module deliberately does not build them.

Every generated schedule carries the SYNTHETIC-DATA banner in its content
(handoff spec §4).
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.core.decimal_config import quantize_money
from govcon.core.errors import ScheduleGenerationError, SignerLevelError
from govcon.models import (
    Contract,
    GLAccount,
    GLTransaction,
    ICESchedule,
    IndirectPool,
    JCLEntry,
    PayrollRegister,
    Period,
    Voucher,
)
from govcon.models.billing import ScheduleType, SignerRole
from govcon.models.enums import (
    CostElement,
    CostType,
    PoolName,
    PoolStatus,
    RateType,
    ReconciliationStatus,
)
from govcon.services.period_close import fiscal_year_fully_closed

BANNER = "SYNTHETIC DATA — NOT FOR REGULATORY RELIANCE"

#: Schedule L de-minimis defaults — configurable engineering starting points
#: per the roadmap, explicitly NOT regulatory figures.
L_ABS_TOLERANCE = Decimal("100.00")
L_PCT_TOLERANCE = Decimal("0.001")  # 0.1%

OUT_OF_SCOPE_SCHEDULES = ("D", "F", "J", "K", "M")  # acknowledged v1 gaps (§6)


def _fy_periods(session: Session, fiscal_year: int) -> list[Period]:
    return list(
        session.execute(
            sa.select(Period).where(Period.fiscal_year == fiscal_year).order_by(Period.period_number)
        ).scalars()
    )


def _final_rates(session: Session, fiscal_year: int) -> dict[str, IndirectPool]:
    """Claimed rates = the locked/approved actual_final rows for the year."""
    pools = session.execute(
        sa.select(IndirectPool)
        .where(IndirectPool.fiscal_year == fiscal_year)
        .where(IndirectPool.rate_type == RateType.ACTUAL_FINAL)
        .where(IndirectPool.status.in_([PoolStatus.APPROVED, PoolStatus.LOCKED]))
    ).scalars()
    return {p.pool_name.value: p for p in pools}


def _pool_detail(session: Session, fiscal_year: int, pool_name: PoolName) -> dict:
    """Schedules B/C: pool expense detail + the unallowable exclusions that
    were kept OUT of the numerator (criterion D made visible)."""
    period_ids = [p.period_id for p in _fy_periods(session, fiscal_year)]
    rows = session.execute(
        sa.select(GLTransaction, GLAccount)
        .join(GLAccount, GLTransaction.account_id == GLAccount.account_id)
        .join(IndirectPool, GLAccount.pool_assignment == IndirectPool.pool_id)
        .where(IndirectPool.pool_name == pool_name)
        .where(GLTransaction.period_id.in_(period_ids))
    ).all()
    detail = [
        dict(transaction_id=t.transaction_id, account_code=a.account_code, amount=str(t.amount))
        for t, a in rows
    ]
    excluded = session.execute(
        sa.select(GLTransaction, GLAccount)
        .join(GLAccount, GLTransaction.account_id == GLAccount.account_id)
        .where(GLAccount.cost_type == CostType.UNALLOWABLE)
        .where(GLTransaction.period_id.in_(period_ids))
    ).all()
    return dict(
        pool=pool_name.value,
        transactions=detail,
        total=str(sum((Decimal(d["amount"]) for d in detail), Decimal("0.00"))),
        unallowable_exclusions=[
            dict(transaction_id=t.transaction_id, account_code=a.account_code, amount=str(t.amount))
            for t, a in excluded
        ],
    )


def _direct_costs_by_contract(session: Session, fiscal_year: int) -> dict[int, dict[str, Decimal]]:
    period_ids = [p.period_id for p in _fy_periods(session, fiscal_year)]
    rows = session.execute(
        sa.select(JCLEntry).where(JCLEntry.period_id.in_(period_ids))
    ).scalars()
    out: dict[int, dict[str, Decimal]] = {}
    for entry in rows:
        per = out.setdefault(entry.contract_id, {})
        key = entry.cost_element.value
        per[key] = per.get(key, Decimal("0.00")) + Decimal(entry.amount)
    return out


def _burden(direct_labor: Decimal, rates: dict[str, IndirectPool]) -> Decimal:
    """Applied indirect on direct labor at the claimed final rates (v1
    burden basis; §5 fully-burdened chain)."""
    burdened = Decimal(direct_labor)
    for name in ("fringe", "overhead", "ga"):
        pool = rates.get(name)
        if pool is not None and pool.calculated_rate is not None:
            burdened *= 1 + pool.calculated_rate
    return quantize_money(burdened)


def _schedule_A(session, fiscal_year):
    rates = _final_rates(session, fiscal_year)
    return dict(
        claimed_rates={
            name: dict(pool_id=p.pool_id, rate=str(p.calculated_rate), status=p.status.value)
            for name, p in rates.items()
        }
    ), ReconciliationStatus.PASSED


def _schedule_B(session, fiscal_year):
    return _pool_detail(session, fiscal_year, PoolName.GA), ReconciliationStatus.PASSED


def _schedule_C(session, fiscal_year):
    return _pool_detail(session, fiscal_year, PoolName.OVERHEAD), ReconciliationStatus.PASSED


def _schedule_E(session, fiscal_year):
    pools = session.execute(
        sa.select(IndirectPool).where(IndirectPool.fiscal_year == fiscal_year)
    ).scalars()
    return dict(
        claimed_allocation_bases=[
            dict(pool_id=p.pool_id, pool=p.pool_name.value, rate_type=p.rate_type.value,
                 base=None if p.allocation_base_amount is None else str(p.allocation_base_amount))
            for p in pools
        ]
    ), ReconciliationStatus.PASSED


def _schedule_G(session, fiscal_year):
    """GL-to-JCL reconciliation with real variance detection — the
    high-value check. Passed only when every period's per-contract direct
    balances tie."""
    from govcon.services.period_close import three_way_reconciliation

    lines, passed = [], True
    for period in _fy_periods(session, fiscal_year):
        result = three_way_reconciliation(session, period)
        lines.append(
            dict(period_id=period.period_id, period=f"{fiscal_year}-{period.period_number:02d}",
                 passed=result.passed, gl_jcl_variances=result.gl_jcl_variances)
        )
        passed = passed and result.passed
    return dict(reconciliation_by_period=lines), (
        ReconciliationStatus.PASSED if passed else ReconciliationStatus.FAILED
    )


def _schedule_H(session, fiscal_year):
    """Direct costs by contract at claimed rates, per cost element."""
    rates = _final_rates(session, fiscal_year)
    by_contract = _direct_costs_by_contract(session, fiscal_year)
    lines = []
    for contract_id, elements in sorted(by_contract.items()):
        labor = elements.get(CostElement.LABOR.value, Decimal("0.00"))
        direct_total = sum(elements.values(), Decimal("0.00"))
        burdened_labor = _burden(labor, rates)
        lines.append(
            dict(contract_id=contract_id,
                 elements={k: str(v) for k, v in sorted(elements.items())},
                 direct_total=str(direct_total),
                 burdened_labor_at_claimed_rates=str(burdened_labor),
                 claimed_total=str(quantize_money(direct_total - labor + burdened_labor)))
        )
    return dict(contracts=lines), ReconciliationStatus.PASSED


def _schedule_I(session, fiscal_year):
    """Cumulative claimed vs billed per contract since inception, computed
    from the base tables at generation time (spec §6: never cached totals).
    Over/under-billing = claimed − billed (positive = under-billed)."""
    rates = _final_rates(session, fiscal_year)
    claimed_rows = session.execute(sa.select(JCLEntry)).scalars()
    claimed: dict[int, dict[str, Decimal]] = {}
    for entry in claimed_rows:
        per = claimed.setdefault(entry.contract_id, {"labor": Decimal("0.00"), "other": Decimal("0.00")})
        key = "labor" if entry.cost_element == CostElement.LABOR else "other"
        per[key] += Decimal(entry.amount)
    billed_rows = session.execute(
        sa.select(Voucher.contract_id, Voucher.amount_billed)
    ).all()
    billed: dict[int, Decimal] = {}
    for contract_id, amount in billed_rows:
        billed[contract_id] = billed.get(contract_id, Decimal("0.00")) + Decimal(amount)

    lines, passed = [], True
    for contract_id in sorted(set(claimed) | set(billed)):
        parts = claimed.get(contract_id, {"labor": Decimal("0.00"), "other": Decimal("0.00")})
        claimed_total = quantize_money(_burden(parts["labor"], rates) + parts["other"])
        billed_total = billed.get(contract_id, Decimal("0.00"))
        over_under = quantize_money(claimed_total - billed_total)
        if billed_total > claimed_total:
            passed = False  # over-billed beyond the claim — a real finding
        lines.append(
            dict(contract_id=contract_id, claimed_cumulative=str(claimed_total),
                 billed_cumulative=str(billed_total), over_under_billing=str(over_under))
        )
    return dict(contracts=lines), (
        ReconciliationStatus.PASSED if passed else ReconciliationStatus.FAILED
    )


def _schedule_L(session, fiscal_year):
    """Payroll-to-labor-distribution reconciliation per period against
    payroll_registers, with the configurable de-minimis tolerance."""
    lines, passed = [], True
    for period in _fy_periods(session, fiscal_year):
        payroll = session.execute(
            sa.select(PayrollRegister.total_gross_payroll).where(
                PayrollRegister.period_id == period.period_id
            )
        ).scalars()
        payroll_total = sum((Decimal(p) for p in payroll), Decimal("0.00"))
        labor_rows = session.execute(
            sa.select(JCLEntry.amount)
            .where(JCLEntry.period_id == period.period_id)
            .where(JCLEntry.cost_element == CostElement.LABOR)
        ).scalars()
        labor_total = sum((Decimal(a) for a in labor_rows), Decimal("0.00"))
        variance = abs(payroll_total - labor_total)
        tolerance = max(L_ABS_TOLERANCE, quantize_money(payroll_total * L_PCT_TOLERANCE))
        period_ok = variance <= tolerance
        passed = passed and period_ok
        lines.append(
            dict(period=f"{fiscal_year}-{period.period_number:02d}",
                 payroll_total=str(payroll_total), labor_distributed=str(labor_total),
                 variance=str(variance), tolerance=str(tolerance), passed=period_ok)
        )
    return dict(reconciliation_by_period=lines), (
        ReconciliationStatus.PASSED if passed else ReconciliationStatus.FAILED
    )


def _schedule_O(session, fiscal_year):
    fy_end = datetime.date(fiscal_year, 12, 31)
    contracts = session.execute(sa.select(Contract).where(Contract.superseded_by.is_(None))).scalars()
    return dict(
        contracts=[
            dict(contract_id=c.contract_id,
                 performance_end_date=None if c.performance_end_date is None
                 else c.performance_end_date.isoformat(),
                 closeout_status="complete" if c.performance_end_date is not None
                 and c.performance_end_date <= fy_end else "open")
            for c in contracts
        ]
    ), ReconciliationStatus.PASSED


CERTIFICATION_TEXT = (
    "Certificate of Final Indirect Costs (FAR 52.216-7(d)(2)(iii) structure; "
    "SYNTHETIC exercise): I certify to the best of my knowledge and belief "
    "that all costs included in this proposal to establish final indirect "
    "cost rates are allowable in accordance with the cost principles of the "
    "FAR, and that this proposal does not include any costs which are "
    "expressly unallowable under applicable cost principles."
)

BUILDERS = {
    ScheduleType.A: _schedule_A,
    ScheduleType.B: _schedule_B,
    ScheduleType.C: _schedule_C,
    ScheduleType.E: _schedule_E,
    ScheduleType.G: _schedule_G,
    ScheduleType.H: _schedule_H,
    ScheduleType.I: _schedule_I,
    ScheduleType.L: _schedule_L,
    ScheduleType.O: _schedule_O,
}


def generate_schedule(
    session: Session,
    fiscal_year: int,
    schedule_type: ScheduleType,
    *,
    signer_name: str | None = None,
    signer_role: SignerRole | None = None,
) -> ICESchedule:
    """Generate one schedule. Preconditions: the fiscal year is fully
    closed (§11 item 3); Schedule N additionally requires a VP/CFO signer."""
    if not fiscal_year_fully_closed(session, fiscal_year):
        raise ScheduleGenerationError(
            f"FY{fiscal_year} is not fully closed — ICE schedules cannot generate "
            "for an open period (§11 item 3)"
        )
    if schedule_type == ScheduleType.N:
        if signer_role not in (SignerRole.CFO, SignerRole.VP):
            raise SignerLevelError(
                "Schedule N must be signed no lower than VP/CFO level "
                f"(got {signer_role.value if signer_role else 'no signer'}) — reg-ref §5"
            )
        content = dict(certification=CERTIFICATION_TEXT, signer_name=signer_name,
                       signer_role=signer_role.value)
        status = ReconciliationStatus.PASSED
    else:
        content, status = BUILDERS[schedule_type](session, fiscal_year)

    content = dict(banner=BANNER, fiscal_year=fiscal_year,
                   schedule=schedule_type.value, **content)
    row = ICESchedule(
        fiscal_year=fiscal_year,
        schedule_type=schedule_type,
        generated_date=datetime.date.today(),
        reconciliation_status=status,
        content=json.dumps(content, sort_keys=True),
        signer_name=signer_name,
        signer_role=signer_role,
        signed_date=datetime.date.today() if schedule_type == ScheduleType.N else None,
    )
    session.add(row)
    session.flush()
    return row
