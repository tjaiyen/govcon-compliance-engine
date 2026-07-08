"""Seed rows for regulatory_thresholds — strictly from the vault's
02_Regulatory_Reference_Verified.md (verified_as_of 2026-07-08). Nothing
invented; the pre-2025 TINA row's start date is not given in that file, so
its effective_date is None ("open start", history begins there).

These constants mirror migration 0002 exactly; a drift test asserts DB rows
== these constants so the frozen migration and the importable constants can
never diverge silently.

Statuses are load-bearing (CLAUDE.md ground rule 3): the $10M TINA figure is
operative via DoD class deviation (not yet a final DFARS rule); the $35M/
$100M CAS figures are statutory with the implementing CAS Board regulation
still a PROPOSED rule as of 2026-07-08; CAS 407 is proposed-not-final while
CAS 408/411 are final (effective 2026-08-07).
"""

from decimal import Decimal

SEED_ROWS: list[dict] = [
    # --- TINA certified cost-or-pricing-data threshold (reg-ref §1) ---
    dict(
        rule_name="TINA_THRESHOLD",
        value=Decimal("2000000.00"),
        effective_date=None,
        superseded_date="2025-10-01",
        status="final_rule",
        source_citation="Reg-Ref §1 TINA inflation-adjustment history ($2.0M before Oct 1, 2025)",
    ),
    dict(
        rule_name="TINA_THRESHOLD",
        value=Decimal("2500000.00"),
        effective_date="2025-10-01",
        superseded_date="2026-07-01",
        status="final_rule",
        source_citation="Reg-Ref §1 ($2.5M Oct 1, 2025 - Jun 30, 2026, inflation-adjusted)",
    ),
    dict(
        rule_name="TINA_THRESHOLD",
        value=Decimal("10000000.00"),
        effective_date="2026-07-01",
        superseded_date=None,
        status="class_deviation",
        source_citation=(
            "P.L. 119-60 (FY2026 NDAA, enacted 2025-12-18); contracts entered "
            "after 2026-06-30; DoD class deviation 2026-O0048 / DFARS 215.403-3(a) "
            "— codified DFARS not yet amended as of 2026-07-08"
        ),
    ),
    # --- Contract-level CAS applicability trigger (reg-ref §1) ---
    dict(
        rule_name="CAS_CONTRACT_TRIGGER",
        value=Decimal("7500000.00"),
        effective_date=None,
        superseded_date="2026-07-01",
        status="final_rule",
        source_citation="Reg-Ref §1 (old $7.5M trigger, pre-NDAA)",
    ),
    dict(
        rule_name="CAS_CONTRACT_TRIGGER",
        value=Decimal("35000000.00"),
        effective_date="2026-07-01",
        superseded_date=None,
        status="statute",
        source_citation=(
            "P.L. 119-60, after 2026-06-30; implementing CAS regulation "
            "(48 CFR 9903.201) still PROPOSED as of 2026-07-08 (CASB Case "
            "2021-01 NPRM, 91 FR 13559)"
        ),
    ),
    # --- Full CAS coverage threshold (reg-ref §1) ---
    dict(
        rule_name="CAS_FULL_COVERAGE",
        value=Decimal("50000000.00"),
        effective_date=None,
        superseded_date="2026-07-01",
        status="final_rule",
        source_citation="Reg-Ref §1 (old $50M full-coverage threshold, pre-NDAA)",
    ),
    dict(
        rule_name="CAS_FULL_COVERAGE",
        value=Decimal("100000000.00"),
        effective_date="2026-07-01",
        superseded_date=None,
        status="statute",
        source_citation=(
            "P.L. 119-60, after 2026-06-30; implementing CAS Board regulation "
            "still PROPOSED as of 2026-07-08 (91 FR 13559)"
        ),
    ),
    # --- Simplified Acquisition Threshold (reg-ref §3) ---
    dict(
        rule_name="SAT",
        value=Decimal("350000.00"),
        effective_date="2025-10-01",
        superseded_date=None,
        status="final_rule",
        source_citation=(
            "FAR Council final rule, Inflation Adjustment of Acquisition-"
            "Related Thresholds, 90 FR 41872 (2025-08-27)"
        ),
    ),
    # --- CAS standard-status rows (reg-ref §2; value NULL = status-only) ---
    dict(
        rule_name="CAS_407_STATUS",
        value=None,
        effective_date=None,
        superseded_date=None,
        status="proposed_rule",
        source_citation=(
            "NPRM 2026-03-20 (comments closed 2026-04-20); proposed for "
            "elimination, NOT final as of 2026-07-08 — re-verify before Phase 3/12"
        ),
    ),
    dict(
        rule_name="CAS_408_STATUS",
        value=None,
        effective_date="2026-08-07",
        superseded_date=None,
        status="final_rule",
        source_citation="Final rule 91 FR 42139 (2026-07-08), rescinds CAS 408 eff. 2026-08-07",
    ),
    dict(
        rule_name="CAS_411_STATUS",
        value=None,
        effective_date="2026-08-07",
        superseded_date=None,
        status="final_rule",
        source_citation="Final rule 91 FR 42139 (2026-07-08), rescinds CAS 411 eff. 2026-08-07",
    ),
]
