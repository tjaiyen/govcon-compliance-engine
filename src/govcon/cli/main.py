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
app.add_typer(db_app, name="db")
app.add_typer(audit_app, name="audit")


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
        flag = "DUE" if item.due else ("watch" if item.kind == "non_final_threshold" else "not yet")
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


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    app()
