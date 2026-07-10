"""The grounding verifier: an ungrounded number/citation is caught; ledger-only
prose passes; legitimate paraphrase of a ledger value passes."""

from govcon.ai.dispatch import GroundingLedger
from govcon.ai.grounding import GroundingVerifier


def _ledger(values=(), citations=()):
    lg = GroundingLedger()
    lg.values.update(values)
    lg.citations.extend(citations)
    return lg


def test_ungrounded_amount_is_flagged():
    lg = _ledger(values={"35000000.00"})
    r = GroundingVerifier().verify("The trigger is $99,000,000.", lg)
    assert not r.verified and any("99" in v for v in r.violations)


def test_grounded_amount_passes():
    lg = _ledger(values={"35000000.00"})
    r = GroundingVerifier().verify("The CAS trigger in force is $35,000,000.00.", lg)
    assert r.verified, r.violations


def test_paraphrased_magnitude_matches_ledger():
    lg = _ledger(values={"35000000.00"})
    # "$35 million" must normalize to the ledger's canonical form
    r = GroundingVerifier().verify("The trigger is about $35 million.", lg)
    assert r.verified, r.violations


def test_small_conversational_numbers_are_not_policed():
    lg = _ledger(values={"10000000.00"})
    # years and counts (2026, "four exceptions") are not money claims
    r = GroundingVerifier().verify(
        "In 2026 all four of the statutory exceptions were evaluated.", lg)
    assert r.verified, r.violations


def test_bare_dollar_amount_with_currency_word_is_policed():
    # the reviewer's hole: a bare 4-5 digit integer + a unit word ("dollars"/
    # "USD") must be treated as a money claim regardless of size/formatting.
    lg = _ledger(values={"7500000.00"})
    for prose in ("It is 50000 dollars.", "The cap is 99999 USD.", "about 8000 dollars"):
        r = GroundingVerifier().verify(prose, lg)
        assert not r.verified, f"should flag: {prose!r}"


def test_bare_amount_with_currency_word_passes_when_grounded():
    lg = _ledger(values={"50000.00"})  # the engine actually returned it
    r = GroundingVerifier().verify("The floor is 50000 dollars.", lg)
    assert r.verified, r.violations


def test_ungrounded_citation_is_flagged():
    lg = _ledger(citations=["FAR 15.403-4"])
    r = GroundingVerifier().verify("This is governed by 48 CFR 9903.201-2.", lg)
    assert not r.verified and any("9903" in v for v in r.violations)


def test_grounded_citation_passes():
    lg = _ledger(citations=["P.L. 119-60 (FY2026 NDAA); DFARS 215.403-3(a)"])
    r = GroundingVerifier().verify("Per DFARS 215.403-3, certified data applies.", lg)
    assert r.verified, r.violations
