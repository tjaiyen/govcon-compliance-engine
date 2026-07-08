"""The only sanctioned change path for contracts (frozen columns are
trigger-enforced immutable): create a v+1 row and point the old row's
superseded_by at it. "Current" = highest version with no superseded_by."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.models import Contract


def supersede_contract(session: Session, old: Contract, **changes) -> Contract:
    """Create the next version of a contract. Frozen fields carry over
    unchanged unless explicitly overridden in `changes` (which is legal on
    the NEW row — immutability binds rows, not the contract lineage)."""
    fields = {
        c.key: getattr(old, c.key)
        for c in sa.inspect(Contract).mapper.column_attrs
        if c.key not in ("contract_id", "version", "superseded_by")
    }
    fields.update(changes)
    new = Contract(**fields, version=old.version + 1)
    session.add(new)
    session.flush()  # assigns new.contract_id
    old.superseded_by = new.contract_id
    session.flush()
    return new
