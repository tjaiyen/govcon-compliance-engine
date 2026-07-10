# AI interaction layer — grounded assistant over the deterministic engine

## The principle: interface, not decider

The engine is a deterministic, grounded compliance tool: every determination returns
`reasons` + `caveats` + `provenance` + `source_citation`. The AI layer is an **interface**
over that — it translates plain English to and from the engine's structured inputs and
explains results. **It never makes a compliance determination.** The structured
determination is the authoritative, audited fact; the AI prose is an advisory rendering,
verified to cite only engine-produced values and *withheld* when it cannot be verified.

No LLM call is ever on the determination path (CHECK constraints, triggers, decision
tables stay pure). Same inputs → same determination, regardless of whether an AI ran.

## How it works (`src/govcon/ai/`)

1. **`registry.py`** — the engine's pure services adapted into Claude `tool_use` tools.
   Each tool's `run(session, input)` mirrors the corresponding `/api/*` endpoint: builds a
   transient (unsaved) model object, calls the real service, returns its grounding payload.
   No tool writes; no tool computes a determination the model could fabricate.
2. **`loop.py`** — one shared tool-use loop. Wraps the user's question in `<user_input>`
   (untrusted DATA, never instructions — B13), calls the model, executes every requested
   tool via `dispatch.py` under the real DB session, feeds results back, repeats.
3. **`dispatch.py`** — runs tools and records every returned value/citation into a
   **grounding ledger** (including numbers embedded in reason strings).
4. **`grounding.py`** — after the loop, extracts every dollar figure + citation from the
   model's prose and asserts each traces to the ledger. An ungrounded number is a **caught
   error**. On violation the prose is withheld and the determination returned alone
   (degrade, never 500).
5. **`gate.py`** — synthetic-only, fail-closed. `GOVCON_DATA_MODE` (default `synthetic`);
   any other value refuses the AI. Enforced at the HTTP boundary AND kernel entry.
6. **`cost.py`** — every model call is token+cost logged (ai-ml.md) and totalled in the
   response.

## Endpoints

- `POST /api/ask` `{question}` → `{ai_available, prose, determinations[], grounding{verified,
  violations}, cost, notice}`. The determination is **always** returned beside the prose.
  If `llm_client` is not configured, `{ai_available: false}` — the engine runs unchanged.

(Phase B–D add `/api/tutor`, `/api/draft-rule`, `/api/draft-narrative` on the same kernel.)

## Running it

```bash
uv sync --extra ai
export ANTHROPIC_API_KEY=sk-...
export GOVCON_DATA_MODE=synthetic     # default; the gate refuses anything else
uv run govcon serve --demo            # "AI assistant: ON (synthetic-data only)"
```

Default model `claude-opus-4-8` (override `GOVCON_AI_MODEL`). Without the extra or the key,
the AI endpoints report unavailable and everything else works.

## Testing

Deterministic: a `FakeLLMClient` (scripted tool_use turns → canned text) is injected exactly
as `session_factory` is. Tests assert on dispatch, grounding, gating, and cost — never on
prose. `tests/ai/test_ai_live_smoke.py` hits the real API once (skipped without a key) and
asserts only that a determination came back grounded.

## Future — real-data mode

Real data would route to a **local model** (Ollama) behind the same `LLMClient` Protocol so
nothing leaves the machine; the gate flips from "refuse" to "route local." v1 is
Claude-API-synthetic-only; the Protocol is the drop-in seam.
