"""A scripted FakeLLMClient — the AI-layer test seam.

Injected exactly as the app injects session_factory. The fake is a state
machine: it yields a pre-scripted sequence of LLMResponse objects (tool_use
turns then a final text turn), so tests assert on tool DISPATCH, GROUNDING, and
GATING — never on nondeterministic prose. It also records every call for the
"gate blocks before any LLM call" assertions.
"""

from __future__ import annotations

import pytest

from govcon.ai.client import LLMResponse, ToolUse


class FakeLLMClient:
    def __init__(self, script: list[LLMResponse]):
        self._script = list(script)
        self.calls: list[dict] = []

    def create(self, *, system, messages, tools, max_tokens=4096) -> LLMResponse:
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        if not self._script:
            return LLMResponse(text="(no more scripted turns)", stop_reason="end_turn",
                               input_tokens=1, output_tokens=1)
        return self._script.pop(0)


def tool_turn(*tool_uses: tuple[str, str, dict], text: str = "",
              input_tokens: int = 100, output_tokens: int = 20) -> LLMResponse:
    """A response that requests tools. Each arg is (id, name, input)."""
    return LLMResponse(
        text=text,
        tool_uses=[ToolUse(id=i, name=n, input=inp) for (i, n, inp) in tool_uses],
        stop_reason="tool_use",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def final_turn(text: str, input_tokens: int = 100, output_tokens: int = 40) -> LLMResponse:
    return LLMResponse(text=text, stop_reason="end_turn",
                       input_tokens=input_tokens, output_tokens=output_tokens)


@pytest.fixture()
def make_fake():
    return lambda script: FakeLLMClient(script)


@pytest.fixture()
def synthetic_mode(monkeypatch):
    monkeypatch.setenv("GOVCON_DATA_MODE", "synthetic")
