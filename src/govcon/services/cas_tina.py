"""CAS coverage-tier + TINA applicability determinations (spec §7 / §8) —
table-driven since Phase 1 (rules-as-data).

The decision LOGIC lives in the seeded CAS_COVERAGE / TINA_APPLICABILITY
decision tables (versioned, dated, append-only — see models.decision_tables
and migration 0015); this module is the thin adapter that assembles the
input facts, evaluates the table in force on the determination date, and
maps the outcome onto the same dataclasses the engine has always returned.
Parity with the pre-Phase-1 coded logic is proven by
tests/test_rules_parity.py against a frozen oracle copy.

Everything is still a dated lookup — thresholds resolve through
threshold_in_force on the evaluation date, never a hard-coded scalar (ground
rule 2), and every result carries status caveats for non-final thresholds
AND (new in Phase 1) non-final rule encodings (ground rule 3).

TINA applicability evaluates PER CONTRACT ACTION on the action's own date
and value via the four tina_exception_* fields — a common real-world
defective-pricing error is letting a task order inherit the vehicle's
"adequate price competition"; the input assembly reads ONLY this action, so
inheritance stays impossible by construction.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.models import Contract, ContractAction
from govcon.models.enums import CASCoverageType
from govcon.services.decision_engine import evaluate_table
from govcon.services.thresholds import status_caveat

#: Backward-compatible alias — the caveat wording moved to the thresholds
#: service in Phase 1 so the decision engine shares it (one wording, one place).
_status_caveat = status_caveat

#: The four statutory exceptions, in 10 U.S.C. 3703 order — field names on
#: contract_actions and result keys here stay identical.
TINA_EXCEPTIONS = (
    "tina_exception_adequate_price_competition",
    "tina_exception_commercial_product_service",
    "tina_exception_prices_set_by_law",
    "tina_exception_waiver_granted",
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


def determine_cas_coverage(session: Session, contract: Contract) -> CASDetermination:
    """Evaluate the CAS_COVERAGE decision table in force on the award date.

    Input assembly is the code half of the rules-as-data split: the
    cumulative prior-fiscal-year sum is computed here (it is a database
    fact, not a rule), then the seeded rule cascade decides the tier."""
    value = Decimal(contract.contract_value)
    cumulative = _cumulative_prior_year_cas_awards(session, contract.award_date)
    ev = evaluate_table(
        session,
        "CAS_COVERAGE",
        contract.award_date,
        {
            "contractor_size": contract.contractor_size.value,
            "is_nontraditional_dc": bool(contract.is_nontraditional_dc),
            "value": value,
            "cumulative": cumulative,
            "cumulative_plus_value": cumulative + value,
            "award_date": contract.award_date.isoformat(),
        },
    )
    return CASDetermination(
        contract_id=contract.contract_id,
        tier=ev.outcome["tier"],
        reasons=ev.reasons,
        caveats=ev.caveats,
        requires_review=ev.outcome["requires_review"],
        disclosure_required=ev.outcome["disclosure_required"],
        trigger_threshold_id=ev.threshold_ids.get("trigger"),
        full_threshold_id=ev.threshold_ids.get("full"),
    )


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


def determine_tina_applicability(
    session: Session, action: ContractAction
) -> TINADetermination:
    """Evaluate the TINA_APPLICABILITY decision table on the action's own
    date. Inputs come from THIS action only — inheritance from the parent
    vehicle or sibling actions is impossible by construction."""
    ev = evaluate_table(
        session,
        "TINA_APPLICABILITY",
        action.action_date,
        {
            "proposed_value": (
                None if action.proposed_value is None else Decimal(action.proposed_value)
            ),
            "action_date": action.action_date.isoformat(),
            **{name: getattr(action, name) for name in TINA_EXCEPTIONS},
            "unevaluated_count": sum(
                1 for name in TINA_EXCEPTIONS if getattr(action, name) is None
            ),
        },
    )
    # Bookkeeping of the statutory order (matches the coded loop exactly):
    # only exceptions encountered BEFORE an applied one — or all of them when
    # none applied above-threshold — are reported as unevaluated.
    unevaluated: list[str] = []
    if ev.outcome["above_threshold"]:
        for name in TINA_EXCEPTIONS:
            if name == ev.outcome["exception_applied"]:
                break
            if getattr(action, name) is None:
                unevaluated.append(name)
    return TINADetermination(
        action_id=action.action_id,
        threshold_id=ev.threshold_ids["th"],
        threshold_value=ev.threshold_values["th"],
        above_threshold=ev.outcome["above_threshold"],
        certification_required=ev.outcome["certification_required"],
        exception_applied=ev.outcome["exception_applied"],
        unevaluated_exceptions=unevaluated,
        reasons=ev.reasons,
        caveats=ev.caveats,
    )
