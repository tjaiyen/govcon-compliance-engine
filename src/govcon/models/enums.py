"""Enums per architecture spec §0.1. Stored as VARCHAR + CHECK constraint
(non-native enum) so the schema is identical on SQLite and Postgres."""

import enum


class AgencyType(str, enum.Enum):
    DOD = "dod"
    CIVILIAN = "civilian"


class CASCoverageType(str, enum.Enum):
    NONE = "none"
    MODIFIED = "modified"
    FULL = "full"


class ContractorSize(str, enum.Enum):
    SMALL = "small"
    OTHER_THAN_SMALL = "other_than_small"


class ContractActionType(str, enum.Enum):
    TASK_ORDER = "task_order"
    MODIFICATION = "modification"
    OTHER_NEGOTIATED_ACTION = "other_negotiated_action"


class PeriodStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


class ReconciliationStatus(str, enum.Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


class ThresholdStatus(str, enum.Enum):
    STATUTE = "statute"
    PROPOSED_RULE = "proposed_rule"
    FINAL_RULE = "final_rule"
    CLASS_DEVIATION = "class_deviation"
    # In force by carry-forward: the prior-period value still governs because a
    # scheduled periodic adjustment was formally waived/frozen by an authority
    # (e.g. an OMB memo cancelling an annual inflation adjustment). Distinct from
    # a settled value AND from an unseeded gap (which raises) — a carried-forward
    # row returns a real value but is non-final, so it rides a caveat and the
    # reverify watch list until the freeze lifts. Never a value invented in code.
    CARRY_FORWARD = "carry_forward"


class CostType(str, enum.Enum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    UNALLOWABLE = "unallowable"


class CostElement(str, enum.Enum):
    LABOR = "labor"
    MATERIAL = "material"
    TRAVEL = "travel"
    ODC = "odc"
    SUBCONTRACT = "subcontract"


class PoolName(str, enum.Enum):
    FRINGE = "fringe"
    OVERHEAD = "overhead"
    GA = "ga"


class RateType(str, enum.Enum):
    PROVISIONAL = "provisional"
    ACTUAL_FINAL = "actual_final"
    FORWARD_PRICING = "forward_pricing"


class PoolStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    LOCKED = "locked"


class FPRAStatus(str, enum.Enum):
    DRAFT = "draft"
    NEGOTIATED = "negotiated"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class DetectionMethod(str, enum.Enum):
    ACCOUNT_CODE = "account_code"
    KEYWORD_PATTERN = "keyword_pattern"
    RECEIPT_PARSING = "receipt_parsing"
    RATE_LOOKUP = "rate_lookup"


class AuditAction(str, enum.Enum):
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"


def db_enum(py_enum: type[enum.Enum], name: str):
    """VARCHAR + CHECK constraint enum, storing the .value strings."""
    import sqlalchemy as sa

    return sa.Enum(
        py_enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda e: [m.value for m in e],
        length=40,
    )
