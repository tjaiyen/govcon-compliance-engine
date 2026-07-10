"""Per-pattern wrappers over the shared loop. The four patterns (ask, tutor,
draft_rule, draft_narrative) differ ONLY by system prompt + tool subset — the
loop, grounding, gate, and withhold-on-fail discipline are identical, so they
resolve through one ``_pattern_config`` and one runner. ``stream_pattern`` adds
event-level streaming over the very same loop (SSE), so a streamed answer can
never diverge from the non-streamed one."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from govcon.ai.client import LLMClient
from govcon.ai.cost import CostLog
from govcon.ai.gate import assert_synthetic
from govcon.ai.loop import AITurnResult, iter_conversation, run_conversation
from govcon.ai.prompts import DEFAULT_TUTOR_PERSONA, system_for
from govcon.ai.registry import ASK_TOOLS, DRAFT_RULE_TOOLS, TUTOR_TOOLS

#: Determination-only fallback prose when grounding fails — never ship
#: unverified numbers; point the user at the structured determination.
_UNVERIFIED_NOTICE = (
    "The explanation could not be fully verified against the engine's output, so "
    "it has been withheld. Rely on the structured determination below — it is the "
    "authoritative result."
)

#: Patterns whose grounded prose is streamable over SSE.
STREAMABLE = frozenset({"ask", "tutor", "draft_narrative"})


def _pattern_config(pattern: str, persona: str | None = None) -> tuple[str, list[str]]:
    """(system prompt, tool subset) for a pattern — the ONE source both the sync
    runners and the streaming runner read, so a streamed answer uses the exact
    same prompt + tools as the non-streamed one."""
    if pattern == "tutor":
        return system_for("tutor", persona=persona or DEFAULT_TUTOR_PERSONA), TUTOR_TOOLS
    if pattern == "draft_rule":
        return system_for("draft_rule"), DRAFT_RULE_TOOLS
    if pattern == "draft_narrative":
        return system_for("draft_narrative"), ASK_TOOLS
    return system_for("ask"), ASK_TOOLS


def _withhold_if_ungrounded(result: AITurnResult) -> AITurnResult:
    if result.grounding is not None and not result.grounding.verified:
        result.prose = _UNVERIFIED_NOTICE
    return result


def run_pattern(
    client: LLMClient,
    session: Session,
    text: str,
    *,
    pattern: str,
    persona: str | None = None,
    actor: str = "unknown",
    workspace: str = "default",
    max_usd: Decimal | None = None,
) -> AITurnResult:
    """Gate synthetic-only, run the grounded loop for ``pattern``, and withhold
    prose that fails grounding (the determination always stays)."""
    assert_synthetic()  # kernel-level gate (defence in depth vs the HTTP gate)
    system, tools = _pattern_config(pattern, persona)
    cost = CostLog(pattern=pattern, actor=actor, workspace=workspace, max_usd=max_usd)
    result = run_conversation(
        client, session, system=system, tool_names=tools, user_text=text, cost_log=cost
    )
    return _withhold_if_ungrounded(result)


def stream_pattern(
    client: LLMClient,
    session: Session,
    text: str,
    *,
    pattern: str,
    persona: str | None = None,
    actor: str = "unknown",
    workspace: str = "default",
    max_usd: Decimal | None = None,
):
    """Event-level streaming (SSE) over the SAME loop as ``run_pattern``: yields
    the loop's progress + determination events as they resolve, then the final
    grounding / prose (withheld if ungrounded) / cost events. Same gate, same
    grounding, same withhold — a streamed answer cannot diverge from a batch one."""
    assert_synthetic()  # kernel-level gate (defence in depth vs the HTTP gate)
    system, tools = _pattern_config(pattern, persona)
    cost = CostLog(pattern=pattern, actor=actor, workspace=workspace, max_usd=max_usd)
    gen = iter_conversation(
        client, session, system=system, tool_names=tools, user_text=text, cost_log=cost
    )
    result: AITurnResult | None = None
    while True:
        try:
            yield next(gen)  # {"type": "status"} / {"type": "determination", ...}
        except StopIteration as stop:
            result = stop.value
            break
    _withhold_if_ungrounded(result)
    yield {
        "type": "grounding",
        "verified": bool(result.grounding and result.grounding.verified),
        "violations": result.grounding.violations if result.grounding else [],
    }
    yield {"type": "prose", "text": result.prose}
    yield {"type": "cost", "cost": result.cost.as_dict()}


# --- thin public wrappers (kept so app.py's `import ... as run_ask` etc. hold) ---
def ask(client, session, question, **kw) -> AITurnResult:
    """Conversational query (Pattern 1)."""
    return run_pattern(client, session, question, pattern="ask", **kw)


def tutor(client, session, question, *, persona: str = DEFAULT_TUTOR_PERSONA, **kw) -> AITurnResult:
    """AI tutor (Pattern 2): taught at the depth ``persona`` calls for."""
    return run_pattern(client, session, question, pattern="tutor", persona=persona, **kw)


def draft_rule(client, session, instruction, **kw) -> AITurnResult:
    """Rule-authoring (Pattern 3): draft + structurally validate a decision rule
    for a HUMAN migration. Writes nothing, applies nothing (B53)."""
    return run_pattern(client, session, instruction, pattern="draft_rule", **kw)


def draft_narrative(client, session, instruction, **kw) -> AITurnResult:
    """Narrative drafter (Pattern 4): a memo grounded entirely in computed
    numbers; strictest grounding, synthetic advisory draft."""
    return run_pattern(client, session, instruction, pattern="draft_narrative", **kw)
