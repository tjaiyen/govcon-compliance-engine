"""Tool registry — the engine's pure services adapted into Claude tool_use tools.

Each ToolSpec binds a Claude tool definition (name, description, JSON schema) to
a ``run(session, input) -> dict`` that mirrors the corresponding endpoint body in
api/app.py: it builds the transient (unsaved) model object, calls the REAL pure
service, and returns the dataclass fields INCLUDING the grounding payload
(reasons / caveats / provenance / source_citation). The model can only obtain a
number by calling one of these — there is no tool that writes, and no tool that
computes a determination the model itself could fabricate.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from govcon.models import Contract, ContractAction
from govcon.models.enums import (
    AgencyType,
    CASCoverageType,
    ContractActionType,
    ContractorSize,
)
from govcon.services.cas_tina import (
    TINA_EXCEPTIONS,
    determine_cas_coverage,
    determine_tina_applicability,
)
from govcon.services.reverification import reverification_items
from govcon.services.sf1408 import explain_limitations, has_data, run_self_check
from govcon.services.thresholds import threshold_in_force


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    run: Callable[[Session, dict], dict]

    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


#: A dollar amount above this is not a real contract value — reject it rather
#: than let NaN/Infinity/1e400 into the engine (breaks comparisons, bloats the
#: audit/grounding ledger, and can raise from Decimal.quantize downstream).
_MAX_MONEY = Decimal("1e15")


def _money(raw) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"not a valid dollar amount: {raw!r}") from exc
    if not value.is_finite() or abs(value) > _MAX_MONEY:
        raise ValueError(f"dollar amount out of range: {raw!r}")
    return value


def _date(raw) -> datetime.date:
    return datetime.date.fromisoformat(str(raw))


# ---------------------------------------------------------------- tool runners
def _run_cas(session: Session, inp: dict) -> dict:
    contract = Contract(
        award_date=_date(inp["award_date"]),
        contract_value=_money(inp["contract_value"]),
        contractor_size=ContractorSize(inp.get("contractor_size", "other_than_small")),
        is_nontraditional_dc=bool(inp.get("is_nontraditional_dc", False)),
        agency_type=AgencyType(inp.get("agency_type", "dod")),
        cas_coverage_type=CASCoverageType.NONE,
    )
    try:
        d = determine_cas_coverage(session, contract)
    except LookupError as exc:
        return {"available": False, "message": str(exc)}
    return {
        "available": True,
        "tier": d.tier,
        "requires_review": d.requires_review,
        "disclosure_required": d.disclosure_required,
        "reasons": d.reasons,
        "caveats": d.caveats,
        "provenance": d.provenance,
    }


def _run_tina(session: Session, inp: dict) -> dict:
    action = ContractAction(
        action_type=ContractActionType.OTHER_NEGOTIATED_ACTION,
        action_date=_date(inp["action_date"]),
        proposed_value=_money(inp["proposed_value"]),
        **{name: inp.get(name) for name in TINA_EXCEPTIONS},
    )
    try:
        d = determine_tina_applicability(session, action)
    except LookupError as exc:
        return {"available": False, "message": str(exc)}
    return {
        "available": True,
        "threshold_value": str(d.threshold_value),
        "above_threshold": d.above_threshold,
        "certification_required": d.certification_required,
        "exception_applied": d.exception_applied,
        "unevaluated_exceptions": d.unevaluated_exceptions,
        "reasons": d.reasons,
        "caveats": d.caveats,
        "provenance": d.provenance,
    }


def _run_pricing_analysis(session: Session, inp: dict) -> dict:
    from govcon.services.pricing_analysis import determine_price_or_cost_analysis

    action = ContractAction(
        action_type=ContractActionType.OTHER_NEGOTIATED_ACTION,
        action_date=_date(inp["action_date"]),
        proposed_value=_money(inp["proposed_value"]),
        **{name: inp.get(name) for name in TINA_EXCEPTIONS},
    )
    try:
        d = determine_price_or_cost_analysis(session, action)
    except LookupError as exc:
        return {"available": False, "message": str(exc)}
    return {
        "available": True,
        "analysis_required": d.analysis_required,
        "certified_data_required": d.certified_data_required,
        "reasons": d.reasons,
        "caveats": d.caveats,
        "source_citation": d.source_citation,
    }


def _run_subcontract_data(session: Session, inp: dict) -> dict:
    from govcon.services.pricing_analysis import determine_subcontract_certified_data

    try:
        d = determine_subcontract_certified_data(
            session,
            prime_proposed_value=_money(inp["prime_proposed_value"]),
            sub_proposed_value=_money(inp["sub_proposed_value"]),
            on_date=_date(inp["on_date"]),
        )
    except LookupError as exc:
        return {"available": False, "message": str(exc)}
    return {
        "available": True,
        "certified_data_required": d.certified_data_required,
        "threshold_value": str(d.threshold_value),
        "prime_proposed_value": str(d.prime_proposed_value),
        "sub_proposed_value": str(d.sub_proposed_value),
        "ten_percent_of_prime": str(d.ten_percent_of_prime),
        "exceeds_threshold": d.exceeds_threshold,
        "exceeds_ten_percent_of_prime": d.exceeds_ten_percent_of_prime,
        "meets_absolute_20m": d.meets_absolute_20m,
        "reasons": d.reasons,
        "caveats": d.caveats,
        "source_citation": d.source_citation,
    }


def _run_weighted_guidelines(session: Session, inp: dict) -> dict:
    from govcon.services.pricing_analysis import compute_weighted_guidelines_profit

    try:
        d = compute_weighted_guidelines_profit(
            cost_base=_money(inp["cost_base"]),
            contract_type=inp["contract_type"],
            technical_pct=Decimal(str(inp["technical_pct"])),
            management_pct=Decimal(str(inp["management_pct"])),
            contract_type_risk_pct=Decimal(str(inp["contract_type_risk_pct"])),
            technology_incentive=bool(inp.get("technology_incentive", False)),
            facilities_capital_profit=_money(inp.get("facilities_capital_profit", "0")),
        )
    except (ValueError, InvalidOperation) as exc:
        return {"available": False, "message": str(exc)}
    return {
        "available": True,
        "cost_base": str(d.cost_base),
        "contract_type": d.contract_type,
        "performance_risk_profit": str(d.performance_risk_profit),
        "contract_type_risk_profit": str(d.contract_type_risk_profit),
        "facilities_capital_profit": str(d.facilities_capital_profit),
        "total_profit_objective": str(d.total_profit_objective),
        "profit_rate_pct": str(d.profit_rate_pct),
        "factor_findings": d.factor_findings,
        "reasons": d.reasons,
        "caveats": d.caveats,
        "source_citation": d.source_citation,
    }


def _run_threshold(session: Session, inp: dict) -> dict:
    try:
        row = threshold_in_force(session, inp["rule"], _date(inp["on"]))
    except LookupError as exc:
        return {"in_force": False, "message": str(exc)}
    return {
        "in_force": True,
        "rule_name": row.rule_name,
        "value": None if row.value is None else str(row.value),
        "effective_date": None if row.effective_date is None else row.effective_date.isoformat(),
        "status": row.status.value,
        "source_citation": row.source_citation,
    }


def _run_sf1408(session: Session, inp: dict) -> dict:
    return {
        "has_data": has_data(session),
        "criteria": [
            {"criterion": r.criterion, "name": r.name, "passed": r.passed, "findings": r.findings}
            for r in run_self_check(session)
        ],
    }


def _run_reverify(session: Session, inp: dict) -> dict:
    as_of = datetime.date.today() if not inp.get("as_of") else _date(inp["as_of"])
    items = reverification_items(session, as_of)
    return {
        "as_of": as_of.isoformat(),
        "items": [{"kind": i.kind, "due": i.due, "description": i.description} for i in items],
    }


def _run_glossary(session: Session, inp: dict) -> dict:
    from govcon.education import GLOSSARY

    term = (inp.get("term") or "").strip().lower()
    if term:
        hits = [g for g in GLOSSARY if term in g["term"].lower()]
        return {"terms": hits or GLOSSARY}
    return {"terms": GLOSSARY}


def _run_scenarios(session: Session, inp: dict) -> dict:
    from govcon.education import SCENARIOS

    return {"scenarios": [{"id": s["id"], "title": s["title"], "story": s["story"]} for s in SCENARIOS]}


def _run_limitations(session: Session, inp: dict) -> dict:
    return {"limitations": explain_limitations()}


def _run_watch_review(session: Session, inp: dict) -> dict:
    """Read-only: the regulation-watch inbox, so a rule-authoring session can be
    grounded in an ACTUAL suggested change rather than a hallucinated one. Never
    writes; mirrors /api/suggestions (strong matches first)."""
    import sqlalchemy as sa

    from govcon.models import RegulatorySuggestion

    rows = (
        session.execute(
            sa.select(RegulatorySuggestion)
            .order_by(
                RegulatorySuggestion.strong_match.desc(),
                RegulatorySuggestion.publication_date.desc(),
            )
            .limit(20)
        )
        .scalars()
        .all()
    )
    return {
        "suggestions": [
            {
                "suggestion_id": r.suggestion_id,
                "watch_rule": r.watch_rule,
                "document_number": r.document_number,
                "doc_type": r.doc_type,
                "title": r.title,
                "effective_on": None if r.effective_on is None else r.effective_on.isoformat(),
                "url": r.url,
                "strong_match": r.strong_match,
                "status": r.status.value,
            }
            for r in rows
        ]
    }


def _run_validate_draft_rule(session: Session, inp: dict) -> dict:
    """Structurally validate a DRAFT decision rule against the engine grammar.
    Pure — writes nothing, executes nothing (B53). ``session`` is ignored."""
    from govcon.services.rule_authoring import validate_draft_rule

    return validate_draft_rule(inp.get("rule") if isinstance(inp.get("rule"), dict) else inp)


# ------------------------------------------------------------------- registry
_CONTRACTOR_SIZE = {"type": "string", "enum": [s.value for s in ContractorSize]}
_AGENCY = {"type": "string", "enum": [a.value for a in AgencyType]}
_TINA_EXC = {name: {"type": ["boolean", "null"]} for name in TINA_EXCEPTIONS}

TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in [
        ToolSpec(
            name="determine_cas_coverage",
            description=(
                "Determine the CAS (Cost Accounting Standards) coverage tier for a contract on "
                "its award date. Call this whenever the user asks whether/which CAS coverage "
                "applies. Returns the tier plus reasons, caveats, and decision-table provenance."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "award_date": {"type": "string", "description": "ISO date the contract was awarded"},
                    "contract_value": {"type": "string", "description": "Contract value in USD, decimal string"},
                    "contractor_size": _CONTRACTOR_SIZE,
                    "is_nontraditional_dc": {"type": "boolean"},
                    "agency_type": _AGENCY,
                },
                "required": ["award_date", "contract_value"],
            },
            run=_run_cas,
        ),
        ToolSpec(
            name="determine_tina_applicability",
            description=(
                "Determine whether TINA (Truthful Cost or Pricing Data) certified data is required "
                "for a specific contract action on its own date and value. The four statutory "
                "exception fields are tri-state: true (applies), false (evaluated, does not apply), "
                "or omitted/null (NOT yet evaluated → the answer is pending, never assumed)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action_date": {"type": "string", "description": "ISO date of the action"},
                    "proposed_value": {"type": "string", "description": "Action value in USD, decimal string"},
                    **_TINA_EXC,
                },
                "required": ["action_date", "proposed_value"],
            },
            run=_run_tina,
        ),
        ToolSpec(
            name="determine_price_or_cost_analysis",
            description=(
                "FAR 15.404-1: determine whether PRICE analysis suffices or COST analysis is "
                "required for a contract action. Cost analysis is required exactly when certified "
                "cost or pricing data are required (TINA applies, no exception); otherwise price "
                "analysis is the basis. Same tri-state exception fields as TINA."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action_date": {"type": "string", "description": "ISO date of the action"},
                    "proposed_value": {"type": "string", "description": "Action value in USD, decimal string"},
                    **_TINA_EXC,
                },
                "required": ["action_date", "proposed_value"],
            },
            run=_run_pricing_analysis,
        ),
        ToolSpec(
            name="determine_subcontract_certified_data",
            description=(
                "FAR 15.404-3(c)(1): determine whether the prime must obtain certified cost or "
                "pricing data from a subcontractor — required when the subcontract price is BOTH "
                "more than the dated certified-data threshold AND more than 10% of the prime's "
                "proposed price, OR $20 million or more."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "on_date": {"type": "string", "description": "ISO date the threshold is read on"},
                    "prime_proposed_value": {"type": "string", "description": "Prime's proposed price, USD decimal string"},
                    "sub_proposed_value": {"type": "string", "description": "Subcontract's proposed price, USD decimal string"},
                },
                "required": ["on_date", "prime_proposed_value", "sub_proposed_value"],
            },
            run=_run_subcontract_data,
        ),
        ToolSpec(
            name="compute_weighted_guidelines_profit",
            description=(
                "FAR 15.404-4 / DFARS 215.404-71: compute the DoD weighted-guidelines profit "
                "OBJECTIVE (DD-1547) from a cost base, contract type, and assigned risk factor "
                "percentages, validating each factor against its DFARS designated range. "
                "contract_type is one of ffp_no_financing, ffp_performance_based_payments, "
                "ffp_progress_payments, ffp_level_of_effort, fpi_no_financing, "
                "fpi_performance_based_payments, fpi_progress_payments, cpif, cpff, "
                "time_and_materials, labor_hour. Percentages are numbers like 5 (=5%)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "cost_base": {"type": "string", "description": "Total cost excl. FCCM (Block 20), USD decimal string"},
                    "contract_type": {"type": "string"},
                    "technical_pct": {"type": "string"},
                    "management_pct": {"type": "string"},
                    "contract_type_risk_pct": {"type": "string"},
                    "technology_incentive": {"type": "boolean"},
                    "facilities_capital_profit": {"type": "string"},
                },
                "required": ["cost_base", "contract_type", "technical_pct", "management_pct", "contract_type_risk_pct"],
            },
            run=_run_weighted_guidelines,
        ),
        ToolSpec(
            name="threshold_in_force",
            description=(
                "Look up the dated regulatory threshold in force for a rule on a date, with its "
                "legal status and source citation. Rules include TINA_THRESHOLD, "
                "CAS_CONTRACT_TRIGGER, CAS_FULL_COVERAGE, SAT, EXEC_COMP_CAP."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "rule": {"type": "string"},
                    "on": {"type": "string", "description": "ISO date"},
                },
                "required": ["rule", "on"],
            },
            run=_run_threshold,
        ),
        ToolSpec(
            name="run_self_check",
            description="Run the SF 1408 six-criteria pre-award accounting-system self-check against the database.",
            input_schema={"type": "object", "properties": {}},
            run=_run_sf1408,
        ),
        ToolSpec(
            name="reverification_items",
            description="List regulatory re-verification watch items (date checkpoints + non-final thresholds/rules).",
            input_schema={
                "type": "object",
                "properties": {"as_of": {"type": "string", "description": "ISO date, optional"}},
            },
            run=_run_reverify,
        ),
        ToolSpec(
            name="lookup_glossary",
            description="Look up plain-language GovCon term definitions with grounded examples. Optional 'term' filter.",
            input_schema={"type": "object", "properties": {"term": {"type": "string"}}},
            run=_run_glossary,
        ),
        ToolSpec(
            name="list_scenarios",
            description="List the worked teaching scenarios (id, title, story).",
            input_schema={"type": "object", "properties": {}},
            run=_run_scenarios,
        ),
        ToolSpec(
            name="explain_limitations",
            description="Return the tool's own stated limitations (advisory, synthetic-only, AI-is-a-rendering).",
            input_schema={"type": "object", "properties": {}},
            run=_run_limitations,
        ),
        ToolSpec(
            name="regulation_watch_review",
            description=(
                "Read-only: list the current regulation-watch suggestions (Federal "
                "Register hits a human should review). Use to ground a rule draft in "
                "a real suggested change. It changes nothing."
            ),
            input_schema={"type": "object", "properties": {}},
            run=_run_watch_review,
        ),
        ToolSpec(
            name="validate_draft_rule",
            description=(
                "Structurally validate a DRAFT decision-table rule against the engine's "
                "grammar (does its when_ast parse; does reason_template use only {name} "
                "placeholders). Returns {valid, errors, references, requires_human_migration}. "
                "It NEVER applies the rule — a valid draft still needs a human-reviewed "
                "migration. Pass the draft as {\"rule\": {rule_key, when_ast, outcome, "
                "reason_template, stop}}."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "rule": {
                        "type": "object",
                        "description": "The draft rule: rule_key, when_ast, outcome, reason_template, stop.",
                    }
                },
                "required": ["rule"],
            },
            run=_run_validate_draft_rule,
        ),
    ]
}

#: Tool subset for the conversational-query pattern (Phase A).
ASK_TOOLS = [
    "determine_cas_coverage",
    "determine_tina_applicability",
    "determine_price_or_cost_analysis",
    "determine_subcontract_certified_data",
    "compute_weighted_guidelines_profit",
    "threshold_in_force",
    "run_self_check",
    "reverification_items",
    "lookup_glossary",
    "explain_limitations",
]

#: Pattern 2 (AI tutor): the ask tools plus the scenario library, so the tutor
#: can point a learner at a hands-on example. Still read-only, still no tool that
#: computes a determination the AI could fabricate.
TUTOR_TOOLS = [*ASK_TOOLS, "list_scenarios"]

#: Pattern 3 (rule-authoring): read the watch inbox + validate a draft rule
#: STRUCTURALLY. Deliberately NO write tool and NO evaluate tool — auto-apply is
#: structurally impossible (B53); the only output is a validated draft for a
#: human-reviewed migration.
DRAFT_RULE_TOOLS = [
    "regulation_watch_review",
    "validate_draft_rule",
    "threshold_in_force",
    "lookup_glossary",
    "explain_limitations",
]


def tool_definitions(names: list[str]) -> list[dict]:
    return [TOOLS[n].definition() for n in names]
