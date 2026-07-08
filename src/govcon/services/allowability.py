"""FAR Part 31 five-part allowability evaluation (spec §3) producing the
structured allowability vector (§3a) stamped onto gl_transactions at
capture time.

Because gl_transactions is append-only, the vector CANNOT be added after
insert — evaluation happens at the point of data capture ("compliance at
the edge", §0). Use post_transaction() to evaluate-and-insert in one step;
a correction re-evaluates on the replacement row.

The five parts, in spec order:
1. reasonableness   — not fully automatable; statistical outlier check that
                      flags for human review, never auto-fails
2. allocability     — direct_specific | indirect_shared | necessary_overhead
3. CAS/GAAP accord  — which treatment governs per §4b effective-date rule
                      (v1 records the basis; deep conformance checks are Phase 3)
4. FAR 31.2 limits  — the unallowable Chart-of-Accounts mapping (§4)
5. contract terms   — contract_clause_exceptions overrides (§0.1)
plus threshold_regime_context: the regulatory_thresholds row ids captured
on the parent contract at award (§3a).
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.core.decimal_config import quantize_money
from govcon.models import (
    Contract,
    ContractClauseException,
    GLAccount,
    GLTransaction,
    IndirectPool,
    RegulatoryThreshold,
    UnallowableCostCategory,
)
from govcon.models.enums import CostType, PoolName

#: Reasonableness defaults — configurable engineering defaults, NOT
#: regulatory figures (spec §3 item 1: "a configurable statistical
#: threshold ... for human review").
DEFAULT_STDDEV_N = Decimal("3")
DEFAULT_MIN_HISTORY = 5


def _reasonableness(
    session: Session,
    account_id: int,
    amount: Decimal,
    stddev_n: Decimal,
    min_history: int,
) -> str:
    history = [
        Decimal(v)
        for v in session.execute(
            sa.select(GLTransaction.amount).where(GLTransaction.account_id == account_id)
        ).scalars()
    ]
    if len(history) < min_history:
        # Too little history to compare against — pass, documented default.
        # (Flagging every young account's transactions would drown review.)
        return "pass"
    n = Decimal(len(history))
    mean = sum(history, Decimal(0)) / n
    variance = sum(((x - mean) ** 2 for x in history), Decimal(0)) / n
    std = variance.sqrt()
    if std == 0:
        return "flag_for_review" if amount != mean else "pass"
    return "flag_for_review" if abs(amount - mean) > stddev_n * std else "pass"


def _allocability(session: Session, account: GLAccount) -> str:
    if account.cost_type == CostType.DIRECT:
        return "direct_specific"
    if account.pool_assignment is not None:
        pool = session.get(IndirectPool, account.pool_assignment)
        if pool is not None and pool.pool_name == PoolName.GA:
            return "necessary_overhead"
        return "indirect_shared"
    # Unallowable accounts carry no pool; classify by shape.
    return "indirect_shared"


def governing_treatment(
    session: Session, account: GLAccount, on_date: datetime.date
) -> tuple[str | None, str | None]:
    """§4b: which of cas_treatment/gaap_treatment governs a transaction's
    period. v1 rule: while a rescission final rule is not yet effective, CAS
    governs; from the earliest CAS_4xx_STATUS final-rule effective date
    onward, GAAP governs for dual-tracked accounts. (Per-standard
    granularity is a Phase 3 deepening — this is the effective-date
    mechanism, not the full standards map.)"""
    has_cas, has_gaap = bool(account.cas_treatment), bool(account.gaap_treatment)
    if not has_cas and not has_gaap:
        return None, None
    if has_cas and not has_gaap:
        return "cas", account.cas_treatment
    if has_gaap and not has_cas:
        return "gaap", account.gaap_treatment
    switch_date = session.execute(
        sa.select(sa.func.min(RegulatoryThreshold.effective_date)).where(
            RegulatoryThreshold.rule_name.in_(["CAS_408_STATUS", "CAS_411_STATUS"]),
            RegulatoryThreshold.status == "final_rule",
        )
    ).scalar()
    if switch_date is not None and on_date >= switch_date:
        return "gaap", account.gaap_treatment
    return "cas", account.cas_treatment


def _far_31_2(session: Session, account: GLAccount) -> dict:
    if account.cost_type != CostType.UNALLOWABLE:
        return {"result": "allowable", "far_citation": None}
    citation = None
    if account.far_31_205_citation is not None:
        category = session.get(UnallowableCostCategory, account.far_31_205_citation)
        citation = category.far_citation if category else None
    return {"result": "unallowable", "far_citation": citation}


def _contract_terms(
    session: Session,
    contract_id: int | None,
    far_citation: str | None,
    on_date: datetime.date,
) -> dict:
    if contract_id is None or far_citation is None:
        return {"result": "pass", "exception_id": None}
    exception = session.execute(
        sa.select(ContractClauseException)
        .where(ContractClauseException.contract_id == contract_id)
        .where(ContractClauseException.far_citation_overridden == far_citation)
        .where(ContractClauseException.effective_date <= on_date)
        .order_by(ContractClauseException.effective_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if exception is None:
        return {"result": "pass", "exception_id": None}
    return {"result": "overridden_by", "exception_id": exception.exception_id}


def evaluate_allowability(
    session: Session,
    *,
    account: GLAccount,
    amount: Decimal,
    transaction_date: datetime.date,
    contract: Contract | None = None,
    stddev_n: Decimal = DEFAULT_STDDEV_N,
    min_history: int = DEFAULT_MIN_HISTORY,
) -> dict:
    """Run all five tests and return the §3a vector (a plain dict, stored
    as the JSON column gl_transactions.allowability_vector)."""
    amount = quantize_money(amount)
    far = _far_31_2(session, account)
    basis, treatment = governing_treatment(session, account, transaction_date)
    return {
        "reasonableness_result": _reasonableness(
            session, account.account_id, amount, stddev_n, min_history
        ),
        "allocability_classification": _allocability(session, account),
        "cas_gaap_conformance": {"result": "pass", "basis": basis, "treatment": treatment},
        "far_31_2_result": far,
        "contract_terms_result": _contract_terms(
            session,
            contract.contract_id if contract else None,
            far["far_citation"],
            transaction_date,
        ),
        "threshold_regime_context": {
            "tina_threshold_id": contract.tina_threshold_id if contract else None,
            "cas_trigger_threshold_id": contract.cas_trigger_threshold_id if contract else None,
        },
    }


def post_transaction(session: Session, **fields) -> GLTransaction:
    """Evaluate-and-insert in one step — the Phase 2 write path.

    Accepts GLTransaction fields; computes the allowability vector from the
    account/contract/amount/date before the row is flushed (it can never be
    stamped later — the table is append-only)."""
    account = session.get(GLAccount, fields["account_id"])
    if account is None:
        raise LookupError(f"unknown account_id {fields['account_id']!r}")
    contract = (
        session.get(Contract, fields["contract_id"])
        if fields.get("contract_id") is not None
        else None
    )
    vector = evaluate_allowability(
        session,
        account=account,
        amount=fields["amount"],
        transaction_date=fields["transaction_date"],
        contract=contract,
    )
    txn = GLTransaction(**fields, allowability_vector=vector)
    session.add(txn)
    session.flush()
    return txn
