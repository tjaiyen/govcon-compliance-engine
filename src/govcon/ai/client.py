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


# ----------------------------------------------------------- local (real-data)
# Real-data mode (GOVCON_DATA_MODE=real) is LOCAL-ONLY: real contract data is
# processed by a local model (Ollama) and NEVER sent to an external service. The
# gate (govcon.ai.gate.assert_data_mode) refuses real data to any non-local
# client, so a misconfiguration fails closed rather than leaking data to the cloud.
# The tool stays advisory and synthetic-by-default; this is a bring-your-own-data,
# runs-entirely-on-your-machine capability, not a certification or system-of-record.

def _to_ollama_tools(tools: list[dict]) -> list[dict]:
    """Anthropic tool defs → Ollama/OpenAI function tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            },
        }
        for t in tools
    ]


def _to_ollama_messages(messages: list[dict]) -> list[dict]:
    """The loop's Anthropic-style messages → Ollama chat messages: assistant
    tool_use blocks become ``tool_calls``; tool_result blocks become ``tool``
    messages; string content passes through."""
    out: list[dict] = []
    for msg in messages:
        role, content = msg["role"], msg["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        text_parts, tool_calls, tool_results = [], [], []
        for block in content:
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_calls.append(
                    {"function": {"name": block["name"], "arguments": block.get("input", {})}}
                )
            elif btype == "tool_result":
                tool_results.append(block.get("content", ""))
        if role == "assistant":
            m: dict = {"role": "assistant", "content": "".join(text_parts)}
            if tool_calls:
                m["tool_calls"] = tool_calls
            out.append(m)
        else:  # a user turn — may carry tool results and/or text
            for tr in tool_results:
                out.append({"role": "tool", "content": tr})
            if text_parts:
                out.append({"role": "user", "content": "".join(text_parts)})
    return out


def _parse_ollama(data: dict, model: str) -> LLMResponse:
    """Ollama /api/chat response → the normalized LLMResponse the loop expects."""
    import json as _json

    msg = data.get("message") or {}
    tool_uses = []
    for i, tc in enumerate(msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        args = fn.get("arguments", {})
        if isinstance(args, str):  # some models emit a JSON string
            try:
                args = _json.loads(args)
            except ValueError:
                args = {}
        tool_uses.append(
            ToolUse(id=f"call_{i}", name=fn.get("name", ""),
                    input=args if isinstance(args, dict) else {})
        )
    return LLMResponse(
        text=msg.get("content", "") or "",
        tool_uses=tool_uses,
        stop_reason="tool_use" if tool_uses else "end_turn",
        input_tokens=int(data.get("prompt_eval_count", 0) or 0),
        output_tokens=int(data.get("eval_count", 0) or 0),
        model=model,
    )


class LocalClient:
    """A LOCAL model client (Ollama) for real-data mode. ``is_local`` is the flag
    the gate checks before allowing real data through — real data is processed
    here, on the machine, and never transmitted to an external service."""

    is_local = True

    def __init__(self, *, model: str | None = None, base_url: str | None = None):
        self._model = model or os.environ.get("GOVCON_OLLAMA_MODEL", "llama3.1")
        self._base = (
            base_url or os.environ.get("GOVCON_OLLAMA_URL", "http://localhost:11434")
        ).rstrip("/")

    def _post(self, path: str, payload: dict) -> dict:
        import json as _json
        import urllib.request

        req = urllib.request.Request(
            self._base + path,
            data=_json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - localhost only
            return _json.loads(resp.read().decode())

    def create(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMResponse:
        payload = {
            "model": self._model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, *_to_ollama_messages(messages)],
            "tools": _to_ollama_tools(tools or []),
            "options": {"num_predict": max_tokens},
        }
        return _parse_ollama(self._post("/api/chat", payload), self._model)


def build_llm_client() -> LLMClient | None:
    """Pick the client by data mode. synthetic → the cloud AnthropicClient (if
    configured); real → the LOCAL Ollama client (real data never leaves the
    machine); anything else → None (fail closed). The gate re-checks the pairing
    at request time, so real data can never reach a cloud client even if
    misconfigured here."""
    from govcon.ai.gate import data_mode

    mode = data_mode()
    if mode == "synthetic":
        return default_client_or_none()
    if mode == "real":
        return LocalClient()  # constructed lazily; connection happens on create()
    return None
