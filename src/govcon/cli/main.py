"""CLI stub — grows with each phase (CLI-first decision, 2026-07-08)."""

from __future__ import annotations

import subprocess
import sys

import typer

app = typer.Typer(
    name="govcon",
    help=(
        "GovCon cost-accounting compliance engine. "
        "SYNTHETIC DATA — NOT FOR REGULATORY RELIANCE."
    ),
    no_args_is_help=True,
)

db_app = typer.Typer(help="Database migration commands.")
audit_app = typer.Typer(help="Audit-trail commands.")
rules_app = typer.Typer(
    help="Decision-table (rules-as-data) explorer — read-only; rule changes "
    "land as new table versions via migrations, never through the CLI."
)
watch_app = typer.Typer(
    help="Regulation watch — a Federal Register suggester with a mandatory "
    "human review step; it NEVER applies a change itself."
)
app.add_typer(db_app, name="db")
app.add_typer(audit_app, name="audit")
app.add_typer(rules_app, name="rules")
app.add_typer(watch_app, name="watch")


@app.command()
def version() -> None:
    """Print the engine version."""
    from govcon import __version__

    typer.echo(f"govcon-engine {__version__} (synthetic data only)")


@app.command()
def about() -> None:
    """Explain this tool's own limitations (SoD, design-vs-operation,
    not-a-certification) — the handoff spec §4 requirement."""
    from govcon.services.sf1408 import explain_limitations

    typer.echo(explain_limitations())


@app.command()
def export(
    fiscal_year: int,
    schedule_type: str,
    out: str = typer.Option(None, help="Output file path; prints to stdout when omitted (md only)."),
    format: str = typer.Option("md", help="md | xlsx (xlsx requires --out)."),
) -> None:
    """Render a generated ICE schedule to markdown or Excel (export-format
    decision 2026-07-08: markdown first, Excel committed next — both here)."""
    import sqlalchemy as sa

    from govcon.db.engine import make_engine, make_session_factory
    from govcon.models import ICESchedule
    from govcon.services.export import render_schedule

    factory = make_session_factory(make_engine())
    with factory() as session:
        row = session.execute(
            sa.select(ICESchedule)
            .where(ICESchedule.fiscal_year == fiscal_year)
            .where(ICESchedule.schedule_type == schedule_type.upper())
            .order_by(ICESchedule.schedule_id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            typer.echo(
                f"no generated Schedule {schedule_type.upper()} for FY{fiscal_year} — "
                "generate it first (generation requires the year fully closed)",
                err=True,
            )
            raise typer.Exit(code=1)
        if format == "xlsx":
            if not out:
                typer.echo("--format xlsx requires --out <file.xlsx>", err=True)
                raise typer.Exit(code=2)
            from govcon.services.export_excel import render_schedule_xlsx

            render_schedule_xlsx(row, out)
            typer.echo(f"wrote {out}")
            return
        rendered = render_schedule(row)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        typer.echo(f"wrote {out}")
    else:
        typer.echo(rendered)


@app.command()
def contract(
    contract_id: int,
    out: str = typer.Option(None, help="Write markdown to this path instead of stdout."),
) -> None:
    """The complete financial picture of one contract, inception to present
    (audit-defense checklist #10)."""
    from govcon.db.engine import make_engine, make_session_factory
    from govcon.models import Contract
    from govcon.services.contract_statement import contract_statement
    from govcon.services.export import render_markdown

    factory = make_session_factory(make_engine())
    with factory() as session:
        row = session.get(Contract, contract_id)
        if row is None:
            typer.echo(f"no contract {contract_id}", err=True)
            raise typer.Exit(code=1)
        statement = contract_statement(session, row)
    rendered = render_markdown(f"Contract {contract_id} — Financial Statement", statement)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        typer.echo(f"wrote {out}")
    else:
        typer.echo(rendered)


@app.command()
def reverify(
    strict: bool = typer.Option(
        False, help="Exit 1 when any checkpoint is due (for scripting/CI); "
        "off by default so the reminder list never blocks."
    ),
) -> None:
    """List regulatory re-verification items (date checkpoints + every
    non-final threshold row). This is a REMINDER surface: it exits 0 by
    default (a passed checkpoint is a standing reminder, not a failure —
    a stress test found the old behavior exited 1 forever after the
    checkpoint dates, which would break any recurring demo). Use --strict
    to get exit 1 when items are due."""
    import datetime

    from govcon.db.engine import make_engine, make_session_factory
    from govcon.services.reverification import reverification_items

    factory = make_session_factory(make_engine())
    with factory() as session:
        items = reverification_items(session, datetime.date.today())
    any_due = False
    for item in items:
        flag = (
            "DUE"
            if item.due
            else (
                "watch"
                if item.kind in ("non_final_threshold", "non_final_decision_rule")
                else "not yet"
            )
        )
        typer.echo(f"[{flag}] {item.description}")
        any_due = any_due or item.due
    if any_due:
        typer.echo("re-verification due — check primary sources and land a new "
                   "threshold migration if anything moved", err=True)
        if strict:
            raise typer.Exit(code=1)


@app.command()
def sf1408() -> None:
    """Run the SF 1408 six-criteria structural self-check against the
    current database state; exit 1 on any failed criterion."""
    from govcon.db.engine import make_engine, make_session_factory
    from govcon.services.sf1408 import run_self_check

    factory = make_session_factory(make_engine())
    with factory() as session:
        results = run_self_check(session)
    failed = False
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        typer.echo(f"[{mark}] {r.criterion} — {r.name}")
        for finding in r.findings:
            typer.echo(f"       - {finding}")
        failed = failed or not r.passed
    typer.echo("SYNTHETIC DATA — NOT FOR REGULATORY RELIANCE")
    if failed:
        raise typer.Exit(code=1)


@rules_app.command("list")
def rules_list() -> None:
    """List every decision-table version with its dated window."""
    import sqlalchemy as sa

    from govcon.db.engine import make_engine, make_session_factory
    from govcon.models import DecisionRule, DecisionTable

    factory = make_session_factory(make_engine())
    with factory() as session:
        tables = session.execute(
            sa.select(DecisionTable).order_by(
                DecisionTable.table_name, DecisionTable.version
            )
        ).scalars()
        for t in tables:
            n_rules = session.execute(
                sa.select(sa.func.count())
                .select_from(DecisionRule)
                .where(DecisionRule.decision_table_id == t.decision_table_id)
            ).scalar()
            window = (
                f"{t.effective_date.isoformat() if t.effective_date else 'open'}"
                f" → {t.superseded_date.isoformat() if t.superseded_date else 'open'}"
            )
            typer.echo(f"{t.table_name} v{t.version}  [{window}]  {n_rules} rules")
            typer.echo(f"    {t.source_citation}")


@rules_app.command("show")
def rules_show(
    name: str,
    on: str = typer.Option(
        None, help="ISO date the table must be in force on (default: today)."
    ),
) -> None:
    """Show a table's rules in evaluation order, with per-rule provenance."""
    import datetime
    import json

    import sqlalchemy as sa

    from govcon.db.engine import make_engine, make_session_factory
    from govcon.models import DecisionRule
    from govcon.services.decision_engine import table_in_force

    on_date = datetime.date.fromisoformat(on) if on else datetime.date.today()
    factory = make_session_factory(make_engine())
    with factory() as session:
        try:
            table = table_in_force(session, name, on_date)
        except LookupError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from None
        typer.echo(f"{table.table_name} v{table.version} — {table.description or ''}")
        typer.echo(f"source: {table.source_citation}")
        if table.threshold_context:
            typer.echo(
                f"thresholds: {json.dumps(table.threshold_context)} "
                f"(resolution: {table.threshold_resolution})"
            )
        if table.initial_outcome is not None:
            typer.echo(f"initial outcome: {json.dumps(table.initial_outcome)}")
        rows = session.execute(
            sa.select(DecisionRule)
            .where(DecisionRule.decision_table_id == table.decision_table_id)
            .order_by(DecisionRule.rule_order)
        ).scalars()
        for r in rows:
            stop = "stop" if r.stop else "continue"
            typer.echo(f"  {r.rule_order}. {r.rule_key} [{stop}]")
            typer.echo(f"     when:    {json.dumps(r.when_ast)}")
            typer.echo(f"     outcome: {json.dumps(r.outcome)}")
            if r.status is not None:
                typer.echo(
                    f"     STATUS:  {r.status.value} — {r.source_citation}"
                )


@watch_app.command("scan")
def watch_scan(
    since: str = typer.Option(
        None, help="Fetch documents published on/after this ISO date "
        "(default: 90 days back)."
    ),
) -> None:
    """Scan the Federal Register for the engine's watch targets and record
    NEW suggestions for human review. Suggest-only: applying a change is
    always a verified migration, never this command."""
    import datetime

    from govcon.db.engine import make_engine, make_session_factory
    from govcon.services.regulation_watch import scan

    since_date = datetime.date.fromisoformat(since) if since else None
    factory = make_session_factory(make_engine())
    with factory() as session:
        result = scan(session, since=since_date)
        session.commit()
    typer.echo(
        f"scanned {len(result.targets)} watch target(s) since "
        f"{result.since.isoformat()}: {len(result.new_suggestions)} new, "
        f"{result.already_known} already known"
    )
    for u in result.unavailable:
        typer.echo(f"[unavailable] {u['watch_rule']}: {u['error']}", err=True)
    for t in result.truncated:
        typer.echo(
            f"[truncated] {t['watch_rule']}: {t['total']} matches, first "
            f"{t['recorded']} recorded — narrow with --since",
            err=True,
        )
    for name in result.skipped_unmapped:
        typer.echo(f"[unmapped] {name}: no search term configured — add one "
                   "to WATCH_TERMS", err=True)
    typer.echo("suggestions are search results, not determinations — review "
               "with `govcon watch list` / `govcon watch review`")


@watch_app.command("list")
def watch_list(
    all: bool = typer.Option(False, "--all", help="Include reviewed/dismissed."),
) -> None:
    """List regulation-watch suggestions (default: NEW only)."""
    import sqlalchemy as sa

    from govcon.db.engine import make_engine, make_session_factory
    from govcon.models import RegulatorySuggestion
    from govcon.models.enums import SuggestionStatus

    factory = make_session_factory(make_engine())
    with factory() as session:
        stmt = sa.select(RegulatorySuggestion).order_by(
            RegulatorySuggestion.strong_match.desc(),
            RegulatorySuggestion.publication_date.desc(),
        )
        if not all:
            stmt = stmt.where(RegulatorySuggestion.status == SuggestionStatus.NEW)
        rows = session.execute(stmt).scalars().all()
        if not rows:
            typer.echo("no suggestions" + ("" if all else " with status=new"))
            return
        for r in rows:
            mark = "STRONG" if r.strong_match else "weak  "
            eff = f" effective {r.effective_on.isoformat()}" if r.effective_on else ""
            typer.echo(
                f"#{r.suggestion_id} [{r.status.value}] [{mark}] "
                f"{r.watch_rule} — {r.doc_type or 'Document'} "
                f"{r.document_number} ({r.publication_date}){eff}"
            )
            typer.echo(f"    {r.title}")
            if r.url:
                typer.echo(f"    {r.url}")


@watch_app.command("review")
def watch_review(
    suggestion_id: int,
    reviewed: bool = typer.Option(False, "--reviewed", help="Mark verified-relevant."),
    dismiss: bool = typer.Option(False, "--dismiss", help="Mark not relevant."),
    note: str = typer.Option(None, help="Why — recorded on the row."),
) -> None:
    """Record the human verdict on a suggestion. This changes ONLY the
    suggestion row — applying a regulatory change is always a migration."""
    from govcon.db.engine import make_engine, make_session_factory
    from govcon.models.enums import SuggestionStatus
    from govcon.services.regulation_watch import review_suggestion

    if reviewed == dismiss:
        typer.echo("choose exactly one of --reviewed / --dismiss", err=True)
        raise typer.Exit(code=2)
    status = SuggestionStatus.REVIEWED if reviewed else SuggestionStatus.DISMISSED
    factory = make_session_factory(make_engine())
    with factory() as session:
        try:
            row = review_suggestion(session, suggestion_id, status=status, note=note)
        except LookupError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from None
        session.commit()
        typer.echo(f"#{row.suggestion_id} -> {row.status.value}")


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Run alembic upgrade head against GOVCON_DB_URL."""
    raise SystemExit(
        subprocess.call([sys.executable, "-m", "alembic", "upgrade", "head"])
    )


@audit_app.command("verify")
def audit_verify() -> None:
    """Recompute the audit-trail hash chain and report the first mismatch."""
    from govcon.db.audit import verify_audit_chain
    from govcon.db.engine import make_engine, make_session_factory

    factory = make_session_factory(make_engine())
    with factory() as session:
        ok, bad_row = verify_audit_chain(session)
    if ok:
        typer.echo("audit chain: OK")
    else:
        typer.echo(f"audit chain: TAMPERED at trail_id={bad_row}", err=True)
        raise typer.Exit(code=1)


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Spin up a fresh, migrated (threshold-seeded) SQLite DB for a "
        "zero-setup demo, instead of using GOVCON_DB_URL.",
    ),
) -> None:
    """Run the guided web workbench (advisory, synthetic data) on localhost.

    Open http://HOST:PORT in a browser. This is a decision-support & training
    surface over the same compliance logic the CLI exposes — it explains and
    teaches every determination; it is not a certified accounting system.
    """
    import os
    import tempfile

    if demo:
        db_path = os.path.join(tempfile.mkdtemp(prefix="govcon-demo-"), "demo.db")
        os.environ["GOVCON_DB_URL"] = f"sqlite:///{db_path}"
        typer.echo(f"demo DB: {db_path} (migrating + seeding thresholds ...)")
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:  # pragma: no cover - surfaced to the user
            typer.echo(result.stderr, err=True)
            raise typer.Exit(code=1)

    try:
        import uvicorn
    except ModuleNotFoundError:  # pragma: no cover - dependency guard
        typer.echo(
            "the web workbench needs the web extras: `uv sync` (fastapi + uvicorn)",
            err=True,
        )
        raise typer.Exit(code=1) from None

    from govcon.api import create_app

    typer.echo(f"GovCon workbench (synthetic data) → http://{host}:{port}")
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    app()
