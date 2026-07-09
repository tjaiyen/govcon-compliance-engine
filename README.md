# govcon-engine

A personal, local-first GovCon cost-accounting compliance engine — FAR Part 31
allowability, SF 1408 structural checks, indirect rates, ICS/ICE schedules,
CAS/TINA dated thresholds, REA/CDA, Eichleay — operating on **synthetic data
only**, with an append-only, hash-chained audit trail underneath everything.

**SYNTHETIC DATA — NOT FOR REGULATORY RELIANCE.** This is a learning/portfolio
build. It is not a certified accounting system, holds no real contract or
CUI/ITAR data, and is not a substitute for professional audit/legal judgment.

The authoritative spec lives in the Obsidian vault
`~/Obsidian/TJ_Vault/govcon-compliance-engine/02 - Context/` (read
`00_HANDOFF_BUILD_SPEC.md` first). This repo deliberately lives OFF-vault —
see the 2026-07-08 code-location decision note there.

## Run

```sh
uv sync
uv run alembic upgrade head   # or: uv run govcon db upgrade
uv run pytest
uv run govcon --help
uv run govcon sf1408          # SF 1408 six-criteria self-check (exit 1 on fail)
uv run govcon about           # this tool's own limitations
uv run govcon audit verify    # recompute the audit-trail hash chain
uv run govcon reverify        # regulatory re-verification checkpoints/watch list
uv run govcon export 2026 G   # render a generated ICE schedule to markdown
```
