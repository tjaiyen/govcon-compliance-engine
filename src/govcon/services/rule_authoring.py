"""Rule-authoring VALIDATION (enterprise vision Phase 3, AI Pattern 3).

The AI rule-authoring assistant drafts a candidate ``decision_rules`` row from a
described regulatory change. This module checks that draft **structurally** —
does its ``when_ast`` parse against the decision-engine grammar, does its
reason_template only use simple ``{name}`` placeholders — and reports what
inputs/thresholds it references.

THE B53 HARD LINE (auto-apply is structurally impossible):
  * This module NEVER writes: no Session mutation, no `add`/`commit`, no import
    of Alembic or any migration API. It takes plain dicts and returns plain dicts.
  * It NEVER executes a rule: it walks the AST shape only (it does not call
    ``_matches``/``evaluate_table``). It imports just the grammar's operator
    *names* from decision_engine — never its evaluator.
  * Every result carries ``requires_human_migration: True`` — a draft is a
    proposal for a human-reviewed Alembic migration, never an applied change.
"""

from __future__ import annotations

import re

from govcon.services.decision_engine import COMPARISON_OPS, UNARY_OPS

_TEMPLATE_FIELD = re.compile(r"\{(\w+)\}")


def _validate_operand(node, *, path: str, errors: list[str], refs: dict) -> None:
    """An operand is a JSON literal or a single-key {input}/{threshold} dict."""
    if isinstance(node, dict):
        if set(node) == {"input"}:
            if not isinstance(node["input"], str) or not node["input"]:
                errors.append(f"{path}: 'input' must be a non-empty string")
            else:
                refs["inputs"].add(node["input"])
        elif set(node) == {"threshold"}:
            if not isinstance(node["threshold"], str) or not node["threshold"]:
                errors.append(f"{path}: 'threshold' must be a non-empty alias string")
            else:
                refs["thresholds"].add(node["threshold"])
        else:
            errors.append(
                f"{path}: malformed operand {node!r} — expected a literal or a "
                "single-key {{'input': ...}} / {{'threshold': ...}}"
            )
    # a non-dict (str/int/float/bool/None) is a valid JSON literal operand


def _validate_node(node, *, path: str, errors: list[str], refs: dict) -> None:
    if node is None:
        return  # an always-rule (the table's default row) — valid
    if not isinstance(node, dict):
        errors.append(f"{path}: node must be an object or null, got {node!r}")
        return
    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        children = node[key]
        if not isinstance(children, list) or not children:
            errors.append(f"{path}.{key}: must be a non-empty list of sub-conditions")
            return
        for i, child in enumerate(children):
            _validate_node(child, path=f"{path}.{key}[{i}]", errors=errors, refs=refs)
        return
    op = node.get("op")
    if op in UNARY_OPS:
        if "lhs" not in node:
            errors.append(f"{path}: unary op {op!r} requires 'lhs'")
        else:
            _validate_operand(node["lhs"], path=f"{path}.lhs", errors=errors, refs=refs)
    elif op in COMPARISON_OPS:
        for side in ("lhs", "rhs"):
            if side not in node:
                errors.append(f"{path}: comparison {op!r} requires '{side}'")
            else:
                _validate_operand(node[side], path=f"{path}.{side}", errors=errors, refs=refs)
    else:
        errors.append(
            f"{path}: unknown predicate op {op!r} — valid ops: "
            f"{sorted(UNARY_OPS | COMPARISON_OPS)} (or use 'all'/'any')"
        )


def validate_when_ast(node) -> tuple[list[str], dict]:
    """Structurally validate a when_ast. Returns (errors, references) — no
    execution, no side effects. Empty errors ⇒ the AST parses against the grammar
    (it is NOT evaluated against any inputs; that is the engine's job at runtime)."""
    errors: list[str] = []
    refs = {"inputs": set(), "thresholds": set()}
    _validate_node(node, path="when", errors=errors, refs=refs)
    return errors, {"inputs": sorted(refs["inputs"]), "thresholds": sorted(refs["thresholds"])}


def validate_reason_template(template) -> list[str]:
    if template is None:
        return []
    if not isinstance(template, str):
        return ["reason_template must be a string or null"]
    # strip every well-formed {word} placeholder; any brace left over is unmatched
    residue = _TEMPLATE_FIELD.sub("", template)
    if "{" in residue or "}" in residue:
        return [f"reason_template has an unmatched brace: {template!r}"]
    return []


def validate_draft_rule(rule: dict) -> dict:
    """Validate a whole draft decision-rule dict. Pure: returns a report, writes
    nothing, executes nothing. ``requires_human_migration`` is always True — a
    valid draft still becomes a human-reviewed migration, never an auto-apply."""
    errors: list[str] = []
    if not isinstance(rule, dict):
        return {
            "valid": False,
            "errors": [f"draft rule must be an object, got {type(rule).__name__}"],
            "references": {"inputs": [], "thresholds": []},
            "requires_human_migration": True,
        }
    rule_key = rule.get("rule_key")
    if not isinstance(rule_key, str) or not rule_key.strip():
        errors.append("rule_key: required non-empty string")
    if "outcome" in rule and rule["outcome"] is not None and not isinstance(rule["outcome"], dict):
        errors.append("outcome: must be an object (or omitted/null)")
    if "stop" in rule and not isinstance(rule["stop"], bool):
        errors.append("stop: must be a boolean")
    ast_errors, references = validate_when_ast(rule.get("when_ast"))
    errors.extend(ast_errors)
    errors.extend(validate_reason_template(rule.get("reason_template")))
    return {
        "valid": not errors,
        "errors": errors,
        "references": references,
        "requires_human_migration": True,
    }
