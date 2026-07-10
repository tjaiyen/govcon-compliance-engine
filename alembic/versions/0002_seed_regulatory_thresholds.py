"""seed regulatory_thresholds

Revision ID: 0002
Revises: 0001

Rows below are FROZEN — transcribed from the vault's
02_Regulatory_Reference_Verified.md (verified_as_of 2026-07-08) and inlined
literally so the migration history is part of the audit-trail story. They
are mirrored by importable constants in govcon/seeds/regulatory_thresholds.py;
tests/test_threshold_seed.py asserts DB rows == constants so the two can
never drift silently. Future regulatory changes are NEW migrations that
supersede rows (set superseded_date / add rows), never edits to this one.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _money_type():
    """The seed insert must bind with the same dialect split SafeNumeric uses:
    canonical TEXT on SQLite (byte-identical to the original migration
    behavior), native NUMERIC on Postgres."""
    from govcon.db.types import Money

    return Money()

# (rule_name, value, effective_date, superseded_date, status, source_citation)
SEED = [
    ("TINA_THRESHOLD", "2000000.00", None, "2025-10-01", "final_rule",
     "Reg-Ref §1 TINA inflation-adjustment history ($2.0M before Oct 1, 2025)"),
    ("TINA_THRESHOLD", "2500000.00", "2025-10-01", "2026-07-01", "final_rule",
     "Reg-Ref §1 ($2.5M Oct 1, 2025 - Jun 30, 2026, inflation-adjusted)"),
    ("TINA_THRESHOLD", "10000000.00", "2026-07-01", None, "class_deviation",
     "P.L. 119-60 (FY2026 NDAA, enacted 2025-12-18); contracts entered after "
     "2026-06-30; DoD class deviation 2026-O0048 / DFARS 215.403-3(a) — "
     "codified DFARS not yet amended as of 2026-07-08"),
    ("CAS_CONTRACT_TRIGGER", "7500000.00", None, "2026-07-01", "final_rule",
     "Reg-Ref §1 (old $7.5M trigger, pre-NDAA)"),
    ("CAS_CONTRACT_TRIGGER", "35000000.00", "2026-07-01", None, "statute",
     "P.L. 119-60, after 2026-06-30; implementing CAS regulation (48 CFR "
     "9903.201) still PROPOSED as of 2026-07-08 (CASB Case 2021-01 NPRM, 91 FR 13559)"),
    ("CAS_FULL_COVERAGE", "50000000.00", None, "2026-07-01", "final_rule",
     "Reg-Ref §1 (old $50M full-coverage threshold, pre-NDAA)"),
    ("CAS_FULL_COVERAGE", "100000000.00", "2026-07-01", None, "statute",
     "P.L. 119-60, after 2026-06-30; implementing CAS Board regulation still "
     "PROPOSED as of 2026-07-08 (91 FR 13559)"),
    ("SAT", "350000.00", "2025-10-01", None, "final_rule",
     "FAR Council final rule, Inflation Adjustment of Acquisition-Related "
     "Thresholds, 90 FR 41872 (2025-08-27)"),
    ("CAS_407_STATUS", None, None, None, "proposed_rule",
     "NPRM 2026-03-20 (comments closed 2026-04-20); proposed for elimination, "
     "NOT final as of 2026-07-08 — re-verify before Phase 3/12"),
    ("CAS_408_STATUS", None, "2026-08-07", None, "final_rule",
     "Final rule 91 FR 42139 (2026-07-08), rescinds CAS 408 eff. 2026-08-07"),
    ("CAS_411_STATUS", None, "2026-08-07", None, "final_rule",
     "Final rule 91 FR 42139 (2026-07-08), rescinds CAS 411 eff. 2026-08-07"),
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
    import datetime

    def d(s):
        return None if s is None else datetime.date.fromisoformat(s)

    op.bulk_insert(
        thresholds,
        [
            dict(
                rule_name=rule,
                value=value,
                effective_date=d(eff),
                superseded_date=d(sup),
                status=status,
                source_citation=citation,
            )
            for rule, value, eff, sup, status, citation in SEED
        ],
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM regulatory_thresholds"))
