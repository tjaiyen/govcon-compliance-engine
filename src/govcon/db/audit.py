"""Hash-chained audit trail (architecture spec §0.1 audit_trail + CLAUDE.md
ground rule 5), wired as ONE session-level after_flush listener — every
mapped table is captured automatically, no per-model code.

Chain design:
- genesis previous_entry_hash = "0" * 64 (named constant, no special-case data)
- entry_hash = SHA-256 over canonical JSON of the full payload (sorted keys,
  compact separators, Decimals as canonical fixed-point text, dates ISO,
  enums by value) — every hashed field is stored verbatim, so the chain is
  recomputable from the table alone (verify_audit_chain).
- audit rows are written with a Core insert, which does not re-enter ORM
  flush events (no recursion).
- SQLite's single-writer serializes the read-last-hash/insert pair; on
  Postgres this section would need an advisory lock.
"""

from __future__ import annotations

import datetime
import enum
import hashlib
import json
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm import Session

from govcon.core.identity import current_actor
from govcon.models import AuditTrail

GENESIS_HASH = "0" * 64
AUDIT_EXEMPT = {"audit_trail"}


def _canon_default(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    raise TypeError(f"unhashable audit value type: {type(value)!r}")


def canonical_json(payload) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_canon_default,
    )


def compute_entry_hash(payload: dict) -> str:
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def _pk_of(obj) -> str:
    """Primary key as text. Reads the PK column ATTRIBUTES (populated by the
    time after_flush runs for inserts) — InstanceState.identity is NOT yet
    set inside after_flush, which made this silently return '?' for every
    insert until a v1.1 test caught it (new_values always carried the real
    pk, so the chain stayed complete — record_id was the degraded field)."""
    mapper = sa.inspect(obj).mapper
    values = [
        getattr(obj, mapper.get_property_by_column(col).key) for col in mapper.primary_key
    ]
    return "/".join("?" if v is None else str(v) for v in values)


def _column_values(obj) -> dict:
    return {c.key: getattr(obj, c.key) for c in sa.inspect(obj).mapper.column_attrs}


def _old_values_of(obj) -> dict:
    """Pre-change values for a dirty object, from attribute history."""
    state = sa.inspect(obj)
    old = {}
    for attr in state.mapper.column_attrs:
        hist = state.attrs[attr.key].history
        if hist.has_changes() and hist.deleted:
            old[attr.key] = hist.deleted[0]
        else:
            old[attr.key] = getattr(obj, attr.key)
    return old


def _collect_changes(session: Session) -> list[tuple[str, str, str, dict | None, dict | None]]:
    changes = []
    for obj in session.new:
        if obj.__table__.name in AUDIT_EXEMPT:
            continue
        changes.append((obj.__table__.name, _pk_of(obj), "insert", None, _column_values(obj)))
    for obj in session.dirty:
        if obj.__table__.name in AUDIT_EXEMPT or not session.is_modified(obj):
            continue
        changes.append(
            (obj.__table__.name, _pk_of(obj), "update", _old_values_of(obj), _column_values(obj))
        )
    for obj in session.deleted:
        if obj.__table__.name in AUDIT_EXEMPT:
            continue
        changes.append((obj.__table__.name, _pk_of(obj), "delete", _column_values(obj), None))
    # Deterministic ordering within a flush.
    changes.sort(key=lambda c: (c[0], c[2], c[1].zfill(12)))
    return changes


@event.listens_for(Session, "after_flush")
def write_audit_rows(session: Session, _ctx) -> None:
    changes = _collect_changes(session)
    if not changes:
        return
    prev = session.execute(
        sa.select(AuditTrail.entry_hash).order_by(AuditTrail.trail_id.desc()).limit(1)
    ).scalar()
    prev = prev or GENESIS_HASH
    actor = current_actor()  # resolved once per flush — one flush, one actor
    for table, record_id, action, old, new in changes:
        timestamp = datetime.datetime.now(datetime.UTC).isoformat()
        payload = {
            "table_name": table,
            "record_id": record_id,
            "action": action,
            "old_values": old,
            "new_values": new,
            "user_id": actor,
            "timestamp": timestamp,
            "previous_entry_hash": prev,
        }
        entry_hash = compute_entry_hash(payload)
        session.execute(
            sa.insert(AuditTrail.__table__).values(
                table_name=table,
                record_id=record_id,
                action=action,
                old_values=None if old is None else canonical_json(old),
                new_values=None if new is None else canonical_json(new),
                user_id=actor,
                timestamp=timestamp,
                previous_entry_hash=prev,
                entry_hash=entry_hash,
            )
        )
        prev = entry_hash


def verify_audit_chain(session: Session) -> tuple[bool, int | None]:
    """Recompute every hash in trail_id order.

    Returns (True, None) if the chain verifies, else (False, trail_id) of
    the first row whose stored hash or linkage does not recompute.
    """
    prev = GENESIS_HASH
    # Contiguity check (a stress test noted mid-chain row deletion was
    # undetectable): trail_ids are a gapless 1..N sequence, so a gap means a
    # row was deleted out of band even if the surviving rows still hash-link.
    count, max_id = session.execute(
        sa.select(sa.func.count(AuditTrail.trail_id), sa.func.max(AuditTrail.trail_id))
    ).one()
    if count and max_id != count:
        # find the first missing id for the report
        present = set(
            session.execute(sa.select(AuditTrail.trail_id)).scalars()
        )
        first_gap = next(i for i in range(1, max_id + 1) if i not in present)
        return False, first_gap
    rows = session.execute(
        sa.select(AuditTrail).order_by(AuditTrail.trail_id)
    ).scalars()
    for row in rows:
        payload = {
            "table_name": row.table_name,
            "record_id": row.record_id,
            "action": row.action.value if isinstance(row.action, enum.Enum) else row.action,
            "old_values": None if row.old_values is None else json.loads(row.old_values),
            "new_values": None if row.new_values is None else json.loads(row.new_values),
            "user_id": row.user_id,
            "timestamp": row.timestamp,
            "previous_entry_hash": prev,
        }
        if row.previous_entry_hash != prev or compute_entry_hash(payload) != row.entry_hash:
            return False, row.trail_id
        prev = row.entry_hash
    return True, None
