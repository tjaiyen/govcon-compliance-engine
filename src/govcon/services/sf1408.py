"""SF 1408 structural adequacy self-check (spec §2, roadmap Phase 6).

Built against the SIX verified criteria (reg-ref §4). One uploaded source
claimed 14 criteria — that conflict is documented-unresolved in reg-ref
§10; per the vault's rule, resolve against DCAA's actual current checklist
before extending this suite, never by trusting either secondary count.

Why re-assert what schema constraints already enforce: the CHECKs and
triggers guard the write path, but this suite verifies the DATA AS IT
STANDS — catching anything that arrived out of band (bulk loads, dropped
triggers, PRAGMA-bypassed constraints). A self-check that could never fail
would be worse than none (roadmap Phase 6's own warning), so every check
here has a demonstrated failure mode in tests.

This demonstrates understanding of the criteria; it is NOT a certification
(handoff spec §4) — see explain_limitations().
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.models import (
    GLAccount,
    GLTransaction,
    IndirectPool,
    JCLEntry,
    Period,
    Voucher,
)
from govcon.models.enums import CostType, PeriodStatus, ReconciliationStatus


@dataclass
class CriterionResult:
    criterion: str  # A..F
    name: str
    passed: bool
    findings: list[str] = field(default_factory=list)


def _check_a(session: Session) -> CriterionResult:
    """A — segregation of direct and indirect costs."""
    findings = []
    rows = session.execute(sa.select(GLAccount)).scalars()
    for account in rows:
        if account.cost_type == CostType.INDIRECT and account.pool_assignment is None:
            findings.append(f"indirect account {account.account_code} has no pool assignment")
        if account.cost_type != CostType.INDIRECT and account.pool_assignment is not None:
            findings.append(
                f"account {account.account_code} is {account.cost_type.value} but carries "
                "a pool assignment"
            )
    return CriterionResult("A", "Segregation of direct and indirect costs", not findings, findings)


def _check_b(session: Session) -> CriterionResult:
    """B — accumulation of direct costs by contract/job."""
    findings = []
    orphans = session.execute(
        sa.select(GLTransaction.transaction_id)
        .join(GLAccount, GLTransaction.account_id == GLAccount.account_id)
        .where(GLAccount.cost_type == CostType.DIRECT)
        .where(GLTransaction.contract_id.is_(None))
    ).scalars().all()
    findings += [f"direct transaction {t} has no contract" for t in orphans]
    contracts_with_direct = set(
        session.execute(
            sa.select(GLTransaction.contract_id)
            .join(GLAccount, GLTransaction.account_id == GLAccount.account_id)
            .where(GLAccount.cost_type == CostType.DIRECT)
            .where(GLTransaction.contract_id.is_not(None))
            .distinct()
        ).scalars()
    )
    contracts_with_jcl = set(
        session.execute(sa.select(JCLEntry.contract_id).distinct()).scalars()
    )
    for contract_id in sorted(contracts_with_direct - contracts_with_jcl):
        findings.append(f"contract {contract_id} has direct GL costs but no JCL accumulation")
    return CriterionResult("B", "Accumulation of direct costs by contract", not findings, findings)


def _check_c(session: Session) -> CriterionResult:
    """C — allocation of indirect costs via logical pools and rates."""
    findings = []
    pools = session.execute(sa.select(IndirectPool)).scalars()
    for pool in pools:
        has_costs = session.execute(
            sa.select(sa.func.count())
            .select_from(GLTransaction)
            .join(GLAccount, GLTransaction.account_id == GLAccount.account_id)
            .where(GLAccount.pool_assignment == pool.pool_id)
        ).scalar_one()
        if has_costs and (pool.allocation_base_amount is None or pool.allocation_base_amount <= 0):
            findings.append(
                f"pool {pool.pool_id} ({pool.pool_name.value} FY{pool.fiscal_year}) has costs "
                "but no defined positive allocation base"
            )
    return CriterionResult("C", "Indirect allocation via logical pools", not findings, findings)


def _check_d(session: Session) -> CriterionResult:
    """D — identification and exclusion of unallowable costs."""
    findings = []
    polluted = session.execute(
        sa.select(GLAccount.account_code)
        .where(GLAccount.cost_type == CostType.UNALLOWABLE)
        .where(GLAccount.pool_assignment.is_not(None))
    ).scalars().all()
    findings += [
        f"unallowable account {code} carries a pool assignment — its costs would enter a "
        "rate numerator" for code in polluted
    ]
    uncited = session.execute(
        sa.select(GLAccount.account_code)
        .where(GLAccount.cost_type == CostType.UNALLOWABLE)
        .where(GLAccount.far_31_205_citation.is_(None))
    ).scalars().all()
    findings += [f"unallowable account {code} has no FAR 31.205 citation" for code in uncited]
    return CriterionResult("D", "Identification/exclusion of unallowable costs", not findings, findings)


def _check_e(session: Session) -> CriterionResult:
    """E — monthly posting and reconciliation (the period-close gate)."""
    findings = []
    bad = session.execute(
        sa.select(Period)
        .where(Period.status == PeriodStatus.CLOSED)
        .where(Period.reconciliation_status != ReconciliationStatus.PASSED)
    ).scalars().all()
    findings += [
        f"period {p.fiscal_year}-{p.period_number:02d} is closed without a passing "
        "reconciliation" for p in bad
    ]
    return CriterionResult("E", "Monthly posting and reconciliation gate", not findings, findings)


def _check_f(session: Session) -> CriterionResult:
    """F — reconciling billings to accounting records."""
    from govcon.services.period_close import three_way_reconciliation

    findings = []
    periods_with_vouchers = session.execute(
        sa.select(Period).join(Voucher, Voucher.period_id == Period.period_id).distinct()
    ).scalars().all()
    for period in periods_with_vouchers:
        result = three_way_reconciliation(session, period)
        for v in result.billing_variances:
            findings.append(
                f"period {period.fiscal_year}-{period.period_number:02d}: contract "
                f"{v['contract_id']} billed {v['billed_total']} against ledger basis "
                f"{v['gl_total']}"
            )
    return CriterionResult("F", "Billing-to-ledger tie-out", not findings, findings)


CHECKS = (_check_a, _check_b, _check_c, _check_d, _check_e, _check_f)


def has_data(session: Session) -> bool:
    """True if there is anything to check — an empty DB passes every
    criterion vacuously, which a stress test flagged as misleading
    ('compliant' when nothing has been audited)."""
    accounts = session.execute(sa.select(sa.func.count()).select_from(GLAccount)).scalar_one()
    txns = session.execute(sa.select(sa.func.count()).select_from(GLTransaction)).scalar_one()
    return bool(accounts or txns)


def run_self_check(session: Session) -> list[CriterionResult]:
    """Run all six criteria against the current database state and return
    pass/fail-with-reasons — never a bare boolean. On an empty database,
    returns a single explicit 'no data' result rather than six vacuous
    passes."""
    if not has_data(session):
        return [
            CriterionResult(
                "—", "No data to verify", False,
                ["0 GL accounts and 0 transactions — an empty database passes every "
                 "criterion vacuously; seed data (or run scripts/demo.py) before "
                 "reading this as 'adequate'"],
            )
        ]
    return [check(session) for check in CHECKS]


LIMITATIONS = """\
SYNTHETIC DATA — NOT FOR REGULATORY RELIANCE

Known limitations this tool states about itself (handoff spec §4, verified
regulatory reference §4):

1. SEGREGATION OF DUTIES: every audited change is attributed to a named
   actor (per request/operation, Phase 4), but that identity is ASSERTED —
   a header, an environment variable, an OS login — not authenticated.
   Attribution is not segregation of duties: without a real identity
   provider in front of a deployment, one person can still claim any role.
2. DESIGN vs. OPERATION: SF 1408 is a DESIGN review. Passing this tool's
   self-check demonstrates the design criteria are understood and modeled;
   it says nothing about adequacy in extended operation (the post-award
   DFARS 252.242-7006 standard tests operation, not design).
3. NOT A CERTIFICATION: no software is "DCAA-compliant out of the box" —
   configuration and actual use drive adequacy. This tool demonstrates
   understanding of the criteria; it is not a certification and cannot
   stand in for DCAA pre-award or post-award system adequacy testing.
4. SYNTHETIC DATA ONLY: nothing here touches real contract, employer, or
   CUI/ITAR data; every figure is a hand-authored test fixture.
5. CRITERIA COUNT: this self-check implements the six verified structural
   criteria. A secondary source claims 14; that conflict is documented,
   unresolved, and must be settled against DCAA's actual current SF 1408
   checklist before extending this suite.
6. REGULATION WATCH IS A SUGGESTER: the Federal Register watcher records
   search results as suggestions for a HUMAN to verify — it never changes
   a threshold or rule itself (parsing regulation is fragile; every change
   lands as a reviewed migration), and a suggestion is not a legal
   conclusion.
7. AI ASSISTANT IS A RENDERING, NOT A DETERMINATION: the conversational /
   tutor / drafting layer translates plain English to and from the engine's
   structured inputs and explains results. It never makes a compliance
   determination — the structured determination (tier, reasons, caveats,
   provenance, citation) is the authoritative, audited fact. AI prose is
   verified to cite only engine-produced values, is withheld when it cannot
   be verified, and is never itself a system-of-record entry. The AI layer
   runs on SYNTHETIC data only (fail-closed).
"""


def explain_limitations() -> str:
    return LIMITATIONS
