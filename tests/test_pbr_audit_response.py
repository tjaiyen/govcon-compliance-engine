"""Phase 11: PBR variance monitoring + FPRA authorization check, and the
audit-response workflow (T-minus escalation, review gate at both layers)."""

import datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa

from govcon.models import ForwardPricingRateAgreement, IndirectPool
from govcon.models.enums import PoolName, PoolStatus, RateType
from govcon.models.monitoring import AlertPoint, AuditType, NotificationStatus
from govcon.models.reference import FPRAStatus
from govcon.services.audit_response import (
    AuditWorkflowError,
    acknowledge_alert,
    advance_status,
    create_notification,
    fire_due_alerts,
    record_management_review,
)
from govcon.services.pbr_monitoring import (
    PBRMonitoringError,
    check_fpra_authorization,
    monitor_period,
    resolve_note,
)
from tests.fixtures.synthetic_data import seed_all

D = datetime.date


# --- PBR monitoring (§12) ----------------------------------------------------


def test_variance_beyond_threshold_creates_note(session):
    """Fixture: approved provisional fringe pool, base 100,000; set rate
    0.0040 → expected YTD through period 6 = 0.0040×100000×6/12 = 200.
    Actual YTD pool costs = 400 → +100% variance → flagged at 5%."""
    data = seed_all(session)
    data.pool.calculated_rate = Decimal("0.0040")
    session.flush()
    notes = monitor_period(session, data.period_open)
    session.commit()
    assert len(notes) == 1
    assert notes[0].variance_amount == Decimal("200.00")
    assert notes[0].variance_pct == Decimal("1.0000")
    assert "auto-flagged" in notes[0].explanation


def test_variance_inside_threshold_creates_nothing(session):
    data = seed_all(session)
    data.pool.calculated_rate = Decimal("0.0040")
    session.flush()
    assert monitor_period(session, data.period_open, threshold_pct=Decimal("1.50")) == []


def test_resolving_note_requires_real_explanation(session):
    data = seed_all(session)
    data.pool.calculated_rate = Decimal("0.0040")
    session.flush()
    note = monitor_period(session, data.period_open)[0]
    with pytest.raises(PBRMonitoringError, match="real explanation"):
        resolve_note(session, note, resolved_by="tj", explanation="  ")
    resolved = resolve_note(
        session, note, resolved_by="tj",
        explanation="One-time June benefits invoice timing; normalizes in July.",
    )
    assert resolved.resolved_by == "tj" and resolved.resolved_date is not None


def test_fpra_authorization_distinction(session):
    """§12: 'typed forward_pricing' ≠ 'authorized by an FPRA'."""
    seed_all(session)
    fpra_draft = ForwardPricingRateAgreement(
        fiscal_year_start=2026, fiscal_year_end=2028, status=FPRAStatus.DRAFT
    )
    fpra_live = ForwardPricingRateAgreement(
        fiscal_year_start=2026, fiscal_year_end=2028, status=FPRAStatus.NEGOTIATED
    )
    session.add_all([fpra_draft, fpra_live])
    session.flush()

    def fp_pool(fpra_id):
        pool = IndirectPool(
            pool_name=PoolName.OVERHEAD, fiscal_year=2027,
            rate_type=RateType.FORWARD_PRICING, status=PoolStatus.APPROVED,
            allocation_base_amount=Decimal("1.00"), fpra_id=fpra_id,
        )
        session.add(pool)
        session.flush()
        return pool

    fp_pool(None)                    # no agreement at all
    fp_pool(fpra_draft.fpra_id)      # agreement not negotiated
    fp_pool(fpra_live.fpra_id)       # properly authorized
    findings = check_fpra_authorization(session)
    assert len(findings) == 2
    assert any("NO FPRA" in f for f in findings)
    assert any("'draft'" in f for f in findings)


# --- Audit response workflow (§13) --------------------------------------------


def _notification(session, days=45):
    return create_notification(
        session,
        audit_type=AuditType.INCURRED_COST,
        received_date=D(2026, 7, 1),
        response_deadline_days=days,
        requested_documents='["ICS FY2025", "labor distribution"]',
    )


def test_deadline_derived_and_alerts_fire_per_point(session):
    seed_all(session)
    n = _notification(session, days=45)  # deadline 2026-08-15
    assert n.computed_deadline_date == D(2026, 8, 15)
    # T-30 window opens 7/16; before that, nothing fires.
    assert fire_due_alerts(session, n, D(2026, 7, 10)) == []
    fired = fire_due_alerts(session, n, D(2026, 7, 16))
    assert [a.alert_point for a in fired] == [AlertPoint.T_30]
    # Idempotent: same day again fires nothing new.
    assert fire_due_alerts(session, n, D(2026, 7, 16)) == []
    # By T-1 day, the remaining four have fired exactly once each.
    fired = fire_due_alerts(session, n, D(2026, 8, 14))
    assert [a.alert_point for a in fired] == [
        AlertPoint.T_14, AlertPoint.T_7, AlertPoint.T_3, AlertPoint.T_1
    ]
    ack = acknowledge_alert(session, fired[0], acknowledged_by="tj")
    assert ack.acknowledged_at is not None


def test_short_window_skips_unfitting_points(session):
    seed_all(session)
    n = _notification(session, days=10)
    fired = fire_due_alerts(session, n, D(2026, 7, 11))  # deadline 7/11: all due
    assert [a.alert_point for a in fired] == [AlertPoint.T_7, AlertPoint.T_3, AlertPoint.T_1]


def test_review_gate_blocks_unreviewed_submit_both_layers(session):
    seed_all(session)
    n = _notification(session)
    advance_status(session, n, NotificationStatus.DOCUMENT_COLLECTION)
    advance_status(session, n, NotificationStatus.MANAGEMENT_REVIEW)
    with pytest.raises(AuditWorkflowError, match="management-review sign-off"):
        advance_status(session, n, NotificationStatus.SUBMITTED)
    session.commit()
    with pytest.raises(sa.exc.IntegrityError, match="sign-off"):  # DB backstop
        session.execute(
            sa.text("UPDATE audit_notifications SET status='submitted' WHERE notification_id=:n"),
            {"n": n.notification_id},
        )
    session.rollback()
    record_management_review(session, n, reviewed_by="tj")
    advance_status(session, n, NotificationStatus.SUBMITTED)
    session.commit()
    assert n.status == NotificationStatus.SUBMITTED
    # Alerts stop once submitted:
    assert fire_due_alerts(session, n, D(2026, 8, 14)) == []


def test_workflow_is_forward_only_one_step(session):
    seed_all(session)
    n = _notification(session)
    with pytest.raises(AuditWorkflowError, match="forward-only"):
        advance_status(session, n, NotificationStatus.SUBMITTED)  # skips two stages
    advance_status(session, n, NotificationStatus.DOCUMENT_COLLECTION)
    with pytest.raises(AuditWorkflowError, match="forward-only"):
        advance_status(session, n, NotificationStatus.NOTIFICATION_RECEIVED)  # backward


def test_review_recorded_only_in_review_stage(session):
    seed_all(session)
    n = _notification(session)
    with pytest.raises(AuditWorkflowError, match="management_review stage"):
        record_management_review(session, n, reviewed_by="tj")
