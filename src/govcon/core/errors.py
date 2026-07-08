"""Typed domain errors raised by the ORM-layer guards.

Each has a database-level backstop (trigger / CHECK) created in the Alembic
migrations — these exceptions are the friendly, testable layer that fires
first in normal ORM use.
"""


class GovconError(Exception):
    """Base class for all domain errors."""


class AppendOnlyViolation(GovconError):
    """UPDATE/DELETE attempted on an append-only table.

    Corrections are reversing entries referencing the original row via
    ``superseded_by`` — see services.corrections.post_correction().
    """


class ImmutableFieldError(GovconError):
    """A frozen column (e.g. contracts.award_date or a threshold snapshot)
    was modified after insert. Create a new contract version instead —
    see services.versioning.supersede_contract()."""


class ClosedPeriodError(GovconError):
    """A transaction was posted to a period whose status is not 'open'
    (architecture spec §11 item 1)."""


class DirectCostWithoutContractError(GovconError):
    """A transaction on a direct-cost account has no contract_id
    (SF 1408 criterion B, enforced at the edge)."""


class RateCalculationError(GovconError):
    """A rate calculation precondition failed — e.g. a missing or
    non-positive allocation base (SF 1408 criterion C: fail loudly, never
    divide by zero or silently skip the pool)."""
