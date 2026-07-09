"""Phase 11 tables per architecture spec §0.1: pbr_fluctuation_notes (§12
during-the-year PBR monitoring), audit_notifications + audit_response_tasks
(§13 workflow simulator — SYNTHETIC notification fixtures only, never real
correspondence), and audit_alert_log (the T-minus escalation record)."""

from __future__ import annotations

import datetime
import enum
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from govcon.db.base import Base
from govcon.db.types import Money, SafeNumeric
from govcon.models.enums import db_enum


class AuditType(str, enum.Enum):
    INCURRED_COST = "incurred_cost"
    FORWARD_PRICING = "forward_pricing"
    SYSTEMS_CAS_TINA = "systems_cas_tina"
    CLAIMS_TERMINATIONS = "claims_terminations"


class NotificationStatus(str, enum.Enum):
    NOTIFICATION_RECEIVED = "notification_received"
    DOCUMENT_COLLECTION = "document_collection"
    MANAGEMENT_REVIEW = "management_review"
    SUBMITTED = "submitted"
    FOLLOW_UP = "follow_up"
    CLOSED = "closed"


class TaskStatus(str, enum.Enum):
    OPEN = "open"
    COMPLETE = "complete"
    OVERDUE = "overdue"


class AlertPoint(str, enum.Enum):
    T_30 = "t_30"
    T_14 = "t_14"
    T_7 = "t_7"
    T_3 = "t_3"
    T_1 = "t_1"


class PBRFluctuationNote(Base):
    __tablename__ = "pbr_fluctuation_notes"

    note_id: Mapped[int] = mapped_column(primary_key=True)
    pool_id: Mapped[int] = mapped_column(sa.ForeignKey("indirect_pools.pool_id"), nullable=False)
    period_id: Mapped[int] = mapped_column(sa.ForeignKey("periods.period_id"), nullable=False)
    variance_amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    variance_pct: Mapped[Decimal] = mapped_column(SafeNumeric(12, 4), nullable=False)
    explanation: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # The "explain it before an auditor asks" log — resolution requires a
    # named resolver + a real explanation (§12).
    resolved_by: Mapped[str | None] = mapped_column(sa.String(80))
    resolved_date: Mapped[datetime.date | None] = mapped_column(sa.Date)


class AuditNotification(Base):
    __tablename__ = "audit_notifications"

    notification_id: Mapped[int] = mapped_column(primary_key=True)
    audit_type: Mapped[AuditType] = mapped_column(db_enum(AuditType, "audit_type"), nullable=False)
    received_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    # Store the actual number, never hard-code it in logic (§0.1).
    response_deadline_days: Mapped[int] = mapped_column(nullable=False)
    computed_deadline_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    status: Mapped[NotificationStatus] = mapped_column(
        db_enum(NotificationStatus, "audit_notification_status"),
        default=NotificationStatus.NOTIFICATION_RECEIVED,
    )
    requested_documents: Mapped[str | None] = mapped_column(sa.Text)  # json list
    # The management-review sign-off gate: status cannot advance to
    # submitted while both are null (service + DB trigger).
    reviewed_by: Mapped[str | None] = mapped_column(sa.String(80))
    reviewed_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime)


class AuditResponseTask(Base):
    __tablename__ = "audit_response_tasks"

    task_id: Mapped[int] = mapped_column(primary_key=True)
    notification_id: Mapped[int] = mapped_column(
        sa.ForeignKey("audit_notifications.notification_id"), nullable=False
    )
    description: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    owner: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    due_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    completed_date: Mapped[datetime.date | None] = mapped_column(sa.Date)
    status: Mapped[TaskStatus] = mapped_column(
        db_enum(TaskStatus, "audit_task_status"), default=TaskStatus.OPEN
    )


class AuditAlertLog(Base):
    __tablename__ = "audit_alert_log"

    alert_id: Mapped[int] = mapped_column(primary_key=True)
    notification_id: Mapped[int] = mapped_column(
        sa.ForeignKey("audit_notifications.notification_id"), nullable=False
    )
    alert_point: Mapped[AlertPoint] = mapped_column(
        db_enum(AlertPoint, "alert_point"), nullable=False
    )
    fired_at: Mapped[datetime.datetime] = mapped_column(sa.DateTime, nullable=False)
    acknowledged_by: Mapped[str | None] = mapped_column(sa.String(80))
    acknowledged_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime)
