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
2. **`loop.py`** — one shared tool-use loop, exposed two ways: `iter_conversation` is a
   **generator** that yields progress + determination events and returns the final result;
   `run_conversation` drains it for the non-streaming path. Wraps the user's question in
   `<user_input>` (untrusted DATA, never instructions — B13), calls the model, executes
   every requested tool via `dispatch.py` under the real DB session, feeds results back.
3. **`dispatch.py`** — runs tools and records every returned value/citation into a
   **grounding ledger** (including numbers embedded in reason strings).
4. **`grounding.py`** — after the loop, extracts every dollar figure + citation from the
   model's prose and asserts each traces to the ledger. An ungrounded number is a **caught
   error**. On violation the prose is withheld and the determination returned alone
   (degrade, never 500).
5. **`gate.py`** — synthetic-only, fail-closed. `GOVCON_DATA_MODE` (default `synthetic`);
   any other value refuses the AI. Enforced at the HTTP boundary AND kernel entry.
6. **`patterns.py`** — one `_pattern_config` (system prompt + tool subset per pattern) and
   one `run_pattern` back all four patterns; `stream_pattern` is the streaming variant over
   the same loop. Both apply the same gate + grounding + withhold-on-ungrounded.
7. **`cost.py`** — every model call is token+cost logged (ai-ml.md) and totalled in the
   response; a per-request USD ceiling (`GOVCON_AI_MAX_USD`) bounds spend.

## The four patterns (one kernel, four surfaces)

They differ ONLY by system prompt + tool subset + endpoint:

| Pattern | Endpoint | What it does | Guardrail highlight |
|---|---|---|---|
| Ask | `POST /api/ask` `{question}` | plain-English Q&A | grounded prose beside the determination |
| Tutor | `POST /api/tutor` `{question, persona}` | teaches at a persona's depth (newcomer…auditor) | depth never changes the determination |
| Rule-authoring | `POST /api/draft-rule` `{instruction}` | drafts a decision-table rule + validates it structurally | **auto-apply structurally impossible** (see below) |
| Narrative | `POST /api/draft-narrative` `{instruction}` | a memo grounded entirely in computed numbers | strictest grounding; SYNTHETIC "not for filing" banner |

Every response returns the authoritative determination(s) beside the prose, plus
`grounding{verified, violations}` and `cost`. If `llm_client` is not configured,
`{ai_available: false}` — the engine runs unchanged.

### Rule-authoring can apply nothing (B53)

The rule-authoring pattern drafts a rule for a **human-reviewed migration** — never an
applied change. This is structural, not a policy:

- The validator (`services/rule_authoring.py`) parses a proposed `when_ast` against the
  engine grammar and checks the reason template. It **never executes** a rule (no
  `_matches`/`evaluate_table`), **never writes** (no Session mutation), and **never imports
  Alembic** — it imports only the grammar's operator *names*.
- `DRAFT_RULE_TOOLS` has **no write tool and no evaluate tool**.
- Every response carries `requires_human_migration: true`; a test asserts the
  `decision_tables`/`decision_rules` row counts are unchanged after a draft.

## Authentication (`src/govcon/api/auth.py`)

Optional, env-gated per-user JWT auth (`uv sync --extra auth`). Off by default: the audit
actor is asserted from `X-Govcon-User`. Configure exactly one signing source
(`GOVCON_JWT_SECRET` | `GOVCON_JWT_PUBLIC_KEY` | `GOVCON_JWT_JWKS_URL`, plus
`GOVCON_JWT_ISSUER`/`AUDIENCE`) and every gated `/api/*` requires a valid bearer token; the
audit actor becomes a verified `auth:<sub>` and the header is ignored. Algorithm-confusion
defense (algs from key type, never the token header), `exp`/`nbf`/`iss`/`aud` validated,
JWKS fail-closed, fail-closed on any error. An optional `GOVCON_JWT_REQUIRED_SCOPE` gates
the expensive AI routes (ask/tutor/draft-rule/draft-narrative) → 403. **Auth ≠ real-data:**
it does not touch the synthetic gate. `/health`, `/`, and `/api/about` stay public. See
[DEPLOY.md](DEPLOY.md) for the full env table.

## Streaming (SSE)

`?stream=1` on `/api/ask`, `/api/tutor`, `/api/draft-narrative` returns a
`text/event-stream` with the SAME rate limit + synthetic gate + USD ceiling as the JSON
path. Events: `status` → `determination` (one per tool as it resolves) → `grounding` →
`prose` → `cost` → `done`. It streams over the exact same loop as the batch path
(`iter_conversation`), so a streamed answer cannot diverge from a batch one. The workbench
Ask + Tutor cards consume it via `fetch` + a `ReadableStream` reader.

### Why this is *event*-level, not token-level (a deliberate boundary)

Token-level streaming — the final prose appearing word-by-word from Claude — is **not
built, on purpose.** It conflicts with the layer's core safety property: `grounding.py`
verifies the *complete* prose before it is shown, and withholds it if any number is
ungrounded. Streaming tokens live would surface unverified figures (a hallucinated
`$500,000,000`) before the verifier can withhold them — even transiently, that breaks the
"never present an unverified value" guarantee. Buffering tokens until grounding passes
would restore safety but add no perceived-latency benefit over the current behavior. So the
layer streams what is *already trustworthy the moment it exists* — the engine's
determinations — and reveals the AI prose only once grounded. That is the correct trade for
a compliance tool.

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
as `session_factory` is. Tests assert on dispatch, grounding, gating, cost, streaming
events, and the B53 no-write proofs — never on prose. Browser tests (`tests/frontend`, the
`frontend` extra) drive the real UI incl. the streaming Ask/Tutor cards.
`tests/ai/test_ai_live_smoke.py` hits the real API once (skipped without a key) and asserts
only that a determination came back grounded.

## Real-data mode — local-only (shipped)

`GOVCON_DATA_MODE=real` routes to a **local model (Ollama)** behind the same `LLMClient`
Protocol (`LocalClient`, `is_local = True`), so real contract data is processed on the
machine and **never leaves it**. The gate (`assert_data_mode`) enforces the pairing: real
mode is allowed ONLY through a local client and **refuses real data to any cloud/absent
client** — so a misconfiguration fails closed instead of leaking data to the API. Configure
`GOVCON_OLLAMA_URL` / `GOVCON_OLLAMA_MODEL` (needs Ollama running with a tool-capable model).

**Authentication and real-data stay separate switches**, and the tool is advisory in every
combination: real-data mode is **NOT** a certification, **NOT** a system-of-record, and
**NOT** for regulatory filing — the workbench shows a loud red REAL-DATA banner, `/api/about`
states it, and the determination engine stays deterministic + grounded + audited. Still
excluded (the Phase-5 liability line): multi-tenant RLS, certification, and any system-of-
record posture. The `LLMClient` Protocol is the seam that made the cloud→local switch a
drop-in, with no change to the loop, grounding, or the four patterns.
