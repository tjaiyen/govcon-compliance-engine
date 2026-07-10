# govcon-engine

[![CI](https://github.com/tjaiyen/govcon-compliance-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/tjaiyen/govcon-compliance-engine/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)

📐 **[Visual blueprint — what it does & how to run it](https://tjaiyen.github.io/govcon-compliance-engine/)**

A personal, local-first **GovCon cost-accounting compliance engine**: FAR
Part 31 allowability, SF 1408 structural checks, three-tier indirect rates,
ICS/ICE schedules, CAS/TINA dated thresholds, TINA sweeps, REA/CDA, Eichleay,
PBR monitoring, audit-response deadlines, and standard costing & variance
analysis — operating on **synthetic data only**, with an append-only,
SHA-256 hash-chained audit trail underneath everything.

**SYNTHETIC DATA — NOT FOR REGULATORY RELIANCE.** This is a learning and
portfolio build. It is not a certified accounting system, holds no real
contract or CUI/ITAR data, and is not a substitute for professional audit or
legal judgment. Run `govcon about` for the tool's own limitations statement.

## What it demonstrates

1. **Compliance at the edge** — every rule is enforced at write time, at two
   layers: a service that raises a typed, explanatory error, backed by a
   database trigger/constraint that refuses raw SQL. Direct/indirect
   segregation, append-only ledgers, contract-field immutability, the
   closed-period gate, the management-review sign-off — all of it.
2. **Dated regulatory thresholds, never scalars** — the 2026 NDAA resets
   (TINA $2.5M→$10M, CAS $7.5M→$35M/$50M→$100M) are `regulatory_thresholds`
   rows with effective dates, statuses (statute / proposed_rule /
   class_deviation / final_rule — surfaced, never presented as settled law),
   and source citations. The same $12M contract is modified CAS coverage
   awarded 2026-06-30 and none awarded 2026-07-01.
3. **Every number defends itself later** — rate calculations stamp their
   inputs (`reconstruct_run` reproduces any historical figure from the stamp
   alone), Eichleay claims store their inputs, TINA sweeps log every
   comparison including non-matches and the match method, and the audit
   trail's hash chain detects out-of-band edits (`govcon audit verify`).
4. **Penny-exact Decimal end to end** — a custom SQLAlchemy type keeps
   `decimal.Decimal` lossless on SQLite (plain NUMERIC round-trips through
   float); floats are rejected at bind time.
5. **A guided web workbench + a grounded AI layer** — `govcon serve` puts a
   self-contained, WCAG&nbsp;2.2&nbsp;AA UI over the same pure services. Over it,
   four AI surfaces — *ask*, *tutor* (taught at your persona depth), *draft-rule*,
   *draft-narrative* — call the deterministic engine **as tools** and never
   decide: a grounding verifier withholds any figure or citation the engine
   didn't return, the authoritative determination is always shown beside the
   prose, and it streams over SSE. Rule-drafting can apply nothing — it only
   proposes a human-reviewed migration (validated structurally, never executed).
6. **Enterprise posture, still advisory** — a verified dual-backend port to
   PostgreSQL (26 plpgsql triggers, advisory-locked audit chain), per-workspace
   database isolation, rules-as-data decision tables (behaviour proven identical
   to the coded logic against a frozen oracle), and optional per-user JWT auth so
   every audit row is attributed to a **cryptographically verified** actor.
   Authentication and real data stay separate switches — synthetic-data-only
   throughout; no real-data mode, no certification.

This engine was built **spec-first**: the regulatory reference, data model, and
phased build plan were authored as a private design vault, and each phase was
implemented, tested, and stress-tested against it. That spec is not included in
this repository; the code, its tests, and this README are the artifact.

## Quick start

```sh
uv sync
uv run pytest                      # 330+ tests, one per business rule minimum
uv run python scripts/demo.py      # end-to-end synthetic world → demo_out/
uv run govcon serve --demo         # the guided web workbench on localhost:8000
```

Optional extras: `--extra ai` (the grounded assistant, needs `ANTHROPIC_API_KEY`),
`--extra auth` (per-user JWT), `--extra postgres` (dual-backend), `--extra frontend`
(Playwright browser tests). Without them the core engine installs and tests unchanged.

The demo migrates a fresh `demo.db`, posts costs through the real write path
(allowability vectors stamped at capture), derives a fringe rate from the
ledger, closes the period through the gated three-way reconciliation,
generates Schedules G/H/I/N, and renders them to markdown under `demo_out/`.

## Command tour

```sh
uv run govcon --help
uv run govcon db upgrade                 # alembic upgrade head
uv run govcon sf1408                     # SF 1408 six-criteria self-check (exit 1 on fail)
uv run govcon audit verify               # recompute the audit-trail hash chain
uv run govcon reverify                   # regulatory re-verification checkpoints + watch list
uv run govcon export 2026 G              # render a generated schedule (--format md|xlsx)
uv run govcon contract 1                 # complete financial picture of a contract
uv run govcon rules show CAS_COVERAGE    # read a rules-as-data decision table
uv run govcon watch scan                 # Federal-Register suggestions for human review
uv run govcon serve --demo               # the guided web workbench (+ AI if a key is set)
uv run govcon about                      # the tool's own limitations, stated plainly
```

Database URL defaults to `sqlite:///govcon.db` (gitignored); override with
`GOVCON_DB_URL`.

## Layout

```
src/govcon/
  core/       decimal config (the ONE place precision is set), errors, logging,
              request-scoped actor identity
  db/         SafeNumeric type, session guards, hash-chained audit listener
  models/     37 tables (SQLAlchemy 2.0, dual-backend SQLite + Postgres schema)
  services/   allowability, rates, period close, ICE schedules, CAS/TINA,
              sweeps, REA/CDA, Eichleay, PBR monitoring, audit response,
              variances, exporters, contract statement, SF 1408 self-check,
              the decision-table engine (rules-as-data), rule-authoring validator
  education/  the plain-language glossary + the executable scenario library
  ai/         the grounded assistant kernel — tool registry, tool-use loop,
              grounding verifier, synthetic-only gate, per-pattern wrappers
  api/        FastAPI app + endpoints, HTTP hardening, optional JWT auth
  web/        the self-contained guided workbench (inlined fonts, no CDN)
  seeds/      regulatory threshold + FAR 31.205 category constants (drift-tested
              against the frozen migration seeds)
alembic/      18 migrations (0000–0017); triggers created alongside the tables
              they guard, ported to plpgsql for Postgres
tests/        every business rule asserted at BOTH layers (ORM error + raw-SQL
              IntegrityError from the trigger); AI dispatch/grounding/gating via
              an injected fake; browser flows via Playwright
```
