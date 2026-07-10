"""regulatory_suggestions — regulation-watch inbox (enterprise Phase 3)

A row is a Federal Register search result recorded for HUMAN review; the
watcher never writes to regulatory_thresholds or the decision tables. Status
transitions (new -> reviewed/dismissed) are legitimate workflow UPDATEs, so
unlike the append-only regulatory tables this one allows updates — but rows
are never deleted (a dismissed suggestion is history, not garbage), enforced
by a no-delete trigger. Seeds nothing: suggestions only ever come from a
scan.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, Sequence[str], None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NO_DELETE = (
    "CREATE TRIGGER trg_regulatory_suggestions_no_delete BEFORE DELETE ON "
    "regulatory_suggestions BEGIN SELECT RAISE(ABORT, 'regulatory_suggestions "
    "rows are never deleted; dismiss instead'); END"
)


def upgrade() -> None:
    op.create_table(
        "regulatory_suggestions",
        sa.Column("suggestion_id", sa.Integer(), primary_key=True),
        sa.Column("watch_rule", sa.String(120), nullable=False),
        sa.Column(
            "source", sa.String(30), nullable=False, server_default="federal_register"
        ),
        sa.Column("document_number", sa.String(30), nullable=False),
        sa.Column("doc_type", sa.String(30), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("effective_on", sa.Date(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column(
            "strong_match", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "new",
                "reviewed",
                "dismissed",
                name="suggestion_status",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
            server_default="new",
        ),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "source",
            "document_number",
            "watch_rule",
            name="uq_regulatory_suggestions_doc_rule",
        ),
    )
    if op.get_bind().dialect.name == "sqlite":
        # the plpgsql equivalent is created by migration 0017
        op.execute(sa.text(_NO_DELETE))


def downgrade() -> None:
    # SQLite drops a table's triggers with the table itself.
    op.drop_table("regulatory_suggestions")
