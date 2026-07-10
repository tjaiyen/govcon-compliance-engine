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
    ]
}

#: Tool subset for the conversational-query pattern (Phase A).
ASK_TOOLS = [
    "determine_cas_coverage",
    "determine_tina_applicability",
    "threshold_in_force",
    "run_self_check",
    "reverification_items",
    "lookup_glossary",
    "explain_limitations",
]


def tool_definitions(names: list[str]) -> list[dict]:
    return [TOOLS[n].definition() for n in names]
