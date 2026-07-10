"""Grounding verifier — the anti-hallucination guard (spirit of ruflo-ground.mjs).

After the tool-use loop, the verifier extracts every dollar figure and citation
identifier from the model's prose and asserts each traces to a value the engine
actually returned (the GroundingLedger). A number the model asserts that never
came back from a tool is an UNGROUNDED CLAIM.

Design choices (see the plan's riskiest-decisions notes):
  * Verify VALUES (dollar amounts) + CITATION identifiers (statute/rule refs),
    not whole sentences — the model may legitimately paraphrase.
  * Normalize money to a canonical numeric form so "$35,000,000.00",
    "$35,000,000", "$35 million", "fifty million", and "35000000.00" all match
    the ledger's "35000000.00".
  * DEGRADE, don't hard-fail: a violation flags the answer (the caller then
    returns the authoritative determination instead of unverified prose). The
    determination is always present, so a verifier miss never costs ground truth.
  * NEVER crash: every quantize/parse is guarded — a hostile/huge number in the
    prose must be a caught violation, never an unhandled exception that 500s.

Hard-graded stress-test fixes (this pass):
  * bare unformatted large numbers ("50000000") are now policed — a hallucinated
    threshold no longer slips just because it lacks a $/comma/decimal;
  * spelled-out amounts ("fifty million dollars") are parsed and checked;
  * citation shorthand ("FAR15.403-4", "USC3703") is matched;
  * extreme Decimals (1e400, NaN, ∞) can no longer raise from quantize.
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
#: A currency unit word immediately AFTER a bare number ("50000 dollars") makes
#: it a money claim regardless of size/formatting — closing the bare-integer
#: hole below the _BARE_MONEY_FLOOR. Years/counts have no such unit word.
_CURRENCY_WORD = re.compile(r"\s*(?:dollars?|usd)\b", re.IGNORECASE)


def _trailing_currency(prose: str, end: int) -> bool:
    return bool(_CURRENCY_WORD.match(prose[end:end + 12]))
_SCALE = {
    "thousand": Decimal(1_000), "k": Decimal(1_000),
    "million": Decimal(1_000_000), "m": Decimal(1_000_000),
    "billion": Decimal(1_000_000_000), "bn": Decimal(1_000_000_000),
}
#: A bare, unformatted integer at or above this magnitude is treated as a money
#: claim even without a $/comma/decimal. Years (≤ 9999) and small counts stay
#: below it; every real engine threshold (SAT $350K … CAS $100M) is above it.
_BARE_MONEY_FLOOR = Decimal(100_000)
#: quantize() raises InvalidOperation once a value's exponent blows past the
#: Decimal context; anything this large is not a real dollar amount anyway.
_MAX_SANE = Decimal("1e15")

# Regulatory citation identifiers (FAR 15.403-4, 48 CFR 9903.201-2,
# 10 U.S.C. 3703, 91 FR 42139, P.L. 119-60, DFARS 215.403-3). No `\b` after the
# prefix, and the following space is optional, so shorthand like "FAR15.403-4"
# and "USC3703" is matched too (over-matching only withholds prose — the safe
# direction for an anti-hallucination gate).
_CITE = re.compile(
    r"\b(?:FAR|DFARS|CFR|U\.?S\.?C\.?|FR|P\.?L\.?|CAS)\.?\s*[\w§.\-/()]*\d",
    re.IGNORECASE,
)

# --- spelled-out amounts ("fifty million") -----------------------------------
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_WORD_SCALE = {"thousand": Decimal(1_000), "million": Decimal(1_000_000),
               "billion": Decimal(1_000_000_000)}
# a run of number-words ending in a scale word: "one hundred fifty million"
_SPELLED = re.compile(
    r"\b((?:(?:"
    + "|".join(list(_UNITS) + list(_TENS) + ["hundred", "and", "\\s"])
    + r")+))\s*(thousand|million|billion)\b",
    re.IGNORECASE,
)


@dataclass
class GroundingResult:
    verified: bool
    violations: list[str] = field(default_factory=list)


def _safe_quant_forms(value: Decimal) -> set[str]:
    """Canonical text forms of a Decimal, never raising on extreme values."""
    if not value.is_finite() or abs(value) > _MAX_SANE:
        return set()
    forms = set()
    try:
        forms.add(format(value, "f"))
        forms.add(format(value.quantize(Decimal("1")), "f"))
        forms.add(format(value.quantize(Decimal("0.01")), "f"))
    except InvalidOperation:  # pragma: no cover - guarded by _MAX_SANE
        return set()
    return forms


def _canonical_money(number: str, scale: str | None) -> set[str]:
    """All canonical forms a money mention could match in the ledger."""
    try:
        base = Decimal(number.replace(",", ""))
    except InvalidOperation:
        return set()
    if not base.is_finite():
        return set()
    value = base * _SCALE[scale.lower()] if scale and scale.lower() in _SCALE else base
    return _safe_quant_forms(value)


def _ledger_numeric(ledger: GroundingLedger) -> set[str]:
    """The ledger's numeric values normalized to canonical Decimal text."""
    out: set[str] = set()
    for raw in ledger.values:
        try:
            d = Decimal(str(raw).replace(",", ""))
        except (InvalidOperation, AttributeError):
            continue
        out |= _safe_quant_forms(d)
    return out


def _spelled_value(words: str) -> Decimal | None:
    """Parse a spelled number run (without the scale) into a Decimal, or None."""
    total = Decimal(0)
    current = Decimal(0)
    seen = False
    for tok in re.split(r"[\s-]+", words.strip().lower()):
        if not tok or tok == "and":
            continue
        if tok in _UNITS:
            current += _UNITS[tok]
            seen = True
        elif tok in _TENS:
            current += _TENS[tok]
            seen = True
        elif tok == "hundred":
            current = (current or Decimal(1)) * 100
            seen = True
        else:
            return None
    return (total + current) if seen else None


def _norm_cite(s: str) -> str:
    return re.sub(r"[\s.]+", "", s).lower()


class GroundingVerifier:
    def verify(self, prose: str, ledger: GroundingLedger) -> GroundingResult:
        violations: list[str] = []
        ledger_nums = _ledger_numeric(ledger)
        ledger_cites = {_norm_cite(c) for c in ledger.citations}

        for m in _MONEY.finditer(prose):
            number, scale = m.group(1), m.group(2)
            has_dollar = m.group(0).lstrip().startswith("$")
            forms = _canonical_money(number, scale)
            if not forms:
                # unparseable / extreme number (huge, NaN, ∞): it cannot be in
                # the ledger, so flag it whenever it looks money-sized — a $/
                # scale word, or 6+ integer digits (≥ _BARE_MONEY_FLOOR).
                int_digits = len(number.split(".")[0].replace(",", "").lstrip("0"))
                if has_dollar or scale or int_digits >= 6 or _trailing_currency(prose, m.end()):
                    violations.append(f"ungrounded amount: {m.group(0).strip()}")
                continue
            # Police amounts that LOOK like money: a $ sign, a scale word, a
            # value formatted as money (comma/decimal), OR a bare integer large
            # enough to be a threshold (≥ _BARE_MONEY_FLOOR). Bare small
            # integers (years, counts) stay conversational.
            magnitude = min((abs(Decimal(f)) for f in forms), default=Decimal(0))
            money_formatted = ("," in number) or ("." in number)
            looks_like_money = (
                has_dollar or bool(scale)
                or (magnitude >= Decimal(1000) and money_formatted)
                or magnitude >= _BARE_MONEY_FLOOR
                or _trailing_currency(prose, m.end())
            )
            if not looks_like_money:
                continue
            if not (forms & ledger_nums):
                violations.append(f"ungrounded amount: {m.group(0).strip()}")

        for m in _SPELLED.finditer(prose):
            base = _spelled_value(m.group(1))
            if base is None:
                continue
            value = base * _WORD_SCALE[m.group(2).lower()]
            forms = _safe_quant_forms(value)
            if forms and not (forms & ledger_nums):
                violations.append(f"ungrounded amount: {m.group(0).strip()}")

        for m in _CITE.finditer(prose):
            token = _norm_cite(m.group(0))
            if not any(token in c or c in token for c in ledger_cites):
                violations.append(f"ungrounded citation: {m.group(0).strip()}")

        return GroundingResult(verified=not violations, violations=violations)
