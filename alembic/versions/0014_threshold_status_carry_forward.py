"""Widen threshold_status CHECK to add the carry_forward state.

Adds a fifth ThresholdStatus value, ``carry_forward``, for a regulatory value
that is in force by carry-forward — the prior-period value still governs because
a scheduled periodic adjustment was formally waived/frozen by an authority
(e.g. an OMB memo cancelling an annual inflation adjustment). This is distinct
from a settled value AND from an unseeded gap (which raises): a carried-forward
row returns a real value but is non-final, so it rides a status caveat and the
``govcon reverify`` watch list until the freeze lifts.

The status column is a VARCHAR + CHECK (non-native) enum, so widening it means
recreating the CHECK — a SQLite batch rebuild of ``regulatory_thresholds``.
That table is append-only (triggers added in 0013) and is the target of an FK
from ``contracts.cas_trigger_threshold_id``; this mirrors the batch-rebuild +
trigger-dance pattern used for ``gl_accounts`` in 0011/0013 (drop the table's
own triggers, rebuild, recreate them verbatim).

This migration seeds NO data row: no current engine threshold is in the
carry_forward state (the motivating precedent, OMB M-26-11, governs civil
monetary penalties, which the engine does not model — and the exec-comp cap is
ECI-set, not CMP-Act). The capability is latent until a real freeze occurs, at
which point a value is seeded via a new migration — never invented here.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ENUM_NAME = "threshold_status"
_OLD_VALUES = ("statute", "proposed_rule", "final_rule", "class_deviation")
_NEW_VALUES = ("statute", "proposed_rule", "final_rule", "class_deviation", "carry_forward")

# The two append-only triggers 0013 created for this table (reproduced verbatim
# so they are recreated identically around the batch rebuild).
_APPEND_ONLY_DDL = (
    "CREATE TRIGGER trg_regulatory_thresholds_no_update BEFORE UPDATE ON "
    "regulatory_thresholds BEGIN SELECT RAISE(ABORT, 'regulatory_thresholds is "
    "append-only; a change is a new row'); END",
    "CREATE TRIGGER trg_regulatory_thresholds_no_delete BEFORE DELETE ON "
    "regulatory_thresholds BEGIN SELECT RAISE(ABORT, 'regulatory_thresholds is "
    "append-only; rows are never deleted'); END",
)


def _status_type(values: Sequence[str]) -> sa.Enum:
    return sa.Enum(
        *values,
        name=_ENUM_NAME,
        native_enum=False,
        create_constraint=True,
        length=40,
    )


def _rebuild_status_check(old_values: Sequence[str], new_values: Sequence[str]) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Non-native enum = a named CHECK constraint (naming convention
        # ck_<table>_<enum name>, verified live on PG 17). Postgres can swap
        # it in place — no table rebuild, no trigger dance (the plpgsql
        # triggers from 0017 are unaffected by constraint DDL).
        values = ", ".join(f"'{v}'" for v in new_values)
        op.execute(sa.text(
            "ALTER TABLE regulatory_thresholds "
            "DROP CONSTRAINT ck_regulatory_thresholds_threshold_status"
        ))
        op.execute(sa.text(
            "ALTER TABLE regulatory_thresholds "
            "ADD CONSTRAINT ck_regulatory_thresholds_threshold_status "
            f"CHECK (status IN ({values}))"
        ))
        return
    if bind.dialect.name != "sqlite":  # pragma: no cover - unknown dialect
        raise NotImplementedError(
            "threshold_status CHECK rebuild implemented for sqlite/postgresql only"
        )
    for name in (
        "trg_regulatory_thresholds_no_update",
        "trg_regulatory_thresholds_no_delete",
    ):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {name}"))
    with op.batch_alter_table("regulatory_thresholds", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=_status_type(old_values),
            type_=_status_type(new_values),
            existing_nullable=False,
        )
    for ddl in _APPEND_ONLY_DDL:
        op.execute(sa.text(ddl))


def upgrade() -> None:
    _rebuild_status_check(_OLD_VALUES, _NEW_VALUES)


def downgrade() -> None:
    # Safe: this migration seeds no carry_forward row, so nothing violates the
    # narrowed CHECK. (If a later migration seeds one, its own downgrade removes
    # it before this runs.)
    _rebuild_status_check(_NEW_VALUES, _OLD_VALUES)
