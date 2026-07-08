"""Dated threshold lookups return the era-correct value; statuses are
load-bearing; the frozen migration seed and the importable constants can't
drift."""

import datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa

from govcon.models import RegulatoryThreshold
from govcon.seeds.regulatory_thresholds import SEED_ROWS
from govcon.services.thresholds import threshold_in_force


def d(iso: str) -> datetime.date:
    return datetime.date.fromisoformat(iso)


@pytest.mark.parametrize(
    ("on_date", "expected"),
    [
        ("2025-06-01", Decimal("2000000.00")),
        ("2025-09-30", Decimal("2000000.00")),
        ("2025-10-01", Decimal("2500000.00")),   # boundary: new era starts
        ("2026-06-30", Decimal("2500000.00")),   # boundary: last day of $2.5M
        ("2026-07-01", Decimal("10000000.00")),  # boundary: "after June 30, 2026"
        ("2026-12-01", Decimal("10000000.00")),
    ],
)
def test_tina_threshold_by_era(session, on_date, expected):
    row = threshold_in_force(session, "TINA_THRESHOLD", d(on_date))
    assert row.value == expected


def test_cas_thresholds_branch_at_ndaa_boundary(session):
    assert threshold_in_force(session, "CAS_CONTRACT_TRIGGER", d("2026-06-30")).value == Decimal("7500000.00")
    assert threshold_in_force(session, "CAS_CONTRACT_TRIGGER", d("2026-07-01")).value == Decimal("35000000.00")
    assert threshold_in_force(session, "CAS_FULL_COVERAGE", d("2026-06-30")).value == Decimal("50000000.00")
    assert threshold_in_force(session, "CAS_FULL_COVERAGE", d("2026-07-01")).value == Decimal("100000000.00")


def test_sat_current_value(session):
    assert threshold_in_force(session, "SAT", d("2026-07-08")).value == Decimal("350000.00")


def test_statuses_are_load_bearing(session):
    """Ground rule 3: statute vs proposed_rule vs final_rule vs class_deviation
    must be exposed, never presented as uniformly settled law."""
    assert threshold_in_force(session, "TINA_THRESHOLD", d("2026-07-08")).status.value == "class_deviation"
    assert threshold_in_force(session, "CAS_CONTRACT_TRIGGER", d("2026-07-08")).status.value == "statute"
    assert threshold_in_force(session, "CAS_407_STATUS", d("2026-07-08")).status.value == "proposed_rule"
    assert threshold_in_force(session, "CAS_408_STATUS", d("2026-08-07")).status.value == "final_rule"


def test_missing_threshold_raises_not_invents(session):
    with pytest.raises(LookupError, match="do not invent"):
        threshold_in_force(session, "EXEC_COMP_CAP", d("2026-07-08"))


def test_seed_constants_match_db_rows(session):
    """Drift guard: migration-frozen rows == importable constants."""
    db_rows = session.execute(
        sa.select(RegulatoryThreshold).order_by(RegulatoryThreshold.threshold_id)
    ).scalars().all()
    assert len(db_rows) == len(SEED_ROWS)
    for db_row, const in zip(db_rows, SEED_ROWS):
        assert db_row.rule_name == const["rule_name"]
        assert db_row.value == const["value"]
        eff = const["effective_date"]
        assert db_row.effective_date == (None if eff is None else d(eff))
        sup = const["superseded_date"]
        assert db_row.superseded_date == (None if sup is None else d(sup))
        assert db_row.status.value == const["status"]
