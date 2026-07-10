# Postgres support (Phase 4b — PORTED and verified live)

## Status: PORTED — verified against PostgreSQL 17.10

This document began as the executable port plan written when no Postgres was
available to verify against; the port has since been executed and verified
live (2026-07-09, PostgreSQL 17.10 via Homebrew, plus a `postgres:17` service
job in CI running the full suite). What shipped:

* **Migration 0017** creates the complete plpgsql enforcement layer (all 26
  triggers, census below) on Postgres; SQLite keeps its original trigger DDL
  from 0001/0006/0013/0015/0016 (now dialect-branched, never raising).
  Trigger functions raise with **ERRCODE 23000** so psycopg surfaces the
  same `IntegrityError` class SQLite's `RAISE(ABORT)` does — every
  business-rule test asserts identical class + message on both backends.
* **Advisory lock** (`AUDIT_CHAIN_LOCK_KEY`, transaction-scoped) serializes
  the audit chain's read-last-hash/insert pair; proven by a two-writer
  concurrency probe (tests/test_postgres_concurrency.py) that SQLite cannot
  express. The gapless-trail_id belt is SQLite-only (PG sequences skip on
  rollback); on PG, integrity = hash linkage + the no-delete trigger.
* **Dual-backend test harness**: `GOVCON_TEST_PG=<admin url> uv run pytest`
  clones a migrated template database per test (`CREATE DATABASE …
  TEMPLATE`). SQLite-by-design probes (PRAGMA bypass mechanics, workspace
  files) carry a reported `sqlite_only` marker — skipped, never silent.

Run it locally: `brew install postgresql@17`, `initdb` + `pg_ctl start`,
then `GOVCON_TEST_PG=postgresql+psycopg://you@127.0.0.1:5432/postgres uv run
pytest`.

## 1. The 26 triggers (live census; ported in migration 0017)

Every trigger is `BEFORE <op> ON <table> … SELECT RAISE(ABORT, 'msg')` on
SQLite. The plpgsql shape is mechanical — one trigger function per rule,
`RAISE EXCEPTION` with the same message, bound `BEFORE <op>`:

| Class | Triggers | plpgsql notes |
|---|---|---|
| Append-only (no UPDATE / no DELETE) | `audit_trail`×2, `gl_transactions`×2, `regulatory_thresholds`×2, `decision_tables`×2, `decision_rules`×2, `tina_sweep_findings`×2 | one generic `raise_append_only()` function reused by all — SQLite needed per-table DDL, Postgres does not |
| No-delete only | `regulatory_suggestions` | reuse `raise_append_only()` with a custom message |
| Frozen columns (UPDATE with column comparison) | `contracts_immutable_cols`, `cost_accounting_practices_frozen`, `indirect_pools_identity_frozen`, `indirect_pools_locked_frozen`, `overhead_budgets_frozen`, `standard_costs_frozen`, `tina_baselines_locked` | SQLite `NEW.x IS NOT OLD.x` → plpgsql `NEW.x IS DISTINCT FROM OLD.x` (exact semantic match) |
| Cross-table state gates | `gl_transactions_open_period`, `jcl_entries_open_period`, `periods_close_requires_reconciliation`, `periods_no_reopen`, `gl_transactions_direct_needs_contract`, `audit_notifications_review_gate` | subselects inside the trigger function; add `FOR EACH ROW`; verify isolation semantics under concurrency (see §3) |

Port mechanics: a new migration (e.g. `0017_postgres_triggers`) that is a
no-op on SQLite and creates the functions+triggers on Postgres — the
existing migrations keep their SQLite DDL and drop their
`NotImplementedError` guards in the same change. `alembic upgrade head`
against a fresh Postgres then produces the full enforcement layer.

## 2. Audit hash-chain: advisory lock

`write_audit_rows` reads the last `entry_hash` then inserts — SQLite's
single-writer serializes that pair for free. On Postgres, wrap the section
in `SELECT pg_advisory_xact_lock(<const>)` (transaction-scoped, one constant
key for the chain) so two concurrent flushes cannot fork the chain. The
contiguity check in `verify_audit_chain` (gapless trail_id) additionally
requires the id sequence not to skip on rollback — either accept documented
gaps on Postgres (weaken the check to hash-linkage only) or allocate ids
inside the same advisory-locked section. Decide at port time; both options
are honest, silent divergence is not.

## 3. Verification bar (met — see CI job `test-postgres`)

* Full `uv run pytest` suite green with `GOVCON_DB_URL` on Postgres — the
  business-rule tests ARE the trigger probes (tamper tests, closed-period
  posts, append-only edits).
* Concurrency probe that SQLite cannot express: two sessions flushing
  audited changes simultaneously → chain verifies afterward.
* `alembic upgrade head` + `downgrade` round-trip on an empty Postgres.

## 4. Multi-tenancy on Postgres: the decision already made

Phase 4 chose **workspace-per-database** (physical isolation; see
`govcon/workspaces.py`) over `tenant_id` + row-level security, because for an
advisory/training tool the isolation is stronger, the schema stays untouched,
and every learner wants a separate synthetic world anyway. On Postgres the
same model maps to database-per-workspace (or schema-per-workspace if
connection count matters). RLS on shared tables only becomes the right trade
at real-data SaaS scale — which sits behind the excluded Phase 5 liability
line. If that line is ever crossed deliberately, RLS lands as: `tenant_id`
on the ~15 operational tables, `CREATE POLICY` per table keyed to
`current_setting('govcon.tenant')`, set per-connection by the session
factory — and the audit chain becomes per-tenant (separate genesis per
tenant) so tenants cannot observe each other's write cadence.
