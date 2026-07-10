"""Tool dispatch + the grounding ledger.

``dispatch`` runs a requested tool under the caller's REAL Session and records
every value/citation the tool returned into a GroundingLedger, so the verifier
can later assert the model only quoted things the engine actually produced.

A service ``LookupError`` is already handled inside each runner (returns an
``available/in_force: False`` payload), matching the engine's "flag the gap,
never invent" discipline. Any other exception (bad tool input) is surfaced to
the model as an error tool_result rather than crashing the loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from govcon.ai.errors import ToolDispatchError
from govcon.ai.registry import TOOLS

#: Numeric tokens embedded inside tool-result strings (reasons/caveats carry the
#: dollar figures inline, e.g. "contract value 12000000.00 meets the 7500000.00
#: trigger"). The ledger must capture these so the verifier can ground prose
#: that quotes them.
_NUM_IN_TEXT = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass
class GroundingLedger:
    """Every scalar value + citation a tool returned this turn — the set of
    facts the model is allowed to state."""

    values: set[str] = field(default_factory=set)
    citations: list[str] = field(default_factory=list)

    def absorb(self, obj) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("source_citation", "description") and isinstance(v, str):
                    self.citations.append(v)
                self.absorb(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                self.absorb(v)
        elif isinstance(obj, bool):
            self.values.add(str(obj).lower())
        elif isinstance(obj, (int, float)):
            self.values.add(str(obj))
        elif isinstance(obj, str):
            self.values.add(obj)
            for token in _NUM_IN_TEXT.findall(obj):
                self.values.add(token)


@dataclass
class DispatchResult:
    tool: str
    input: dict
    result: dict
    is_error: bool = False


def dispatch(session: Session, tool_name: str, tool_input: dict, ledger: GroundingLedger) -> DispatchResult:
    spec = TOOLS.get(tool_name)
    if spec is None:
        raise ToolDispatchError(f"unknown tool {tool_name!r}")
    try:
        result = spec.run(session, tool_input or {})
    except (ValueError, KeyError) as exc:  # malformed tool input from the model
        return DispatchResult(tool=tool_name, input=tool_input, result={"error": str(exc)}, is_error=True)
    ledger.absorb(result)
    return DispatchResult(tool=tool_name, input=tool_input, result=result)
