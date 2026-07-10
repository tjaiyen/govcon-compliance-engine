"""Regulation-watch suggestions (enterprise vision Phase 3).

A row here is a SEARCH RESULT the Federal Register watcher recorded for a
human to review — never a determination, never an applied change. The watcher
has no write path to regulatory_thresholds or the decision tables by
construction; acting on a suggestion means a person verifies the primary
source and lands a migration (the same discipline every threshold change has
always followed).

Fetched text (title/abstract) is untrusted DATA: it is stored inert and
rendered escaped; nothing in the engine parses it back into behavior.

Workflow state (new → reviewed/dismissed) is mutable; rows are never deleted
(a no-delete trigger in migration 0016 keeps dismissed suggestions as
history).
"""

from __future__ import annotations

import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from govcon.db.base import Base
from govcon.models.enums import SuggestionStatus, db_enum


class RegulatorySuggestion(Base):
    __tablename__ = "regulatory_suggestions"
    __table_args__ = (
        sa.UniqueConstraint(
            "source",
            "document_number",
            "watch_rule",
            name="uq_regulatory_suggestions_doc_rule",
        ),
    )

    suggestion_id: Mapped[int] = mapped_column(primary_key=True)
    #: What the engine was watching when this surfaced — a threshold rule_name
    #: (e.g. "TINA_THRESHOLD") or "decision:<table>.<rule_key>".
    watch_rule: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    source: Mapped[str] = mapped_column(
        sa.String(30), nullable=False, server_default="federal_register"
    )
    document_number: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    doc_type: Mapped[str | None] = mapped_column(sa.String(30))
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    publication_date: Mapped[datetime.date | None] = mapped_column(sa.Date)
    effective_on: Mapped[datetime.date | None] = mapped_column(sa.Date)
    url: Mapped[str | None] = mapped_column(sa.Text)
    excerpt: Mapped[str | None] = mapped_column(sa.Text)
    #: True when the watch term literally appears in the title/abstract —
    #: full-text search matches loosely (a probe surfaced estate-tax rules
    #: for a CAS query), so weak matches are kept but flagged, never
    #: silently dropped.
    strong_match: Mapped[bool] = mapped_column(nullable=False, server_default=sa.false())
    fetched_at: Mapped[datetime.datetime] = mapped_column(sa.DateTime, nullable=False)
    status: Mapped[SuggestionStatus] = mapped_column(
        db_enum(SuggestionStatus, "suggestion_status"),
        nullable=False,
        server_default=SuggestionStatus.NEW.value,
    )
    review_note: Mapped[str | None] = mapped_column(sa.Text)
    reviewed_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime)
