"""Dated threshold lookup — the engine applies the threshold that was in
force on a given date (CLAUDE.md ground rule 2; architecture spec §7-8).

Semantics: a row is in force on date d when
    (effective_date IS NULL OR effective_date <= d)
AND (superseded_date IS NULL OR d < superseded_date)
"""

from __future__ import annotations

import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.models import RegulatoryThreshold


def threshold_in_force(
    session: Session, rule_name: str, on_date: datetime.date
) -> RegulatoryThreshold:
    """Return the regulatory_thresholds row in force for rule_name on on_date.

    Raises LookupError if no row is in force — a missing threshold is an
    open question to flag, never a value to invent (reg-reference §11).
    """
    stmt = (
        sa.select(RegulatoryThreshold)
        .where(RegulatoryThreshold.rule_name == rule_name)
        .where(
            sa.or_(
                RegulatoryThreshold.effective_date.is_(None),
                RegulatoryThreshold.effective_date <= on_date,
            )
        )
        .where(
            sa.or_(
                RegulatoryThreshold.superseded_date.is_(None),
                RegulatoryThreshold.superseded_date > on_date,
            )
        )
    )
    rows = session.execute(stmt).scalars().all()
    if not rows:
        raise LookupError(
            f"no {rule_name!r} threshold in force on {on_date.isoformat()} — "
            "flag as an open question, do not invent a value"
        )
    if len(rows) > 1:
        raise LookupError(
            f"{len(rows)} {rule_name!r} rows in force on {on_date.isoformat()} "
            "— overlapping effective windows in the seed data"
        )
    return rows[0]
