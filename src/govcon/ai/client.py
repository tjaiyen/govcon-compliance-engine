"""LLM client boundary — the injectable seam.

The kernel talks to a provider-agnostic ``LLMClient`` Protocol. ``AnthropicClient``
wraps the Anthropic SDK per the claude-api guidance (default claude-opus-4-8,
adaptive thinking, no budget_tokens/temperature, stream on large output). Tests
inject a ``FakeLLMClient`` (in tests/) exactly as ``create_app(session_factory=…)``
injects a session — so AI behavior is tested deterministically without a network
call.

Normalized response shape (provider-agnostic): a concatenated ``text`` plus a
list of ``ToolUse`` requests, the ``stop_reason``, and token usage. The loop
reconstructs the Anthropic-format assistant turn from this, so the same normalized
object serves both the real client and the fake.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

#: Default model for the AI layer (claude-api guidance: opus-tier for app code).
#: Override with GOVCON_AI_MODEL.
DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_MAX_TOKENS = 4096


@dataclass(frozen=True)
class ToolUse:
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tool_uses: list[ToolUse] = field(default_factory=list)
    stop_reason: str = "end_turn"
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = DEFAULT_MODEL


@runtime_checkable
class LLMClient(Protocol):
    def create(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMResponse: ...


class AnthropicClient:
    """Real client over the Anthropic SDK. Constructed only when the optional
    ``ai`` extra is installed AND an API key is present; otherwise the AI
    endpoints report unavailable (the engine still runs)."""

    def __init__(self, *, model: str | None = None, api_key: str | None = None):
        try:
            import anthropic
        except ModuleNotFoundError as exc:  # pragma: no cover - optional extra
            raise RuntimeError(
                "the AI layer needs the ai extra: `uv sync --extra ai` (anthropic)"
            ) from exc
        self._model = model or os.environ.get("GOVCON_AI_MODEL", DEFAULT_MODEL)
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )

    def create(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMResponse:  # pragma: no cover - exercised by the @live smoke test only
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools or [],
            thinking={"type": "adaptive"},
        )
        text_parts, tool_uses = [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_uses.append(ToolUse(id=block.id, name=block.name, input=dict(block.input)))
        return LLMResponse(
            text="".join(text_parts),
            tool_uses=tool_uses,
            stop_reason=resp.stop_reason,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            model=self._model,
        )


def default_client_or_none() -> LLMClient | None:
    """Build an AnthropicClient if the extra + key are present, else None (the
    AI endpoints then report unavailable — the engine runs unchanged)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        return AnthropicClient()
    except RuntimeError:
        return None
