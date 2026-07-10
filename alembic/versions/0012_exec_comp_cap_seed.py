"""seed EXEC_COMP_CAP (verified CY2024/CY2025 BBA §702 cap amounts)

Revision ID: 0012
Revises: 0011

Verified 2026-07-08 against the OMB/OFPP Contractor Compensation Cap table
(Nov 2024 update, whitehouse.gov): CY2024 = $646,000; CY2025 = $671,000
(BBA §702, Pub. L. 113-67; 10 U.S.C. 2324(e)(1)(P) / 41 U.S.C. 4304(a)(16);
ECI-adjusted annually). The CY2026 amount was NOT published in a primary
source at verification time — consultant estimates are not seedable — so
the CY2025 row is superseded at 2026-01-01 and 2026 lookups RAISE until the
official number lands via a new migration (never invent; the gap sits on
the `govcon reverify` watch list via its non-final status).

Rows FROZEN here; mirrored in govcon/seeds/regulatory_thresholds.py with
the drift test.
"""

import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _money_type():
    """The seed insert must bind with the same dialect split SafeNumeric uses:
    canonical TEXT on SQLite (byte-identical to the original migration
    behavior), native NUMERIC on Postgres."""
    from govcon.db.types import Money

    return Money()

SEED = [
    ("EXEC_COMP_CAP", "646000.00", "2024-01-01", "2025-01-01",
     "BBA §702 cap, costs incurred CY2024: OMB/OFPP Contractor Compensation "
     "Cap table (Nov 2024 update, whitehouse.gov); 10 U.S.C. 2324(e)(1)(P) / "
     "41 U.S.C. 4304(a)(16)"),
    ("EXEC_COMP_CAP", "671000.00", "2025-01-01", "2026-01-01",
     "BBA §702 cap, costs incurred CY2025 (3.9% ECI escalation): OMB/OFPP "
     "Contractor Compensation Cap table (Nov 2024 update, whitehouse.gov). "
     "CY2026 amount not yet published in a primary source as of 2026-07-08 "
     "— deliberately unseeded; re-verify"),
]


def upgrade() -> None:
    thresholds = sa.table(
        "regulatory_thresholds",
        sa.column("rule_name", sa.String),
        sa.column("value", _money_type()),  # TEXT on SQLite / NUMERIC on PG (SafeNumeric)
        sa.column("effective_date", sa.Date),
        sa.column("superseded_date", sa.Date),
        sa.column("status", sa.String),
        sa.column("source_citation", sa.Text),
    )
    op.bulk_insert(
        thresholds,
        [
            dict(
                rule_name=rule,
                value=value,
                effective_date=datetime.date.fromisoformat(eff),
                superseded_date=datetime.date.fromisoformat(sup),
                status="statute",
                source_citation=citation,
            )
            for rule, value, eff, sup, citation in SEED
        ],
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM regulatory_thresholds WHERE rule_name = 'EXEC_COMP_CAP'"))
