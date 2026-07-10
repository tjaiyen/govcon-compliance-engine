"""Grounding verifier — the anti-hallucination guard (spirit of ruflo-ground.mjs).

After the tool-use loop, the verifier extracts every dollar figure and citation
identifier from the model's prose and asserts each traces to a value the engine
actually returned (the GroundingLedger). A number the model asserts that never
came back from a tool is an UNGROUNDED CLAIM.

Design choices (see the plan's riskiest-decisions notes):
  * Verify VALUES (dollar amounts) + CITATION identifiers (statute/rule refs),
    not whole sentences — the model may legitimately paraphrase.
  * Normalize money to a canonical numeric form so "$35,000,000.00",
    "$35,000,000", "$35 million", and "35000000.00" all match the ledger's
    "35000000.00".
  * DEGRADE, don't hard-fail: a violation flags the answer (the caller then
    returns the authoritative determination instead of unverified prose). The
    determination is always present, so a verifier miss never costs ground truth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from govcon.ai.dispatch import GroundingLedger

# $12,000,000 | $12,000,000.00 | $12M | $12 million | 12000000.00
_MONEY = re.compile(
    r"\$?\s?([0-9][0-9,]*(?:\.[0-9]+)?)\s*(million|billion|thousand|m|bn|k)?\b",
    re.IGNORECASE,
)
_SCALE = {
    "thousand": Decimal(1_000), "k": Decimal(1_000),
    "million": Decimal(1_000_000), "m": Decimal(1_000_000),
    "billion": Decimal(1_000_000_000), "bn": Decimal(1_000_000_000),
}
# Regulatory citation identifiers the model might state (FAR 15.403-4, 48 CFR
# 9903.201-2, 10 U.S.C. 3703, 91 FR 42139, P.L. 119-60, DFARS 215.403-3).
_CITE = re.compile(
    r"\b(?:FAR|DFARS|CFR|U\.?S\.?C\.?|FR|P\.?L\.?|CAS)\b[\s\w§.\-/()]*\d",
    re.IGNORECASE,
)


@dataclass
class GroundingResult:
    verified: bool
    violations: list[str] = field(default_factory=list)


def _canonical_money(number: str, scale: str | None) -> set[str]:
    """All canonical forms a money mention could match in the ledger."""
    try:
        base = Decimal(number.replace(",", ""))
    except InvalidOperation:
        return set()
    value = base * _SCALE[scale.lower()] if scale and scale.lower() in _SCALE else base
    forms = set()
    # integer-ish and 2dp forms (the ledger stores "35000000.00")
    forms.add(format(value, "f"))
    forms.add(format(value.quantize(Decimal("1")), "f"))
    forms.add(format(value.quantize(Decimal("0.01")), "f"))
    return forms


def _ledger_numeric(ledger: GroundingLedger) -> set[str]:
    """The ledger's numeric values normalized to canonical Decimal text."""
    out = set()
    for raw in ledger.values:
        try:
            d = Decimal(raw.replace(",", ""))
        except (InvalidOperation, AttributeError):
            continue
        out.add(format(d, "f"))
        out.add(format(d.quantize(Decimal("1")), "f"))
        out.add(format(d.quantize(Decimal("0.01")), "f"))
    return out


def _norm_cite(s: str) -> str:
    return re.sub(r"[\s.]+", "", s).lower()


class GroundingVerifier:
    #: Small integers/years the model may use conversationally without a tool
    #: (2026, 6, "four exceptions"); money-scale guards exclude these from the
    #: dollar-figure check by requiring a $ or a scale word for large values.
    _SMALL = {Decimal(n) for n in range(0, 101)}

    def verify(self, prose: str, ledger: GroundingLedger) -> GroundingResult:
        violations: list[str] = []
        ledger_nums = _ledger_numeric(ledger)
        ledger_cites = {_norm_cite(c) for c in ledger.citations}

        for m in _MONEY.finditer(prose):
            number, scale = m.group(1), m.group(2)
            has_dollar = m.group(0).lstrip().startswith("$")
            forms = _canonical_money(number, scale)
            if not forms:
                continue
            # Only police amounts that LOOK like money: a $ sign, a scale word,
            # or a large value FORMATTED as money (thousands-comma or decimal).
            # A bare 4-digit integer like a year (2026) or a small count is
            # conversational, not a compliance claim.
            magnitude = min((Decimal(f) for f in forms), default=Decimal(0))
            money_formatted = ("," in number) or ("." in number)
            looks_like_money = (
                has_dollar or bool(scale)
                or (magnitude >= Decimal(1000) and money_formatted)
            )
            if not looks_like_money:
                continue
            if not (forms & ledger_nums):
                violations.append(f"ungrounded amount: {m.group(0).strip()}")

        for m in _CITE.finditer(prose):
            token = _norm_cite(m.group(0))
            if not any(token in c or c in token for c in ledger_cites):
                violations.append(f"ungrounded citation: {m.group(0).strip()}")

        return GroundingResult(verified=not violations, violations=violations)
