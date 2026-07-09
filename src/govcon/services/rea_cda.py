"""REA vs. CDA Claim comparator (spec §9).

One table, one discriminator — REAs and CDA claims share their shape and
differ in certification and interest rules. Totals are computed from line
items; the DFARS 252.243-7002 test sums the ABSOLUTE VALUES of increases
and decreases (never the net — a common real-world miscalculation, and a
test here proves the net would get it wrong). Derived dates
(co_response_deadline, interest_accrual_start_date) compute from
co_received_date, never entered independently.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.core.decimal_config import quantize_money
from govcon.core.errors import GovconError
from govcon.models import REACDAAction, REACDALineItem
from govcon.models.claims import REACDAType
from govcon.models.tina import CertificationStatus
from govcon.services.thresholds import threshold_in_force

CDA_DECISION_WINDOW_DAYS = 60  # 41 U.S.C. 7103(f) per reg-ref §7

REA_CERTIFICATION_TEXT = (
    "I certify that the request is made in good faith, and that the "
    "supporting data are accurate and complete to the best of my knowledge "
    "and belief."  # DFARS 252.243-7002, quoted in spec §9
)


class REACDAError(GovconError):
    pass


#: §9 comparator — the structural differences, as data.
COMPARATOR = {
    "legal_posture": {"rea": "collaborative negotiation", "cda_claim": "formal dispute, may litigate"},
    "prep_costs": {
        "rea": "generally recoverable",
        "cda_claim": "generally unrecoverable (FAR 31.205-47 claim-prosecution costs)",
    },
    "interest_accrual": {
        "rea": "none during negotiation",
        "cda_claim": "accrues from CO receipt of certified claim (41 U.S.C. 7109)",
    },
    "co_response_deadline": {
        "rea": "none statutory",
        "cda_claim": "60 days for claims at/below the certification threshold; firm date for larger",
    },
    "certification_trigger": {
        "rea": "DFARS 252.243-7002, at the Simplified Acquisition Threshold",
        "cda_claim": "FAR 33.207, at the CDA claim certification threshold",
    },
}


def comparator() -> dict:
    return COMPARATOR


def add_line_item(
    session: Session,
    action: REACDAAction,
    *,
    description: str,
    amount: Decimal,
    gl_transaction_id: int | None = None,
) -> REACDALineItem:
    item = REACDALineItem(
        action_id=action.action_id,
        description=description,
        amount=Decimal(amount),
        gl_transaction_id=gl_transaction_id,
    )
    session.add(item)
    session.flush()
    recompute_totals(session, action)
    return item


def recompute_totals(session: Session, action: REACDAAction) -> None:
    """Totals are sums over line items — never entered as bare aggregates."""
    amounts = [
        Decimal(a)
        for a in session.execute(
            sa.select(REACDALineItem.amount).where(REACDALineItem.action_id == action.action_id)
        ).scalars()
    ]
    action.cost_increase_total = quantize_money(
        sum((a for a in amounts if a > 0), Decimal("0.00"))
    )
    action.cost_decrease_total = quantize_money(
        sum((a for a in amounts if a < 0), Decimal("0.00"))
    )
    session.flush()


def certification_test(
    session: Session, action: REACDAAction, on_date: datetime.date
) -> dict:
    """The threshold test: ABS(increases) + ABS(decreases), NEVER the net.

    REA (DoD): DFARS 252.243-7002 at the SAT in force on the date.
    CDA claim: FAR 33.207 at the CDA_CLAIM_CERT threshold.
    Sets certification_required and returns the reasoning + statement.
    """
    magnitude = quantize_money(
        abs(action.cost_increase_total) + abs(action.cost_decrease_total)
    )
    rule = "SAT" if action.action_type == REACDAType.REA else "CDA_CLAIM_CERT"
    row = threshold_in_force(session, rule, on_date)
    required = magnitude > row.value
    action.certification_required = required
    if not required:
        action.certification_status = CertificationStatus.NOT_REQUIRED
    session.flush()
    return dict(
        action_id=action.action_id,
        action_type=action.action_type.value,
        abs_magnitude=str(magnitude),
        net=str(quantize_money(action.cost_increase_total + action.cost_decrease_total)),
        threshold_rule=rule,
        threshold_value=str(row.value),
        threshold_id=row.threshold_id,
        certification_required=required,
        certification_statement=REA_CERTIFICATION_TEXT
        if required and action.action_type == REACDAType.REA
        else None,
        note="test uses ABS(increases) + ABS(decreases), never the net (spec §9)",
    )


def record_co_receipt(
    session: Session, action: REACDAAction, co_received_date: datetime.date
) -> REACDAAction:
    """Record CO receipt. For CDA claims this anchors BOTH derived dates:
    interest accrues from receipt (41 U.S.C. 7109), and the CO must decide
    within 60 days for claims at/below the certification threshold (larger
    claims get a firm date the CO sets — recorded as no computed deadline).
    REAs carry neither: no statutory deadline, interest never accrues."""
    action.co_received_date = co_received_date
    if action.action_type == REACDAType.CDA_CLAIM:
        action.interest_accrual_start_date = co_received_date
        magnitude = abs(action.cost_increase_total) + abs(action.cost_decrease_total)
        cert_row = threshold_in_force(session, "CDA_CLAIM_CERT", co_received_date)
        if magnitude <= cert_row.value:
            action.co_response_deadline = co_received_date + datetime.timedelta(
                days=CDA_DECISION_WINDOW_DAYS
            )
        else:
            action.co_response_deadline = None  # CO must set a firm date
    session.flush()
    return action
