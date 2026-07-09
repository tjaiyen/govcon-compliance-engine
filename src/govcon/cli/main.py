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
