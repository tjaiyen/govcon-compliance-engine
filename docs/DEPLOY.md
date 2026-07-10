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
| `GOVCON_DATA_MODE` | `synthetic` | Any other value fails the AI gate closed (no AI on non-synthetic data). |
| `GOVCON_DB_URL` | `sqlite:///govcon.db` | SQLite (default) or `postgresql+psycopg://…` (needs the `postgres` extra). |
| `ANTHROPIC_API_KEY` | — | Enables the `/api/ask` assistant (with the `ai` extra). |
| `GOVCON_AI_MODEL` | `claude-opus-4-8` | Model for the assistant. |
| `GOVCON_AI_MAX_USD` | `0.50` | Hard per-request USD ceiling for `/api/ask`. |
| `GOVCON_AI_RATE_LIMIT` / `_WINDOW_S` | `30` / `60` | `/api/ask` requests per window per client IP → 429. |
| `GOVCON_API_TOKEN` | — | When set, every `/api/*` request must send `Authorization: Bearer <token>`. A shared-secret gate, **NOT an identity provider** — per-user auth is Phase 5. |
| `GOVCON_CORS_ORIGINS` | — | Comma-separated allow-list; empty = same-origin only. |

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
