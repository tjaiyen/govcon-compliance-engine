"""Modified-CAS consistency tracking (spec §7a): disclosed-vs-actual
checks, practice-change events, and standard-status lookups.

Modified coverage = CAS 401/402/405/406 exactly (reg-ref §2). Rescinding
other standards does NOT remove the 401/402 consistency obligations — the
9903.201-4 clauses persist, and a change to a disclosed practice can still
trigger a cost-impact analysis under 9903.201-6.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.models import CostAccountingPractice, GLAccount, PracticeChangeEvent, RegulatoryThreshold
from govcon.models.practices import ChangeEventStatus, DisclosedTreatment

#: Modified CAS coverage is exactly these four standards (reg-ref §2).
MODIFIED_COVERAGE_STANDARDS = ("CAS 401", "CAS 402", "CAS 405", "CAS 406")


def current_practices(session: Session) -> list[CostAccountingPractice]:
    """Practice rows not superseded — the disclosed practices in force."""
    return list(
        session.execute(
            sa.select(CostAccountingPractice).where(
                CostAccountingPractice.superseded_by.is_(None)
            )
        ).scalars()
    )


@dataclass
class ConsistencyViolation:
    practice_id: int
    practice_area: str
    disclosed_treatment: str
    account_id: int
    account_code: str
    actual_cost_type: str

    def describe(self) -> str:
        return (
            f"account {self.account_code} is coded {self.actual_cost_type!r} but the "
            f"disclosed practice {self.practice_area!r} (practice_id="
            f"{self.practice_id}) discloses {self.disclosed_treatment!r} — "
            "CAS 401/402 disclosed-vs-actual inconsistency; surface for review, "
            "do not auto-fix"
        )


def check_disclosed_vs_actual(session: Session) -> list[ConsistencyViolation]:
    """The v1 CAS 401/402 mechanical check (spec §7a): flag every gl_account
    whose cost_type contradicts its governing disclosed practice (matched by
    account_code prefix). Unallowable-coded accounts are not violations —
    CAS 405 handling is the Section 3/4 allowability layer's job."""
    violations: list[ConsistencyViolation] = []
    for practice in current_practices(session):
        accounts = session.execute(
            sa.select(GLAccount).where(
                GLAccount.account_code.startswith(practice.account_code_prefix)
            )
        ).scalars()
        for account in accounts:
            actual = account.cost_type.value
            if actual == "unallowable":
                continue
            if actual != practice.disclosed_treatment.value:
                violations.append(
                    ConsistencyViolation(
                        practice_id=practice.practice_id,
                        practice_area=practice.practice_area,
                        disclosed_treatment=practice.disclosed_treatment.value,
                        account_id=account.account_id,
                        account_code=account.account_code,
                        actual_cost_type=actual,
                    )
                )
    return violations


def record_practice_change(
    session: Session,
    old: CostAccountingPractice,
    *,
    effective_date: datetime.date,
    **changes,
) -> tuple[CostAccountingPractice, PracticeChangeEvent]:
    """The only sanctioned way to change a disclosed practice: a new version
    row plus a flagged change event (cost_impact_required=True by default,
    9903.201-6). Same versioning idiom as supersede_contract()."""
    fields = dict(
        practice_area=old.practice_area,
        disclosed_treatment=old.disclosed_treatment,
        account_code_prefix=old.account_code_prefix,
        description=old.description,
        effective_date=effective_date,
    )
    fields.update(changes)
    new = CostAccountingPractice(**fields)
    session.add(new)
    session.flush()
    old.superseded_by = new.practice_id
    event = PracticeChangeEvent(
        practice_id=old.practice_id,
        new_practice_id=new.practice_id,
        detected_date=effective_date,
    )
    session.add(event)
    session.flush()
    return new, event


def resolve_change_event(
    session: Session, event: PracticeChangeEvent, *, notes: str
) -> PracticeChangeEvent:
    """Resolving a cost-impact flag requires a recorded reason — never a
    silent dismissal (spec §7a)."""
    if not notes or not notes.strip():
        raise ValueError(
            "resolving a practice-change event requires a recorded reason "
            "(9903.201-6 cost-impact flag cannot be silently dismissed)"
        )
    event.status = ChangeEventStatus.RESOLVED
    event.notes = notes
    session.flush()
    return event


def cas_standard_status(session: Session, standard: str) -> RegulatoryThreshold:
    """Status row for a CAS standard (e.g. 'CAS_407') from the seeded
    regulatory_thresholds — proposed_rule vs final_rule is load-bearing
    (CLAUDE.md ground rule 3). Raises LookupError when no status row exists
    (e.g. CAS 401 — active, no pending rulemaking tracked)."""
    row = session.execute(
        sa.select(RegulatoryThreshold)
        .where(RegulatoryThreshold.rule_name == f"{standard}_STATUS")
        .order_by(RegulatoryThreshold.threshold_id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise LookupError(
            f"no {standard}_STATUS row in regulatory_thresholds — no tracked "
            "rulemaking for this standard (do not infer a status)"
        )
    return row
