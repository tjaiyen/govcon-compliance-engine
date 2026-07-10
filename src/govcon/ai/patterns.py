"""Thin per-pattern wrappers over the shared loop. ``ask`` (Pattern 1) and
``tutor`` (Pattern 2) differ ONLY by system prompt + tool subset — the loop,
grounding, gate, and withhold-on-fail discipline are identical."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from govcon.ai.client import LLMClient
from govcon.ai.cost import CostLog
from govcon.ai.gate import assert_synthetic
from govcon.ai.loop import AITurnResult, run_conversation
from govcon.ai.prompts import DEFAULT_TUTOR_PERSONA, system_for
from govcon.ai.registry import ASK_TOOLS, DRAFT_RULE_TOOLS, TUTOR_TOOLS

#: Determination-only fallback prose when grounding fails — never ship
#: unverified numbers; point the user at the structured determination.
_UNVERIFIED_NOTICE = (
    "The explanation could not be fully verified against the engine's output, so "
    "it has been withheld. Rely on the structured determination below — it is the "
    "authoritative result."
)


def ask(
    client: LLMClient,
    session: Session,
    question: str,
    *,
    actor: str = "unknown",
    workspace: str = "default",
    max_usd: Decimal | None = None,
) -> AITurnResult:
    """Conversational query (Pattern 1). Gate synthetic-only, run the grounded
    loop, and withhold prose that fails grounding (the determination stays)."""
    assert_synthetic()  # kernel-level gate (defence in depth vs the HTTP gate)
    cost = CostLog(pattern="ask", actor=actor, workspace=workspace, max_usd=max_usd)
    result = run_conversation(
        client,
        session,
        system=system_for("ask"),
        tool_names=ASK_TOOLS,
        user_text=question,
        cost_log=cost,
    )
    if result.grounding is not None and not result.grounding.verified:
        result.prose = _UNVERIFIED_NOTICE
    return result


def tutor(
    client: LLMClient,
    session: Session,
    question: str,
    *,
    persona: str = DEFAULT_TUTOR_PERSONA,
    actor: str = "unknown",
    workspace: str = "default",
    max_usd: Decimal | None = None,
) -> AITurnResult:
    """AI tutor (Pattern 2): same grounded loop as ``ask``, taught at the depth
    the ``persona`` calls for. Grounding still governs — an unverified number is
    withheld and the authoritative determination stands."""
    assert_synthetic()  # kernel-level gate (defence in depth vs the HTTP gate)
    cost = CostLog(pattern="tutor", actor=actor, workspace=workspace, max_usd=max_usd)
    result = run_conversation(
        client,
        session,
        system=system_for("tutor", persona=persona),
        tool_names=TUTOR_TOOLS,
        user_text=question,
        cost_log=cost,
    )
    if result.grounding is not None and not result.grounding.verified:
        result.prose = _UNVERIFIED_NOTICE
    return result


def draft_rule(
    client: LLMClient,
    session: Session,
    instruction: str,
    *,
    actor: str = "unknown",
    workspace: str = "default",
    max_usd: Decimal | None = None,
) -> AITurnResult:
    """Rule-authoring (Pattern 3): draft a decision-table rule from a described
    regulatory change, validate it structurally, and return it for a HUMAN
    migration. Writes nothing and applies nothing (B53) — the tool subset has no
    write/evaluate tool. Grounding still withholds ungrounded explanatory prose."""
    assert_synthetic()  # kernel-level gate (defence in depth vs the HTTP gate)
    cost = CostLog(pattern="draft_rule", actor=actor, workspace=workspace, max_usd=max_usd)
    result = run_conversation(
        client,
        session,
        system=system_for("draft_rule"),
        tool_names=DRAFT_RULE_TOOLS,
        user_text=instruction,
        cost_log=cost,
    )
    if result.grounding is not None and not result.grounding.verified:
        result.prose = _UNVERIFIED_NOTICE
    return result


def draft_narrative(
    client: LLMClient,
    session: Session,
    instruction: str,
    *,
    actor: str = "unknown",
    workspace: str = "default",
    max_usd: Decimal | None = None,
) -> AITurnResult:
    """Narrative drafter (Pattern 4): a memo grounded ENTIRELY in the engine's
    computed numbers. Strictest grounding — an ungrounded figure withholds the
    memo (the authoritative determination is still returned). A synthetic,
    advisory draft, never a filing."""
    assert_synthetic()  # kernel-level gate (defence in depth vs the HTTP gate)
    cost = CostLog(pattern="draft_narrative", actor=actor, workspace=workspace, max_usd=max_usd)
    result = run_conversation(
        client,
        session,
        system=system_for("draft_narrative"),
        tool_names=ASK_TOOLS,
        user_text=instruction,
        cost_log=cost,
    )
    if result.grounding is not None and not result.grounding.verified:
        result.prose = _UNVERIFIED_NOTICE
    return result
