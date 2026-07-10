"""Thin per-pattern wrappers over the shared loop. Phase A ships ``ask``."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from govcon.ai.client import LLMClient
from govcon.ai.cost import CostLog
from govcon.ai.gate import assert_synthetic
from govcon.ai.loop import AITurnResult, run_conversation
from govcon.ai.prompts import system_for
from govcon.ai.registry import ASK_TOOLS

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
