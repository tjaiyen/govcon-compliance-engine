"""FAR 15.404 pricing-analysis determinations (Subpart 15.4 — Contract Pricing).

Two determinations a Contract Costing & Pricing analyst needs, layered on the
existing TINA/threshold machinery and grounded to the primary source (48 CFR):

  * determine_price_or_cost_analysis (FAR 15.404-1): does PRICE analysis suffice,
    or is COST analysis required? Grounded rule — "Price analysis shall be used
    when certified cost or pricing data are not required" (15.404-1(a)(2)); "Cost
    analysis shall be used to evaluate the reasonableness of individual cost
    elements when certified cost or pricing data are required" (15.404-1(a)(3)).
    So this is a thin, honest view over the TINA applicability determination.

  * determine_subcontract_certified_data (FAR 15.404-3(c)(1)): must the prime
    obtain (and analyze) certified cost or pricing data from a subcontractor?
    Grounded rule — required when the subcontract price is BOTH more than the
    pertinent certified cost-or-pricing-data threshold AND more than 10 percent of
    the prime contractor's proposed price, OR $20 million or more. The dated
    threshold resolves through threshold_in_force (never a hard-coded scalar —
    ground rule 2); the 10% and $20M figures are structural FAR text (15.404-3(c)(1)).

SYNTHETIC exercise — advisory decision-support, not a certification or a filing.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from govcon.models import ContractAction
from govcon.services.cas_tina import determine_tina_applicability
from govcon.services.thresholds import status_caveat, threshold_in_force

#: FAR 15.404-3(c)(1) structural figures (not inflation-adjusted; verbatim rule text).
_SUBCONTRACT_PCT_OF_PRIME = Decimal("0.10")  # "more than 10 percent of the prime … price"
_SUBCONTRACT_ABSOLUTE = Decimal("20000000.00")  # "$20 million or more"


# --------------------------------------------------- FAR 15.404-1: which analysis
@dataclass
class AnalysisTypeDetermination:
    #: "cost_analysis" | "price_analysis" | "pending" (TINA exceptions unevaluated)
    analysis_required: str
    certified_data_required: bool | None
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    source_citation: str = "FAR 15.404-1"
    #: TINA table version + fired rules (the analysis type is derived from it).
    provenance: dict | None = None


def determine_price_or_cost_analysis(
    session: Session, action: ContractAction
) -> AnalysisTypeDetermination:
    """FAR 15.404-1: cost analysis is required exactly when certified cost or
    pricing data are required (TINA applies, no exception); otherwise price
    analysis is the basis for a fair-and-reasonable determination. A thin, honest
    view over the TINA applicability determination on this action's own date."""
    tina = determine_tina_applicability(session, action)
    cr = tina.certification_required
    if cr is True:
        analysis = "cost_analysis"
        reasons = [
            "Certified cost or pricing data are required (TINA applies with no "
            "exception), so COST analysis is required to evaluate the "
            "reasonableness of individual cost elements (FAR 15.404-1(a)(3), (c))."
        ]
    elif cr is False:
        analysis = "price_analysis"
        reasons = [
            "Certified cost or pricing data are NOT required, so PRICE analysis is "
            "the basis for a fair-and-reasonable price determination "
            "(FAR 15.404-1(a)(2), (b))."
        ]
    else:  # None — TINA exceptions not yet evaluated
        analysis = "pending"
        reasons = [
            "TINA applicability is pending exception evaluation; the required "
            "analysis type follows once certified-data applicability is settled."
        ]
    caveats = list(tina.caveats) + [
        "Price and cost analysis techniques may be used together (FAR 15.404-1(a)(1)).",
        "Even when certified cost or pricing data are not required, the contracting "
        "officer may require data other than certified cost or pricing data (FAR 15.403-3).",
    ]
    return AnalysisTypeDetermination(
        analysis_required=analysis,
        certified_data_required=cr,
        reasons=reasons,
        caveats=caveats,
        provenance=tina.provenance,
    )


# ---------------------------------------- FAR 15.404-3: subcontractor certified data
@dataclass
class SubcontractDataDetermination:
    certified_data_required: bool
    threshold_value: Decimal
    prime_proposed_value: Decimal
    sub_proposed_value: Decimal
    ten_percent_of_prime: Decimal
    exceeds_threshold: bool
    exceeds_ten_percent_of_prime: bool
    meets_absolute_20m: bool
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    source_citation: str = "FAR 15.404-3(c)(1)"


def determine_subcontract_certified_data(
    session: Session,
    *,
    prime_proposed_value: Decimal,
    sub_proposed_value: Decimal,
    on_date: datetime.date,
) -> SubcontractDataDetermination:
    """FAR 15.404-3(c)(1): the prime must obtain certified cost or pricing data
    from a subcontractor when the subcontract price is BOTH more than the pertinent
    (dated) certified cost-or-pricing-data threshold AND more than 10% of the
    prime's proposed price, OR $20 million or more. The threshold is a dated
    lookup; the 10%/$20M figures are structural FAR text."""
    th = threshold_in_force(session, "TINA_THRESHOLD", on_date)
    threshold = th.value
    ten_percent = _SUBCONTRACT_PCT_OF_PRIME * prime_proposed_value
    exceeds_threshold = sub_proposed_value > threshold
    exceeds_ten = sub_proposed_value > ten_percent
    meets_absolute = sub_proposed_value >= _SUBCONTRACT_ABSOLUTE
    required = (exceeds_threshold and exceeds_ten) or meets_absolute

    reasons: list[str] = []
    if meets_absolute:
        reasons.append(
            f"The subcontract price ${sub_proposed_value:,} is $20 million or more, "
            "so certified cost or pricing data are required from the subcontractor "
            "(FAR 15.404-3(c)(1))."
        )
    if exceeds_threshold and exceeds_ten:
        reasons.append(
            f"The subcontract price ${sub_proposed_value:,} exceeds BOTH the certified "
            f"cost-or-pricing-data threshold ${threshold:,} in force on "
            f"{on_date.isoformat()} AND 10% of the prime's proposed price "
            f"(${ten_percent:,}), so certified data are required (FAR 15.404-3(c)(1))."
        )
    if not required:
        reasons.append(
            f"The subcontract price ${sub_proposed_value:,} does not meet the "
            f"FAR 15.404-3(c)(1) trigger (more than ${threshold:,} AND more than 10% "
            f"of the prime's ${prime_proposed_value:,}, i.e. ${ten_percent:,}, or "
            "$20 million or more), so certified cost or pricing data are not mandatory."
        )

    caveats: list[str] = []
    sc = status_caveat(th)
    if sc:
        caveats.append(sc)
    caveats += [
        "Below these thresholds the contracting officer SHOULD still require "
        "subcontractor certified cost or pricing data unless unnecessary to price "
        "the prime contract (FAR 15.404-3(c)(2)).",
        "The prime remains responsible for conducting appropriate cost or price "
        "analysis of subcontractor proposals and for subcontract price reasonableness "
        "(FAR 15.404-3(a), (b)).",
        "A standard TINA exception (adequate price competition, commercial "
        "product/service, prices set by law, or a waiver) removes the requirement "
        "(FAR 15.403-1).",
    ]
    return SubcontractDataDetermination(
        certified_data_required=required,
        threshold_value=threshold,
        prime_proposed_value=prime_proposed_value,
        sub_proposed_value=sub_proposed_value,
        ten_percent_of_prime=ten_percent,
        exceeds_threshold=exceeds_threshold,
        exceeds_ten_percent_of_prime=exceeds_ten,
        meets_absolute_20m=meets_absolute,
        reasons=reasons,
        caveats=caveats,
    )
