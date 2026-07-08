"""Penny-exact numeric storage (CLAUDE.md ground rule 4a).

SQLAlchemy's plain ``Numeric`` round-trips through float on SQLite, which
silently destroys penny-exactness. ``SafeNumeric`` is native NUMERIC on
Postgres and canonical fixed-point TEXT on SQLite, and rejects ``float`` at
bind time.

Documented consequence of TEXT storage: SQL-level ``SUM()``/numeric
comparisons on SQLite go through float — all financial aggregation happens
in Python over ``Decimal``, never in SQL, until/unless this runs on
Postgres.
"""

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.types import TypeDecorator

from govcon.core.decimal_config import ROUNDING  # also sets the global context


class SafeNumeric(TypeDecorator):
    impl = sa.Numeric
    cache_ok = True

    def __init__(self, precision: int = 18, scale: int = 2):
        super().__init__()
        self.precision = precision
        self.scale = scale
        self._quantum = Decimal(1).scaleb(-scale)

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(sa.Text())
        return dialect.type_descriptor(sa.Numeric(self.precision, self.scale))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, float):
            raise TypeError(
                "float is forbidden for financial values (CLAUDE.md ground "
                "rule 4a); pass decimal.Decimal"
            )
        quantized = Decimal(value).quantize(self._quantum, rounding=ROUNDING)
        # format(..., "f"): no exponent notation — one canonical text form.
        return format(quantized, "f") if dialect.name == "sqlite" else quantized

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(str(value))


def Money() -> SafeNumeric:
    """Dollar amounts: NUMERIC(18, 2)."""
    return SafeNumeric(18, 2)


def Rate() -> SafeNumeric:
    """Calculated rates, quantized to 4dp per architecture spec §5."""
    return SafeNumeric(12, 4)
