# Deploying the workbench (ops hardening — still advisory, synthetic-data only)

This is a **decision-support & training tool on synthetic data**, not a certified
system and not multi-tenant SaaS (real per-user auth, real-data mode, and RLS are
the deliberately-excluded Phase-5 liability line). The guidance below hardens a
*single-org, behind-the-firewall* deployment; it does not change that posture.

## Run it

```bash
docker build -t govcon-workbench .
docker run -p 8000:8000 -v govcon-data:/data \
  -e GOVCON_DATA_MODE=synthetic \
  govcon-workbench
```

Or without Docker:

```bash
uv sync --extra ai
alembic upgrade head
govcon serve --host 127.0.0.1 --port 8000     # add --workspaces for routing
```

## Environment (all optional; safe defaults)

| Var | Default | Effect |
|---|---|---|
| `GOVCON_DATA_MODE` | `synthetic` | `synthetic` (cloud or local model) · `real` → **local-only**: the AI runs on a LOCAL model (Ollama) and the gate REFUSES real data to any cloud/absent client, so real data never leaves the machine. Any other value fails closed. Still advisory / not certified in every mode. |
| `GOVCON_OLLAMA_URL` / `GOVCON_OLLAMA_MODEL` | `http://localhost:11434` / `llama3.1` | The local model for real-data mode. Needs Ollama running with a tool-capable model; no cloud key involved. |
| `GOVCON_DB_URL` | `sqlite:///govcon.db` | SQLite (default) or `postgresql+psycopg://…` (needs the `postgres` extra). |
| `ANTHROPIC_API_KEY` | — | Enables the cloud `/api/ask` assistant on SYNTHETIC data (with the `ai` extra). Never used in real-data mode. |
| `GOVCON_AI_MODEL` | `claude-opus-4-8` | Cloud model for the assistant (synthetic mode). |
| `GOVCON_AI_MAX_USD` | `0.50` | Hard per-request USD ceiling for `/api/ask`. |
| `GOVCON_AI_RATE_LIMIT` / `_WINDOW_S` | `30` / `60` | `/api/ask` requests per window per client IP → 429. |
| `GOVCON_API_TOKEN` | — | Coarse shared-secret edge gate: when set (and JWT auth is off), every `/api/*` request must send `Authorization: Bearer <token>` (constant-time compared). **NOT an identity provider.** Superseded by JWT auth when that is configured. |
| `GOVCON_CORS_ORIGINS` | — | Comma-separated allow-list; empty = same-origin only. |

### Real per-user authentication (optional; needs the `auth` extra — `uv sync --extra auth`)

Off by default: with none of these set the audit-trail actor is *asserted* from
the `X-Govcon-User` header. Set **exactly one** signing source to turn on real
JWT bearer auth — every gated `/api/*` call then requires a valid token and the
audit actor becomes a verified `auth:<sub>` (the header is ignored). `/health`,
`/`, and `/api/about` stay public. **Auth ≠ real-data:** this does not touch
`GOVCON_DATA_MODE` — the tool stays advisory on synthetic data.

| Var | Default | Effect |
|---|---|---|
| `GOVCON_JWT_SECRET` | — | Symmetric HS256 shared secret (one signing source). |
| `GOVCON_JWT_PUBLIC_KEY` | — | Static asymmetric PEM public key (RS*/ES*). |
| `GOVCON_JWT_JWKS_URL` | — | IdP JWKS endpoint (`https://` only); keys cached, fail-closed. |
| `GOVCON_JWT_ISSUER` / `GOVCON_JWT_AUDIENCE` | — | **Required** when auth is on; `iss`/`aud` validated on every token. |
| `GOVCON_JWT_ALGS` | per key type | Override accepted algs; must stay within the key's family (algorithm-confusion guard). |
| `GOVCON_JWT_LEEWAY_S` | `60` | Clock-skew tolerance for `exp`/`nbf`/`iat`. |
| `GOVCON_JWT_REQUIRED_SCOPE` / `GOVCON_JWT_SCOPE_CLAIM` | — / `scope` | If set, a token missing the scope gets `403` on `/api/ask` (the expensive route); read-only determinations stay open to any valid user. |

Setting more than one signing source, or omitting `iss`/`aud`, makes the app
refuse to boot — a misconfiguration fails loud, never silently open.

## TLS / reverse proxy

The app speaks plain HTTP on localhost. **Terminate TLS at a reverse proxy**
(nginx/Caddy/Cloudflare) in front of it; do not expose it directly. Example
nginx location:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header X-Request-Id $request_id;
    # add HSTS at the TLS edge:
    add_header Strict-Transport-Security "max-age=31536000" always;
}
```

The app already sets `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
and a strict `Content-Security-Policy` (the UI is fully self-contained, so CSP
`default-src 'self'` holds). HSTS belongs at the TLS edge, above.

## Health & observability

- `GET /health` → `{status, db, ai}` for liveness/readiness probes.
- Every response carries `X-Request-Id` (generated if absent); it is bound into
  the structlog context, so logs across a request correlate. AI calls log
  `llm_call` with token+cost per actor/workspace.

## Scale notes

- SQLite runs in WAL mode (multi-reader / single-writer). For real concurrency
  use Postgres (`GOVCON_DB_URL=postgresql+psycopg://…`, `--extra postgres`); the
  audit chain there is serialized by a transaction-scoped advisory lock.
- `serve --workspaces` isolates each workspace in its own database file
  (physical isolation; see `docs/POSTGRES.md §4` for the shared-DB RLS design
  that would be needed only if the Phase-5 line were ever crossed).
