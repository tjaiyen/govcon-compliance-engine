"""Regulatory re-verification checkpoints (Phase 10; tech-stack decision #5
= manual reminders, no fragile web automation).

Two sources of "go re-check the primary sources":
1. DATE CHECKPOINTS transcribed from the roadmap's recurring list — events
   after which the reference file must be re-verified.
2. NON-FINAL THRESHOLD ROWS straight from the database — anything whose
   status is not final_rule (class_deviation / statute / proposed_rule) is
   by definition still in motion (ground rule 3) and stays on the watch
   list until a superseding migration lands.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.models import RegulatoryThreshold
from govcon.models.enums import ThresholdStatus

#: (trigger date, what to re-verify) — from 04_Build_Phases_Roadmap.md's
#: "Re-verification checkpoints" section.
DATE_CHECKPOINTS: tuple[tuple[datetime.date, str], ...] = (
    (
        datetime.date(2026, 7, 23),
        "RFO Phase II FAR rules (Cases 2026-001/-002/-005/-007): comments closed "
        "2026-07-23 — check whether the proposed rules moved to final",
    ),
    (
        datetime.date(2026, 8, 7),
        "CAS-to-GAAP final rule (91 FR 42139): confirm it took effect 2026-08-07 "
        "as scheduled and no delay/stay was issued",
    ),
)


@dataclass
class ReverificationItem:
    kind: str  # date_checkpoint | non_final_threshold
    due: bool
    description: str


def reverification_items(
    session: Session, as_of: datetime.date
) -> list[ReverificationItem]:
    items = [
        ReverificationItem(
            kind="date_checkpoint",
            due=as_of >= trigger,
            description=f"[{trigger.isoformat()}] {description}",
        )
        for trigger, description in DATE_CHECKPOINTS
    ]
    rows = session.execute(
        sa.select(RegulatoryThreshold)
        .where(RegulatoryThreshold.status != ThresholdStatus.FINAL_RULE)
        .where(RegulatoryThreshold.superseded_date.is_(None))
        .order_by(RegulatoryThreshold.threshold_id)
    ).scalars()
    for row in rows:
        items.append(
            ReverificationItem(
                kind="non_final_threshold",
                due=False,  # standing watch item, not date-triggered
                description=(
                    f"{row.rule_name} = {row.value if row.value is not None else '(status row)'} "
                    f"is {row.status.value} — re-verify against the primary source before "
                    "external reliance; supersede via a new migration when it finalizes"
                ),
            )
        )
    return items
