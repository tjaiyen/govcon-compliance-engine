"""Phase 0 smoke: package imports, migrations apply, engine connects, CLI runs."""

import subprocess
import sys

import sqlalchemy as sa


def test_package_imports():
    import govcon
    import govcon.models  # registers all tables

    assert govcon.__version__
    from govcon.db.base import Base

    assert len(Base.metadata.tables) == 14  # 12 Phase 1 + 2 Phase 2


def test_migrated_db_connects_and_has_tables(engine):
    with engine.connect() as conn:
        names = set(sa.inspect(conn).get_table_names())
    assert {"contracts", "gl_transactions", "jcl_entries", "audit_trail",
            "regulatory_thresholds", "periods", "indirect_pools",
            "gl_accounts", "contract_actions", "persons",
            "unallowable_cost_categories",
            "forward_pricing_rate_agreements"} <= names


def test_cli_help_runs():
    result = subprocess.run(
        [sys.executable, "-m", "govcon.cli.main", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "SYNTHETIC DATA" in result.stdout
