"""Audit Response workflow simulator (spec §13) — SYNTHETIC notification
fixtures only; never a real DCAA correspondence system. The single most
consequential documented failure isn't a calculation error, it's a missed
response deadline — this module tracks state, computes deadlines, and
escalates.

Workflow: notification_received → document_collection → management_review
→ submitted → follow_up → closed. Forward-only, one step at a time, and
the transition to SUBMITTED is gated on a recorded management-review
sign-off (service check + DB trigger — compliance at the edge applied to
outbound documents).
"""

from __future__ import annotations

import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.core.errors import GovconError
from govcon.models import AuditAlertLog, AuditNotification
from govcon.models.monitoring import AlertPoint, AuditType, NotificationStatus

#: T-minus escalation points (days before the computed deadline). Points
#: that don't fit a shorter response window are skipped — an alert can't
#: fire before the notification exists.
ALERT_SCHEDULE: tuple[tuple[AlertPoint, int], ...] = (
    (AlertPoint.T_30, 30),
    (AlertPoint.T_14, 14),
    (AlertPoint.T_7, 7),
    (AlertPoint.T_3, 3),
    (AlertPoint.T_1, 1),
)

#: Forward-only workflow order (§13).
STATUS_ORDER = (
    NotificationStatus.NOTIFICATION_RECEIVED,
    NotificationStatus.DOCUMENT_COLLECTION,
    NotificationStatus.MANAGEMENT_REVIEW,
    NotificationStatus.SUBMITTED,
    NotificationStatus.FOLLOW_UP,
    NotificationStatus.CLOSED,
)

#: Alerts stop once the response is out the door.
ALERTS_STOP_AT = {
    NotificationStatus.SUBMITTED,
    NotificationStatus.FOLLOW_UP,
    NotificationStatus.CLOSED,
}


class AuditWorkflowError(GovconError):
    pass


def create_notification(
    session: Session,
    *,
    audit_type: AuditType,
    received_date: datetime.date,
    response_deadline_days: int,
    requested_documents: str | None = None,
) -> AuditNotification:
    """SYNTHETIC fixture only. computed_deadline_date is derived from the
    stored day count, never entered separately."""
    notification = AuditNotification(
        audit_type=audit_type,
        received_date=received_date,
        response_deadline_days=response_deadline_days,
        computed_deadline_date=received_date + datetime.timedelta(days=response_deadline_days),
        requested_documents=requested_documents,
    )
    session.add(notification)
    session.flush()
    return notification


def fire_due_alerts(
    session: Session, notification: AuditNotification, as_of: datetime.date
) -> list[AuditAlertLog]:
    """Fire every T-minus alert whose window has opened, idempotently —
    each point fires at most once per notification, and each firing lands
    in audit_alert_log (an alert with no firing record is 'a database flag
    nobody looks at')."""
    if notification.status in ALERTS_STOP_AT:
        return []
    already = set(
        session.execute(
            sa.select(AuditAlertLog.alert_point).where(
                AuditAlertLog.notification_id == notification.notification_id
            )
        ).scalars()
    )
    fired: list[AuditAlertLog] = []
    for point, days in ALERT_SCHEDULE:
        if days > notification.response_deadline_days:
            continue  # point doesn't fit the response window — skipped
        if point in already:
            continue
        if as_of >= notification.computed_deadline_date - datetime.timedelta(days=days):
            alert = AuditAlertLog(
                notification_id=notification.notification_id,
                alert_point=point,
                fired_at=datetime.datetime.now(datetime.timezone.utc),
            )
            session.add(alert)
            fired.append(alert)
    session.flush()
    return fired


def acknowledge_alert(
    session: Session, alert: AuditAlertLog, *, acknowledged_by: str
) -> AuditAlertLog:
    alert.acknowledged_by = acknowledged_by
    alert.acknowledged_at = datetime.datetime.now(datetime.timezone.utc)
    session.flush()
    return alert


def record_management_review(
    session: Session, notification: AuditNotification, *, reviewed_by: str
) -> AuditNotification:
    """The sign-off the SUBMITTED transition is gated on."""
    if notification.status != NotificationStatus.MANAGEMENT_REVIEW:
        raise AuditWorkflowError(
            "management review is recorded during the management_review stage "
            f"(current status: {notification.status.value})"
        )
    notification.reviewed_by = reviewed_by
    notification.reviewed_at = datetime.datetime.now(datetime.timezone.utc)
    session.flush()
    return notification


def advance_status(
    session: Session, notification: AuditNotification, new_status: NotificationStatus
) -> AuditNotification:
    """Forward-only, one step at a time; SUBMITTED requires the recorded
    sign-off (the DB trigger backstops raw SQL)."""
    current_idx = STATUS_ORDER.index(notification.status)
    new_idx = STATUS_ORDER.index(new_status)
    if new_idx != current_idx + 1:
        raise AuditWorkflowError(
            f"invalid transition {notification.status.value} → {new_status.value}: "
            "the workflow is forward-only, one stage at a time (§13)"
        )
    if new_status == NotificationStatus.SUBMITTED and (
        notification.reviewed_by is None or notification.reviewed_at is None
    ):
        raise AuditWorkflowError(
            "cannot submit without a management-review sign-off — the response "
            "does not leave the building unreviewed (§13)"
        )
    notification.status = new_status
    session.flush()
    return notification
