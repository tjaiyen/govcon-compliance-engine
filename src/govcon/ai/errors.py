"""Typed errors for the AI interaction layer."""

from __future__ import annotations


class AIError(Exception):
    """Base class for AI-layer failures."""


class SyntheticGateError(AIError):
    """Raised when an AI call is attempted outside synthetic-data mode. The AI
    layer is refused (fail-closed) on anything but GOVCON_DATA_MODE=synthetic —
    never send real contract/CUI/ITAR data to an external model."""


class GroundingError(AIError):
    """Raised when the model's prose asserts a value or citation that never
    appeared in a tool result (an ungrounded claim)."""


class CostCeilingError(AIError):
    """Raised when a single request would exceed its configured USD ceiling."""


class ToolDispatchError(AIError):
    """Raised when a requested tool is unknown or its input is malformed."""
