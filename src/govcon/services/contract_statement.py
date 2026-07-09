"""The on-demand complete financial picture of one contract — audit-defense
checklist item #10, composed entirely from existing, already-tested pieces:
CAS determination, per-action TINA results, JCL direct costs, GL totals
with the unallowable split, cumulative claimed-vs-billed, recorded
variances, and the audit-trail footprint.
"""

from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.core.decimal_config import quantize_money
from govcon.models import (
    AuditTrail,
    Contract,
    ContractAction,
    CostVariance,
    GLAccount,
    GLTransaction,
    JCLEntry,
    Voucher,
)
from govcon.models.enums import CostType
from govcon.services.cas_tina import determine_cas_coverage, determine_tina_applicability
from govcon.services.ice_schedules import BANNER


def _gl_totals(session: Session, contract_id: int) -> dict:
    rows = session.execute(
        sa.select(GLAccount.cost_type, GLTransaction.amount)
        .join(GLAccount, GLTransaction.account_id == GLAccount.account_id)
        .where(GLTransaction.contract_id == contract_id)
    ).all()
    totals = {t.value: Decimal("0.00") for t in CostType}
    for cost_type, amount in rows:
        totals[cost_type.value] += Decimal(amount)
    return {k: str(v) for k, v in totals.items()} | {
        "note": "unallowable-coded amounts never enter pool numerators or billing exports (criterion D)"
    }


def contract_statement(session: Session, contract: Contract) -> dict:
    """Everything the engine knows about one contract, from inception to
    present, computed from base tables at call time."""
    cas = determine_cas_coverage(session, contract)
    actions = session.execute(
        sa.select(ContractAction)
        .where(ContractAction.contract_id == contract.contract_id)
        .order_by(ContractAction.action_id)
    ).scalars().all()

    jcl_rows = session.execute(
        sa.select(JCLEntry).where(JCLEntry.contract_id == contract.contract_id)
    ).scalars().all()
    direct_by_element: dict[str, Decimal] = {}
    for entry in jcl_rows:
        key = entry.cost_element.value
        direct_by_element[key] = direct_by_element.get(key, Decimal("0.00")) + Decimal(entry.amount)

    billed = sum(
        (
            Decimal(a)
            for a in session.execute(
                sa.select(Voucher.amount_billed).where(Voucher.contract_id == contract.contract_id)
            ).scalars()
        ),
        Decimal("0.00"),
    )
    variance_count = session.execute(
        sa.select(sa.func.count())
        .select_from(CostVariance)
        .join(JCLEntry, CostVariance.jcl_entry_id == JCLEntry.entry_id)
        .where(JCLEntry.contract_id == contract.contract_id)
    ).scalar_one()
    audit_rows = session.execute(
        sa.select(sa.func.count())
        .select_from(AuditTrail)
        .where(AuditTrail.table_name == "contracts")
        .where(AuditTrail.record_id == str(contract.contract_id))
    ).scalar_one()

    return {
        "banner": BANNER,
        "identity": {
            "contract_id": contract.contract_id,
            "agency_type": contract.agency_type.value,
            "award_date": contract.award_date.isoformat(),
            "contract_value": str(contract.contract_value),
            "contractor_size": contract.contractor_size.value,
            "version": contract.version,
        },
        "threshold_regime_immutable": {
            "tina_threshold_snapshot": str(contract.tina_threshold_snapshot),
            "tina_threshold_id": contract.tina_threshold_id,
            "cas_trigger_threshold_snapshot": str(contract.cas_trigger_threshold_snapshot),
            "cas_trigger_threshold_id": contract.cas_trigger_threshold_id,
        },
        "cas_determination": {
            "tier": cas.tier,
            "requires_review": cas.requires_review,
            "disclosure_required": cas.disclosure_required,
            "reasons": cas.reasons,
            "caveats": cas.caveats,
        },
        "tina_actions": [
            {
                "action_id": a.action_id,
                "action_type": a.action_type.value,
                "action_date": a.action_date.isoformat(),
                "proposed_value": None if a.proposed_value is None else str(a.proposed_value),
                "certification_required": determine_tina_applicability(session, a).certification_required,
            }
            for a in actions
        ],
        "direct_costs_by_element": {k: str(v) for k, v in sorted(direct_by_element.items())},
        "gl_totals_by_cost_type": _gl_totals(session, contract.contract_id),
        "billing": {
            "billed_cumulative": str(quantize_money(billed)),
            "note": "claimed-vs-billed with burdens applied is Schedule I's job",
        },
        "variances_recorded": variance_count,
        "audit_trail_rows_for_contract": audit_rows,
    }
