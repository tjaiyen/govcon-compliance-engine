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


# --------------------------------- FAR 15.404-4 / DFARS 215.404-71: weighted guidelines
# The DoD structured profit/fee objective (DD Form 1547). Factor ranges are
# grounded verbatim to DFARS 215.404-71-2 (performance risk) and -71-3 (contract
# type risk); percentages apply to Block 20 = total contract cost EXCLUDING
# facilities capital cost of money. Facilities-capital profit (215.404-71-4) uses
# its own DD-1861 cost-of-money factors and is accepted here as a provided input,
# not computed — stated as an explicit limitation.

_PERF_RANGE = (Decimal("3"), Decimal("7"))            # technical + management/cost-control
_TECH_INCENTIVE_RANGE = (Decimal("7"), Decimal("11"))  # technical factor, innovation
_PERF_NORMAL = Decimal("5")

#: Contract-type risk: (low, high, normal) designated ranges, DFARS 215.404-71-3.
_CTR_RANGES: dict[str, tuple[Decimal, Decimal, Decimal]] = {
    "ffp_no_financing": (Decimal("4"), Decimal("6"), Decimal("5.0")),
    "ffp_performance_based_payments": (Decimal("2.5"), Decimal("5.5"), Decimal("4.0")),
    "ffp_progress_payments": (Decimal("2"), Decimal("4"), Decimal("3.0")),
    "ffp_level_of_effort": (Decimal("0"), Decimal("1"), Decimal("0.5")),
    "fpi_no_financing": (Decimal("2"), Decimal("4"), Decimal("3.0")),
    "fpi_performance_based_payments": (Decimal("0.5"), Decimal("3.5"), Decimal("2.0")),
    "fpi_progress_payments": (Decimal("0"), Decimal("2"), Decimal("1.0")),
    "cpif": (Decimal("0"), Decimal("2"), Decimal("1.0")),
    "cpff": (Decimal("0"), Decimal("1"), Decimal("0.5")),
    "time_and_materials": (Decimal("0"), Decimal("1"), Decimal("0.5")),
    "labor_hour": (Decimal("0"), Decimal("1"), Decimal("0.5")),
}

CONTRACT_TYPES = tuple(_CTR_RANGES)


@dataclass
class WeightedGuidelinesProfit:
    cost_base: Decimal  # DD-1547 Block 20 (total cost excluding FCCM)
    contract_type: str
    performance_risk_profit: Decimal
    contract_type_risk_profit: Decimal
    facilities_capital_profit: Decimal
    total_profit_objective: Decimal
    profit_rate_pct: Decimal
    factor_findings: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    source_citation: str = "DFARS 215.404-71 (FAR 15.404-4)"


def _finding(name: str, value: Decimal, low: Decimal, high: Decimal) -> dict:
    return {
        "factor": name,
        "value_pct": str(value),
        "designated_range": f"{low}% to {high}%",
        "in_range": low <= value <= high,
    }


def compute_weighted_guidelines_profit(
    *,
    cost_base: Decimal,
    contract_type: str,
    technical_pct: Decimal,
    management_pct: Decimal,
    contract_type_risk_pct: Decimal,
    technology_incentive: bool = False,
    facilities_capital_profit: Decimal = Decimal(0),
) -> WeightedGuidelinesProfit:
    """DFARS 215.404-71 weighted-guidelines profit objective. Each assigned factor
    is validated against its DFARS designated range (flagged, never silently
    clamped); the objective sums performance-risk + contract-type-risk (each a %
    of the cost base) + the provided facilities-capital profit. Pure — no dated
    lookups; the ranges are structural DFARS constants."""
    if contract_type not in _CTR_RANGES:
        raise ValueError(
            f"unknown contract_type {contract_type!r}; expected one of "
            f"{', '.join(_CTR_RANGES)}"
        )
    ctr_low, ctr_high, _ctr_normal = _CTR_RANGES[contract_type]
    tech_low, tech_high = _TECH_INCENTIVE_RANGE if technology_incentive else _PERF_RANGE

    findings = [
        _finding("technical risk", technical_pct, tech_low, tech_high),
        _finding("management/cost-control risk", management_pct, *_PERF_RANGE),
        _finding(f"contract-type risk ({contract_type})", contract_type_risk_pct, ctr_low, ctr_high),
    ]

    hundred = Decimal(100)
    cents = Decimal("0.01")
    perf_profit = ((technical_pct + management_pct) / hundred * cost_base).quantize(cents)
    ctr_profit = (contract_type_risk_pct / hundred * cost_base).quantize(cents)
    facilities_capital_profit = facilities_capital_profit.quantize(cents)
    total = perf_profit + ctr_profit + facilities_capital_profit
    rate = (total / cost_base * hundred).quantize(cents) if cost_base else Decimal("0.00")

    reasons = [
        f"Performance-risk profit = (technical {technical_pct}% + management "
        f"{management_pct}%) × ${cost_base:,} (Block 20) = ${perf_profit:,} "
        "(DFARS 215.404-71-2).",
        f"Contract-type-risk profit = {contract_type_risk_pct}% × ${cost_base:,} = "
        f"${ctr_profit:,} for a {contract_type} contract (DFARS 215.404-71-3).",
        f"Total profit objective ${total:,} = {rate}% of the ${cost_base:,} cost base.",
    ]
    caveats = [
        "Each factor must be assigned WITHIN its DFARS designated range; a value "
        "outside the range is flagged for justification (DFARS 215.404-71-2/-3).",
        "Facilities-capital profit (DFARS 215.404-71-4) is computed separately by "
        "compute_facilities_capital_profit (land/buildings 0%, equipment 17.5%) and "
        "provided here as the facilities-capital component.",
        "This is the objective going into negotiation, not the negotiated profit; "
        "profit is always a negotiated outcome (FAR 15.404-4(a), (b)).",
    ]
    out_of_range = [f["factor"] for f in findings if not f["in_range"]]
    if out_of_range:
        caveats.insert(0, f"Assigned factor(s) OUTSIDE the DFARS designated range: {', '.join(out_of_range)} — require documented justification.")

    return WeightedGuidelinesProfit(
        cost_base=cost_base,
        contract_type=contract_type,
        performance_risk_profit=perf_profit,
        contract_type_risk_profit=ctr_profit,
        facilities_capital_profit=facilities_capital_profit,
        total_profit_objective=total,
        profit_rate_pct=rate,
        factor_findings=findings,
        reasons=reasons,
        caveats=caveats,
    )


# ------------------------------ DFARS 215.404-71-4: facilities capital employed profit
# Grounded factors: land 0%, buildings 0%, equipment 17.5% normal (range 10%–25%),
# applied to the facilities capital EMPLOYED by category (from the DD Form 1861
# allocation of the CAS 414/417 cost of money). Only equipment earns profit; land
# and buildings carry a 0% factor. Feeds the DD-1547 profit objective (215.404-71).

_FCE_EQUIPMENT_NORMAL = Decimal("17.5")
_FCE_EQUIPMENT_RANGE = (Decimal("10"), Decimal("25"))


@dataclass
class FacilitiesCapitalProfit:
    land_capital: Decimal
    buildings_capital: Decimal
    equipment_capital: Decimal
    equipment_factor_pct: Decimal
    facilities_capital_profit: Decimal
    factor_findings: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    source_citation: str = "DFARS 215.404-71-4"


def compute_facilities_capital_profit(
    *,
    equipment_capital: Decimal,
    land_capital: Decimal = Decimal(0),
    buildings_capital: Decimal = Decimal(0),
    equipment_factor_pct: Decimal = _FCE_EQUIPMENT_NORMAL,
) -> FacilitiesCapitalProfit:
    """DFARS 215.404-71-4: profit for facilities capital employed. Land and
    buildings carry a 0% factor; equipment carries a 17.5% normal factor within a
    10%–25% designated range. Profit = equipment capital × equipment factor. The
    equipment factor is validated against its range and flagged (never clamped)."""
    equip_low, equip_high = _FCE_EQUIPMENT_RANGE
    cents = Decimal("0.01")
    profit = (equipment_capital * equipment_factor_pct / Decimal(100)).quantize(cents)

    findings = [
        {"factor": "land", "value_pct": "0", "designated_range": "0% (N/A)", "in_range": True},
        {"factor": "buildings", "value_pct": "0", "designated_range": "0% (N/A)", "in_range": True},
        {
            "factor": "equipment",
            "value_pct": str(equipment_factor_pct),
            "designated_range": f"{equip_low}% to {equip_high}%",
            "in_range": equip_low <= equipment_factor_pct <= equip_high,
        },
    ]
    reasons = [
        "Land and buildings carry a 0% facilities-capital factor — no profit "
        "contribution (DFARS 215.404-71-4).",
        f"Equipment facilities capital ${equipment_capital:,} × {equipment_factor_pct}% "
        f"= ${profit:,} facilities-capital profit (DFARS 215.404-71-4).",
    ]
    caveats = [
        "Facilities capital employed comes from the DD Form 1861 allocation of the "
        "CAS 414 / 417 facilities capital cost of money to the contract.",
        "Feed this amount into the weighted-guidelines objective as the "
        "facilities-capital profit (DD-1547, DFARS 215.404-71).",
    ]
    if not (equip_low <= equipment_factor_pct <= equip_high):
        caveats.insert(0, f"Equipment factor {equipment_factor_pct}% is OUTSIDE the DFARS 10%–25% designated range — requires documented justification.")

    return FacilitiesCapitalProfit(
        land_capital=land_capital,
        buildings_capital=buildings_capital,
        equipment_capital=equipment_capital,
        equipment_factor_pct=equipment_factor_pct,
        facilities_capital_profit=profit,
        factor_findings=findings,
        reasons=reasons,
        caveats=caveats,
    )


# ---------------------------------------------- FAR 15.404-1(d): cost realism analysis
# Grounded: cost realism analysis SHALL be performed on cost-reimbursement contracts
# to determine the PROBABLE COST (which may differ from proposed and is used for
# evaluation) — 15.404-1(d)(2); it MAY be used on competitive FPI or, in exceptional
# cases, other FP contracts to assess performance risk — 15.404-1(d)(3).

_COST_REIMBURSEMENT_TYPES = {"cpff", "cpif", "cpaf", "cost", "cost_sharing"}


@dataclass
class CostRealismDetermination:
    contract_type: str
    realism_status: str  # "required" (cost-reimbursement) | "discretionary" (FP)
    proposed_cost: Decimal
    probable_cost: Decimal
    total_adjustment: Decimal
    adjustment_pct: Decimal
    element_findings: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    source_citation: str = "FAR 15.404-1(d)"


def assess_cost_realism(
    *, contract_type: str, cost_elements: list[dict]
) -> CostRealismDetermination:
    """FAR 15.404-1(d): roll a proposal's cost elements up to a PROBABLE COST.
    Each element carries a proposed and a probable (realism-adjusted) amount; the
    probable cost = sum of probable amounts, and — for cost-reimbursement contracts
    — is what the government evaluates, not the proposed cost. Pure; no dated lookup."""
    cents = Decimal("0.01")
    required = contract_type in _COST_REIMBURSEMENT_TYPES
    status = "required" if required else "discretionary"

    findings: list[dict] = []
    proposed_total = Decimal(0)
    probable_total = Decimal(0)
    for el in cost_elements:
        proposed = Decimal(str(el.get("proposed", "0")))
        probable = Decimal(str(el.get("probable", el.get("proposed", "0"))))
        proposed_total += proposed
        probable_total += probable
        adj = (probable - proposed).quantize(cents)
        findings.append({
            "element": str(el.get("name", "cost element")),
            "proposed": str(proposed.quantize(cents)),
            "probable": str(probable.quantize(cents)),
            "adjustment": str(adj),
            "adjusted": adj != 0,
        })
    proposed_total = proposed_total.quantize(cents)
    probable_total = probable_total.quantize(cents)
    adjustment = (probable_total - proposed_total).quantize(cents)
    pct = ((adjustment / proposed_total * Decimal(100)).quantize(cents)
           if proposed_total else Decimal("0.00"))

    if required:
        reasons = [
            "Cost realism analysis SHALL be performed on cost-reimbursement "
            "contracts to determine the probable cost (FAR 15.404-1(d)(2))."
        ]
    else:
        reasons = [
            "Cost realism analysis MAY be used on competitive fixed-price-incentive "
            "or, in exceptional cases, other fixed-price contracts to assess "
            "performance risk (FAR 15.404-1(d)(3))."
        ]
    reasons.append(
        f"Probable cost ${probable_total:,} = proposed ${proposed_total:,} "
        f"{'+' if adjustment >= 0 else '-'} ${abs(adjustment):,} realism adjustment "
        f"({pct}%)."
    )
    caveats = [
        "The probable cost — not the proposed cost — is used for evaluation to "
        "determine best value on a cost-reimbursement contract (FAR 15.404-1(d)(2)).",
        "Each adjustment must be realistic for the work and consistent with the "
        "offeror's own technical proposal, element by element (FAR 15.404-1(d)(1)).",
        "Probable cost is the government's best estimate of the most likely cost; "
        "it does not change the contract's estimated cost or ceiling.",
    ]
    return CostRealismDetermination(
        contract_type=contract_type,
        realism_status=status,
        proposed_cost=proposed_total,
        probable_cost=probable_total,
        total_adjustment=adjustment,
        adjustment_pct=pct,
        element_findings=findings,
        reasons=reasons,
        caveats=caveats,
    )


# ------------------- FAR 31.205-10 / CAS 414 (9904.414): facilities capital cost of money
# Grounded: FCCM is an imputed cost, "not a form of interest on borrowings"
# (FAR 31.205-10(a)(1)), allowable when measured/allocated per CAS 414 (31.205-10(b)).
# CAS 9904.414-50(c)(3): the cost of capital committed to facilities is "the sum of the
# products obtained by multiplying the amount of allocation base units … by the facilities
# capital cost of money factor for the corresponding indirect cost pool" — i.e. the DD
# Form 1861 / Form CASB-CMF computation. The factors themselves derive from the cost of
# money rate, which per 9904.414-50(b) is "the arithmetic mean of the interest rates
# specified by the Secretary of the Treasury pursuant to Public Law 92-41" — a dated
# rate the engine takes as input (never a hard-coded scalar — ground rule 2).


@dataclass
class FacilitiesCostOfMoney:
    total_cost_of_money: Decimal
    pool_findings: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    source_citation: str = "FAR 31.205-10 / CAS 9904.414-50"


def compute_facilities_cost_of_money(*, pools: list[dict]) -> FacilitiesCostOfMoney:
    """DD Form 1861 / CAS 9904.414-50(c)(3): facilities capital cost of money = the sum,
    over each indirect-cost pool, of the contract's allocation-base amount in that pool
    times the pool's facilities capital cost of money factor (from Form CASB-CMF). Pure;
    the factor already embeds the Treasury cost-of-money rate, so no rate is hard-coded."""
    cents = Decimal("0.01")
    findings: list[dict] = []
    total = Decimal(0)
    for p in pools:
        base = Decimal(str(p.get("allocation_base", "0")))
        factor = Decimal(str(p.get("cost_of_money_factor", "0")))
        fccm = (base * factor).quantize(cents)
        total += fccm
        findings.append({
            "pool": str(p.get("name", "pool")),
            "allocation_base": str(base.quantize(cents)),
            "cost_of_money_factor": str(factor),
            "cost_of_money": str(fccm),
        })
    total = total.quantize(cents)

    reasons = [
        "Facilities capital cost of money is the sum, over each indirect cost pool, of "
        "the contract's allocation-base units times that pool's facilities capital cost "
        "of money factor (CAS 9904.414-50(c)(3); DD Form 1861).",
        f"Total facilities capital cost of money = ${total:,} across {len(findings)} "
        "pool(s).",
    ]
    caveats = [
        "FCCM is an imputed cost — NOT a form of interest on borrowings "
        "(FAR 31.205-10(a)(1)); it is an allowable cost element, distinct from the "
        "weighted-guidelines facilities-capital PROFIT factor (DFARS 215.404-71-4).",
        "Each pool factor derives from the cost of money rate — the arithmetic mean of "
        "the Treasury interest rates under Public Law 92-41 (CAS 9904.414-50(b)) — applied "
        "to the net book value of facilities capital (Form CASB-CMF); supply the factor in "
        "force for the period, never a hard-coded rate.",
        "Allowable only when specifically identified and proposed, and measured/allocated "
        "per CAS 414 and FAR 31.205-52 (FAR 31.205-10(b)).",
    ]
    return FacilitiesCostOfMoney(
        total_cost_of_money=total,
        pool_findings=findings,
        reasons=reasons,
        caveats=caveats,
    )
