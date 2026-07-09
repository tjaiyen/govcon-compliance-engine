"""Standard costing & variance analysis (spec §14) — the six textbook
formulas, one sign convention, flexible-budget overhead basis.

Convention (stated once, applied everywhere):
    variance_amount = standard_amount − actual_amount; positive = FAVORABLE.
`favorable` is always derived from the sign at write time — record_variance
does not even accept it as a parameter.

"Standard quantity/hours ALLOWED" means allowed FOR THE ACTUAL OUTPUT:
units_completed × standard quantity per unit — never a flat number, and
never actual hours. Both overhead variances use budgeted overhead AT
standard hours allowed (the 2-way method; the flexible budget comes from
overhead_budgets — using actual hours here was the spec's own corrected
error, and a test pins the right basis).

Standard-vs-actual and FAR-allowability are two INDEPENDENT evaluations of
the same data: a favorable variance never flips an unallowable cost, and
vice versa (§14).
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.core.decimal_config import quantize_money
from govcon.core.errors import GovconError
from govcon.models import CostVariance, JCLEntry, OverheadBudget, StandardCost
from govcon.models.standard_costing import StandardCostElement, VarianceType


class VarianceError(GovconError):
    pass


def standard_hours_allowed(units_completed: Decimal, standard: StandardCost) -> Decimal:
    """SHA = actual output × standard quantity per unit (§14)."""
    return Decimal(units_completed) * Decimal(standard.standard_quantity)


def budgeted_overhead_at_sha(budget: OverheadBudget, sha: Decimal) -> Decimal:
    """Flexible budget: fixed + variable rate × standard hours allowed."""
    return quantize_money(
        Decimal(budget.fixed_overhead_budget)
        + Decimal(budget.variable_overhead_rate) * Decimal(sha)
    )


def find_standard(
    session: Session,
    *,
    code: str,
    cost_element: StandardCostElement,
    on_date: datetime.date,
) -> StandardCost:
    """The current standard for an operation/product code (wbs_id for v1) —
    dated like everything else in this schema."""
    row = session.execute(
        sa.select(StandardCost)
        .where(StandardCost.operation_or_product_code == code)
        .where(StandardCost.cost_element == cost_element)
        .where(StandardCost.effective_date <= on_date)
        .where(
            sa.or_(
                StandardCost.superseded_date.is_(None),
                StandardCost.superseded_date > on_date,
            )
        )
        .order_by(StandardCost.effective_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise VarianceError(
            f"no {cost_element.value} standard in force for code {code!r} on "
            f"{on_date.isoformat()} — set the standard, do not invent one"
        )
    return row


def record_variance(
    session: Session,
    *,
    standard: StandardCost,
    period_id: int,
    variance_type: VarianceType,
    standard_amount: Decimal,
    actual_amount: Decimal,
    jcl_entry_id: int | None = None,
) -> CostVariance:
    """The single write path: the convention and the derived `favorable`
    live here and nowhere else."""
    variance_amount = quantize_money(Decimal(standard_amount) - Decimal(actual_amount))
    row = CostVariance(
        standard_cost_id=standard.standard_cost_id,
        period_id=period_id,
        jcl_entry_id=jcl_entry_id,
        variance_type=variance_type,
        standard_amount=quantize_money(standard_amount),
        actual_amount=quantize_money(actual_amount),
        variance_amount=variance_amount,
        favorable=variance_amount > 0,  # ALWAYS derived, never a parameter
    )
    session.add(row)
    session.flush()
    return row


def _entry_inputs(entry: JCLEntry) -> tuple[Decimal, Decimal, Decimal]:
    if entry.quantity is None or entry.quantity == 0:
        raise VarianceError(
            f"jcl entry {entry.entry_id} has no quantity — every variance formula "
            "decomposes a dollar amount into rate × quantity (§14); populate it"
        )
    if entry.units_completed is None:
        raise VarianceError(
            f"jcl entry {entry.entry_id} has no units_completed — usage/efficiency "
            "variances have no output base without it (§14)"
        )
    actual_qty = Decimal(entry.quantity)
    actual_rate = Decimal(entry.amount) / actual_qty
    return actual_qty, actual_rate, Decimal(entry.units_completed)


def labor_variances(
    session: Session, entry: JCLEntry, standard: StandardCost
) -> tuple[CostVariance, CostVariance]:
    """Rate: (SR − AR) × AH.  Efficiency: (SHA − AH) × SR."""
    actual_hours, actual_rate, units = _entry_inputs(entry)
    sha = units * Decimal(standard.standard_quantity)
    sr = Decimal(standard.standard_rate)
    rate = record_variance(
        session,
        standard=standard,
        period_id=entry.period_id,
        jcl_entry_id=entry.entry_id,
        variance_type=VarianceType.LABOR_RATE,
        standard_amount=sr * actual_hours,
        actual_amount=actual_rate * actual_hours,
    )
    efficiency = record_variance(
        session,
        standard=standard,
        period_id=entry.period_id,
        jcl_entry_id=entry.entry_id,
        variance_type=VarianceType.LABOR_EFFICIENCY,
        standard_amount=sr * sha,
        actual_amount=sr * actual_hours,
    )
    return rate, efficiency


def material_variances(
    session: Session, entry: JCLEntry, standard: StandardCost
) -> tuple[CostVariance, CostVariance]:
    """Price: (SP − AP) × AQ.  Usage: (SQA − AQ) × SP."""
    actual_qty, actual_price, units = _entry_inputs(entry)
    sqa = units * Decimal(standard.standard_quantity)
    sp = Decimal(standard.standard_rate)
    price = record_variance(
        session,
        standard=standard,
        period_id=entry.period_id,
        jcl_entry_id=entry.entry_id,
        variance_type=VarianceType.MATERIAL_PRICE,
        standard_amount=sp * actual_qty,
        actual_amount=actual_price * actual_qty,
    )
    usage = record_variance(
        session,
        standard=standard,
        period_id=entry.period_id,
        jcl_entry_id=entry.entry_id,
        variance_type=VarianceType.MATERIAL_USAGE,
        standard_amount=sp * sqa,
        actual_amount=sp * actual_qty,
    )
    return price, usage


def overhead_variances(
    session: Session,
    *,
    standard: StandardCost,
    budget: OverheadBudget,
    period_id: int,
    units_completed: Decimal,
    actual_overhead: Decimal,
) -> tuple[CostVariance, CostVariance]:
    """2-way method (§14). BOTH use budgeted overhead AT STANDARD HOURS
    ALLOWED for actual output — never at actual hours worked.
    Spending: budgeted(SHA) − actual.  Volume: applied (SHA × standard OH
    rate) − budgeted(SHA)."""
    sha = standard_hours_allowed(units_completed, standard)
    budgeted = budgeted_overhead_at_sha(budget, sha)
    applied = quantize_money(sha * Decimal(standard.standard_rate))
    spending = record_variance(
        session,
        standard=standard,
        period_id=period_id,
        variance_type=VarianceType.OVERHEAD_SPENDING,
        standard_amount=budgeted,
        actual_amount=Decimal(actual_overhead),
    )
    volume = record_variance(
        session,
        standard=standard,
        period_id=period_id,
        variance_type=VarianceType.OVERHEAD_VOLUME,
        standard_amount=applied,
        actual_amount=budgeted,
    )
    return spending, volume
