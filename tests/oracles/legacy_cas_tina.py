"""FROZEN parity oracle — a verbatim copy of the coded CAS/TINA logic as of
main@0e244e6, taken immediately BEFORE the Phase 1 rules-as-data rewiring.

tests/test_rules_parity.py drives an input matrix through both this oracle and
the table-driven services and asserts identical determinations. NEVER edit this
file to make a parity test pass — a divergence means the decision-table seed
(or the evaluator) is wrong, not the oracle.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.models import Contract, ContractAction, RegulatoryThreshold
from govcon.models.enums import CASCoverageType, ContractorSize, ThresholdStatus
from govcon.services.thresholds import threshold_in_force

#: The four statutory exceptions, in 10 U.S.C. 3703 order — field names on
#: contract_actions and result keys here stay identical.
TINA_EXCEPTIONS = (
    "tina_exception_adequate_price_competition",
    "tina_exception_commercial_product_service",
    "tina_exception_prices_set_by_law",
    "tina_exception_waiver_granted",
)


def _status_caveat(row: RegulatoryThreshold) -> str | None:
    if row.status == ThresholdStatus.FINAL_RULE:
        return None
    if row.status == ThresholdStatus.CARRY_FORWARD:
        return (
            f"threshold {row.rule_name}={row.value} is carried forward — the prior-period "
            "value still governs because its scheduled adjustment was formally waived; confirm "
            f"the freeze remains in effect before external reliance (source: {row.source_citation})"
        )
    return (
        f"threshold {row.rule_name}={row.value} is {row.status.value}, not settled "
        "final regulation — surface this status, do not present as settled law "
        f"(source: {row.source_citation})"
    )


@dataclass
class CASDetermination:
    contract_id: int | None
    tier: str  # exempt_small_business | review_nontraditional | none | modified | full
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    requires_review: bool = False
    disclosure_required: bool = False
    trigger_threshold_id: int | None = None
    full_threshold_id: int | None = None


def _cumulative_prior_year_cas_awards(
    session: Session, award_date: datetime.date
) -> Decimal:
    prior_start = datetime.date(award_date.year - 1, 1, 1)
    prior_end = datetime.date(award_date.year - 1, 12, 31)
    amounts = session.execute(
        sa.select(Contract.contract_value)
        .where(Contract.cas_coverage_type != CASCoverageType.NONE)
        .where(Contract.award_date >= prior_start)
        .where(Contract.award_date <= prior_end)
        .where(Contract.superseded_by.is_(None))
    ).scalars()
    return sum((Decimal(a) for a in amounts), Decimal("0.00"))


def oracle_determine_cas_coverage(session: Session, contract: Contract) -> CASDetermination:
    result = CASDetermination(contract_id=contract.contract_id, tier="none")

    # 1. Small business — CAS-exempt regardless of value (§7 step 1).
    if contract.contractor_size == ContractorSize.SMALL:
        result.tier = "exempt_small_business"
        result.reasons.append("small-business contractor: CAS-exempt regardless of value")
        return result

    # 2. Nontraditional defense contractor — a DISTINCT exemption path,
    #    flagged for review, never silently applied (§7 step 2; NDAA §1826
    #    also exempts FAR Part 31 cost principles per reg-ref §1).
    if contract.is_nontraditional_dc:
        result.tier = "review_nontraditional"
        result.requires_review = True
        result.reasons.append(
            "nontraditional-defense-contractor award: likely exempt from CAS and "
            "FAR Part 31 cost principles (NDAA §1826) — REVIEW REQUIRED, not "
            "silently applied"
        )
        return result

    trigger = threshold_in_force(session, "CAS_CONTRACT_TRIGGER", contract.award_date)
    full = threshold_in_force(session, "CAS_FULL_COVERAGE", contract.award_date)
    result.trigger_threshold_id = trigger.threshold_id
    result.full_threshold_id = full.threshold_id
    for row in (trigger, full):
        caveat = _status_caveat(row)
        if caveat:
            result.caveats.append(caveat)

    value = Decimal(contract.contract_value)

    # 3. Contract-level trigger (§7 step 3).
    if value < trigger.value:
        result.reasons.append(
            f"contract value {value} below the {trigger.value} CAS trigger in force "
            f"on {contract.award_date.isoformat()}"
        )
        return result
    result.tier = "modified"
    result.reasons.append(
        f"contract value {value} meets the {trigger.value} trigger in force on "
        f"{contract.award_date.isoformat()}: modified coverage (CAS 401/402/405/406)"
    )

    # 4. Full coverage: single award, or cumulative prior-year CAS-covered
    #    awards (§7 step 4 + the encoded cumulative window).
    cumulative = _cumulative_prior_year_cas_awards(session, contract.award_date)
    if value >= full.value or cumulative + value >= full.value:
        result.tier = "full"
        result.disclosure_required = True
        basis = (
            f"single award {value}"
            if value >= full.value
            else f"cumulative prior-year CAS-covered awards {cumulative} + this award {value}"
        )
        result.reasons.append(
            f"{basis} meets the {full.value} full-coverage threshold: all active "
            "standards apply and a CASB DS-1 Disclosure Statement obligation is triggered"
        )
    return result


@dataclass
class TINADetermination:
    action_id: int | None
    threshold_id: int
    threshold_value: Decimal
    above_threshold: bool
    certification_required: bool | None  # None = pending exception evaluation
    exception_applied: str | None = None
    unevaluated_exceptions: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


def oracle_determine_tina_applicability(
    session: Session, action: ContractAction
) -> TINADetermination:
    """Per-action TINA determination (§8). Reads ONLY this action's own
    date, value, and exception fields — inheritance from the parent vehicle
    or sibling actions is impossible by construction."""
    row = threshold_in_force(session, "TINA_THRESHOLD", action.action_date)
    result = TINADetermination(
        action_id=action.action_id,
        threshold_id=row.threshold_id,
        threshold_value=row.value,
        above_threshold=False,
        certification_required=None,
    )
    caveat = _status_caveat(row)
    if caveat:
        result.caveats.append(caveat)

    if action.proposed_value is None:
        result.reasons.append("action has no proposed_value — cannot determine; flag")
        return result

    value = Decimal(action.proposed_value)
    # TINA requires certified data only when the price EXCEEDS the threshold
    # (FAR 15.403-4 / 10 U.S.C. 3702) — an action exactly AT the threshold is
    # not required (a stress test found the boundary was inclusive-wrong; the
    # REA/CDA ABS-magnitude test at rea_cda.py already uses the correct `>`).
    if value <= row.value:
        result.certification_required = False
        result.reasons.append(
            f"action value {value} at or below the {row.value} TINA threshold in force "
            f"on {action.action_date.isoformat()}: certified cost-or-pricing data not required"
        )
        return result

    result.above_threshold = True
    for name in TINA_EXCEPTIONS:
        flag = getattr(action, name)
        if flag is True:
            result.certification_required = False
            result.exception_applied = name
            result.reasons.append(
                f"above threshold, but statutory exception {name} applies to THIS "
                "action (recorded, not a bare 'exempt' flag)"
            )
            return result
        if flag is None:
            result.unevaluated_exceptions.append(name)

    if result.unevaluated_exceptions:
        result.certification_required = None
        result.reasons.append(
            "above threshold with exceptions not yet evaluated "
            f"({len(result.unevaluated_exceptions)} of 4) — evaluate each explicitly "
            "on this action before concluding; do not inherit from the vehicle"
        )
        return result

    result.certification_required = True
    result.reasons.append(
        f"action value {value} meets the {row.value} threshold and all four statutory "
        "exceptions are evaluated False: certified cost-or-pricing data required "
        "(FAR 15.403-4)"
    )
    return result
