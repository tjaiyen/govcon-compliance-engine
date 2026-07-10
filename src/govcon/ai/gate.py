"""Synthetic-only hard gate (enterprise AI layer).

The engine's posture is synthetic-or-demo data. The AI layer sends prompts to
an external model (Claude API), so it is refused on anything but synthetic
mode — fail-closed: an unset variable defaults to synthetic, but any UNKNOWN
value is treated as real and the AI is refused. This turns "synthetic-only"
from documented prose into a tested runtime invariant.

Gated in TWO places (defence in depth): the HTTP dependency (fast reject) and
the kernel entry (so a non-HTTP caller — a future CLI — cannot bypass it).
"""

from __future__ import annotations

import os

from govcon.ai.errors import SyntheticGateError

_SYNTHETIC = "synthetic"


def data_mode() -> str:
    """The current data mode from GOVCON_DATA_MODE (default 'synthetic')."""
    return os.environ.get("GOVCON_DATA_MODE", _SYNTHETIC).strip().lower() or _SYNTHETIC


def is_synthetic() -> bool:
    return data_mode() == _SYNTHETIC


def assert_synthetic() -> None:
    """Raise SyntheticGateError unless GOVCON_DATA_MODE is exactly 'synthetic'.
    Any other value (including an unrecognized one) fails closed."""
    mode = data_mode()
    if mode != _SYNTHETIC:
        raise SyntheticGateError(
            f"AI is disabled outside synthetic-data mode (GOVCON_DATA_MODE={mode!r}); "
            "the AI layer never sends real data to an external model"
        )
