"""The single place decimal context is configured (CLAUDE.md ground rule 4a).

Every module doing financial arithmetic imports from here. Importing this
module sets the process-wide decimal context exactly once; nothing else in
the codebase may call ``decimal.getcontext()`` to change precision/rounding.
"""

from decimal import ROUND_HALF_UP, Decimal, getcontext

#: Rounding mode for all quantization. HALF_UP matches ordinary accounting
#: expectations (0.005 -> 0.01), unlike Python's default ROUND_HALF_EVEN.
ROUNDING = ROUND_HALF_UP

#: Working precision for intermediate arithmetic (well above any stored scale).
PRECISION = 28

getcontext().prec = PRECISION

#: Quantum for dollar amounts: two decimal places.
CENT = Decimal("0.01")

#: Quantum for calculated rates: four decimal places (architecture spec §5).
RATE_4DP = Decimal("0.0001")


def quantize_money(value: Decimal) -> Decimal:
    """Quantize a dollar amount to the cent."""
    return Decimal(value).quantize(CENT, rounding=ROUNDING)


def quantize_rate(value: Decimal) -> Decimal:
    """Quantize a calculated rate to four decimal places."""
    return Decimal(value).quantize(RATE_4DP, rounding=ROUNDING)
