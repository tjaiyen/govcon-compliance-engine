"""Decision-table evaluator (enterprise vision Phase 1).

Evaluates a dated, versioned DecisionTable against a plain inputs dict and
returns the merged outcome plus the human-readable reasons/caveats trail —
the same explain-everything contract the coded services established.

Design constraints (deliberate, load-bearing):
  * Predicates are a structural JSON AST walked by _matches() — there is no
    eval()/exec() path, so a rule row can never execute code.
  * Threshold references resolve through threshold_in_force() on the
    evaluation date: dated-lookup semantics and status caveats stay
    single-sourced in the thresholds service.
  * Authoring errors fail LOUDLY (unknown op, missing input, comparison
    against None) — a malformed rule must never silently skip.
  * A missing/ambiguous table raises LookupError, mirroring the missing-
    threshold discipline: flag the gap, never invent a determination.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.models import DecisionRule, DecisionTable
from govcon.models.enums import ThresholdStatus
from govcon.services.thresholds import status_caveat, threshold_in_force

#: A reason template may reference ONLY simple {name} placeholders. This is a
#: plain substitution, NOT str.format — so a template can never traverse
#: attributes ({x.__class__...}), index, or reach object internals, even if a
#: malicious/buggy rule row supplied one. An unknown name fails loudly.
_TEMPLATE_FIELD = re.compile(r"\{(\w+)\}")


def _render_template(template: str, rule_key: str, values: dict) -> str:
    def _sub(m: re.Match) -> str:
        name = m.group(1)
        if name not in values:
            raise ValueError(
                f"rule {rule_key!r} reason_template references an "
                f"unavailable name: {name!r}"
            )
        return str(values[name])

    # A stray unmatched brace is an authoring error, not a silent literal.
    stripped = _TEMPLATE_FIELD.sub("", template)
    if "{" in stripped or "}" in stripped:
        raise ValueError(
            f"rule {rule_key!r} reason_template has an unmatched brace: {template!r}"
        )
    return _TEMPLATE_FIELD.sub(_sub, template)


_COMPARISONS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
}
_UNARY = {
    "is_true": lambda a: a is True,
    "is_false": lambda a: a is False,
    "is_null": lambda a: a is None,
    "not_null": lambda a: a is not None,
}
_ORDERED = {"lt", "le", "gt", "ge"}

#: Public, read-only views of the grammar so an authoring VALIDATOR can check a
#: proposed when_ast structurally without importing the private tables (and
#: without ever executing a rule — see govcon.services.rule_authoring).
COMPARISON_OPS = frozenset(_COMPARISONS)
UNARY_OPS = frozenset(_UNARY)
ORDERED_OPS = frozenset(_ORDERED)


@dataclass
class TableEvaluation:
    table_name: str
    version: int
    decision_table_id: int
    outcome: dict
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    #: alias -> resolved regulatory_thresholds row id / Decimal value
    threshold_ids: dict[str, int] = field(default_factory=dict)
    threshold_values: dict[str, Decimal] = field(default_factory=dict)
    fired_rules: list[str] = field(default_factory=list)


def table_in_force(
    session: Session, table_name: str, on_date: datetime.date
) -> DecisionTable:
    """The DecisionTable version in force on on_date — window semantics
    identical to threshold_in_force; 0 rows or >1 rows raise LookupError."""
    stmt = (
        sa.select(DecisionTable)
        .where(DecisionTable.table_name == table_name)
        .where(
            sa.or_(
                DecisionTable.effective_date.is_(None),
                DecisionTable.effective_date <= on_date,
            )
        )
        .where(
            sa.or_(
                DecisionTable.superseded_date.is_(None),
                DecisionTable.superseded_date > on_date,
            )
        )
    )
    rows = session.execute(stmt).scalars().all()
    if not rows:
        raise LookupError(
            f"no {table_name!r} decision table in force on {on_date.isoformat()} — "
            "flag as an open question, do not invent a determination"
        )
    if len(rows) > 1:
        raise LookupError(
            f"{len(rows)} {table_name!r} decision-table versions in force on "
            f"{on_date.isoformat()} — overlapping effective windows in the seed data"
        )
    return rows[0]


def _references_threshold(node: Any) -> bool:
    if isinstance(node, dict):
        if "threshold" in node and len(node) == 1:
            return True
        return any(_references_threshold(v) for v in node.values())
    if isinstance(node, list):
        return any(_references_threshold(v) for v in node)
    return False


def _rule_uses_thresholds(rule: DecisionRule, aliases: dict) -> bool:
    if _references_threshold(rule.when_ast):
        return True
    template = rule.reason_template or ""
    return any("{" + alias + "}" in template for alias in aliases)


class _Ctx:
    def __init__(self, inputs: dict):
        self.inputs = inputs
        self.threshold_values: dict[str, Decimal] = {}

    def operand(self, node: Any, rule_key: str) -> Any:
        if isinstance(node, dict):
            if set(node) == {"input"}:
                key = node["input"]
                if key not in self.inputs:
                    raise ValueError(
                        f"rule {rule_key!r} references unknown input {key!r} — "
                        f"known inputs: {sorted(self.inputs)}"
                    )
                return self.inputs[key]
            if set(node) == {"threshold"}:
                alias = node["threshold"]
                if alias not in self.threshold_values:
                    raise ValueError(
                        f"rule {rule_key!r} references threshold alias {alias!r} "
                        "not declared in the table's threshold_context"
                    )
                return self.threshold_values[alias]
            raise ValueError(f"rule {rule_key!r} has a malformed operand: {node!r}")
        return node  # JSON literal


def _matches(node: Any, ctx: _Ctx, rule_key: str) -> bool:
    if node is None:
        return True  # an always-rule (the table's default row)
    if not isinstance(node, dict):
        raise ValueError(f"rule {rule_key!r} has a malformed when_ast node: {node!r}")
    if "all" in node:
        return all(_matches(child, ctx, rule_key) for child in node["all"])
    if "any" in node:
        return any(_matches(child, ctx, rule_key) for child in node["any"])
    op = node.get("op")
    if op in _UNARY:
        return _UNARY[op](ctx.operand(node["lhs"], rule_key))
    if op in _COMPARISONS:
        lhs = ctx.operand(node["lhs"], rule_key)
        rhs = ctx.operand(node["rhs"], rule_key)
        if op in _ORDERED and (lhs is None or rhs is None):
            raise ValueError(
                f"rule {rule_key!r} compares against None with {op!r} — "
                "guard with an is_null rule first"
            )
        return _COMPARISONS[op](lhs, rhs)
    raise ValueError(f"rule {rule_key!r} uses unknown predicate op {op!r}")


def evaluate_table(
    session: Session,
    table_name: str,
    on_date: datetime.date,
    inputs: dict,
) -> TableEvaluation:
    table = table_in_force(session, table_name, on_date)
    rules = (
        session.execute(
            sa.select(DecisionRule)
            .where(DecisionRule.decision_table_id == table.decision_table_id)
            .order_by(DecisionRule.rule_order)
        )
        .scalars()
        .all()
    )
    result = TableEvaluation(
        table_name=table.table_name,
        version=table.version,
        decision_table_id=table.decision_table_id,
        outcome=dict(table.initial_outcome or {}),
    )
    ctx = _Ctx(inputs)
    aliases: dict = table.threshold_context or {}
    resolution = table.threshold_resolution
    if resolution not in ("eager", "on_first_use"):
        raise ValueError(
            f"table {table_name!r} has unknown threshold_resolution {resolution!r}"
        )
    resolved = False

    def _resolve_thresholds() -> None:
        nonlocal resolved
        for alias, rule_name in aliases.items():
            row = threshold_in_force(session, rule_name, on_date)
            result.threshold_ids[alias] = row.threshold_id
            result.threshold_values[alias] = row.value
            ctx.threshold_values[alias] = row.value
            caveat = status_caveat(row)
            if caveat:
                result.caveats.append(caveat)
        resolved = True

    if aliases and resolution == "eager":
        _resolve_thresholds()

    for rule in rules:
        if aliases and not resolved and _rule_uses_thresholds(rule, aliases):
            # on_first_use: the whole context resolves as a unit the moment any
            # rule needs it, so every declared threshold's caveat rides the
            # result together — matching the coded services' behavior.
            _resolve_thresholds()
        if not _matches(rule.when_ast, ctx, rule.rule_key):
            continue
        result.fired_rules.append(rule.rule_key)
        if rule.outcome:
            result.outcome.update(rule.outcome)
        if rule.reason_template:
            result.reasons.append(
                _render_template(rule.reason_template, rule.rule_key,
                                 {**inputs, **ctx.threshold_values})
            )
        if rule.status is not None and rule.status != ThresholdStatus.FINAL_RULE:
            result.caveats.append(
                f"rule {rule.rule_key!r} of decision table {table.table_name} "
                f"v{table.version} is encoded from a {rule.status.value} authority, "
                "not settled final regulation — verify before external reliance "
                f"(source: {rule.source_citation})"
            )
        if rule.stop:
            break
    return result
