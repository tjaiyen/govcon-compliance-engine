"""audit_trail per architecture spec §0.1 — append-only, SHA-256 hash-chained.

Rows are written ONLY by the session-level listener in govcon.db.audit.
No update/delete is possible: ORM guard + RAISE(ABORT) triggers. The
timestamp is stored as the exact ISO TEXT string that was hashed, so the
chain is recomputable from this table alone.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from govcon.db.base import Base
from govcon.models.enums import AuditAction, db_enum


class AuditTrail(Base):
    __tablename__ = "audit_trail"

    trail_id: Mapped[int] = mapped_column(primary_key=True)
    table_name: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    record_id: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    action: Mapped[AuditAction] = mapped_column(
        db_enum(AuditAction, "audit_action"), nullable=False
    )
    old_values: Mapped[str | None] = mapped_column(sa.Text)  # canonical JSON
    new_values: Mapped[str | None] = mapped_column(sa.Text)  # canonical JSON
    user_id: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    timestamp: Mapped[str] = mapped_column(sa.String(40), nullable=False)  # ISO, verbatim-hashed
    previous_entry_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
