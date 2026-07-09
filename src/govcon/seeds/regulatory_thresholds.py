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
    # --- CDA claim certification threshold (reg-ref §7; seeded by 0008) ---
    dict(
        rule_name="CDA_CLAIM_CERT",
        value=Decimal("100000.00"),
        effective_date=None,
        superseded_date=None,
        status="statute",
        source_citation=(
            "FAR 33.207 / 41 U.S.C. 7103(f) per Reg-Ref §7: CDA claims over "
            "$100,000 require certification; CO decides within 60 days for "
            "claims at/below, or sets a firm date for larger claims"
        ),
    ),
    # --- Executive/employee compensation cap (FAR 31.205-6(p); seeded by 0012) ---
    # BBA §702 cap (Pub. L. 113-67), codified 10 U.S.C. 2324(e)(1)(P) /
    # 41 U.S.C. 4304(a)(16), ECI-adjusted annually. Verified 2026-07-08 against
    # the OMB/OFPP table (Nov 2024 update, whitehouse.gov). The CY2026 amount
    # was NOT published in a primary source at verification time (consultant
    # ESTIMATES ~$695K are not seedable) — so the CY2025 row is superseded at
    # 2026-01-01 and 2026 lookups RAISE until the official number lands via a
    # new migration. That gap sits on the `govcon reverify` watch list.
    dict(
        rule_name="EXEC_COMP_CAP",
        value=Decimal("646000.00"),
        effective_date="2024-01-01",
        superseded_date="2025-01-01",
        status="statute",
        source_citation=(
            "BBA §702 cap, costs incurred CY2024: OMB/OFPP Contractor "
            "Compensation Cap table (Nov 2024 update, whitehouse.gov); "
            "10 U.S.C. 2324(e)(1)(P) / 41 U.S.C. 4304(a)(16)"
        ),
    ),
    dict(
        rule_name="EXEC_COMP_CAP",
        value=Decimal("671000.00"),
        effective_date="2025-01-01",
        superseded_date="2026-01-01",
        status="statute",
        source_citation=(
            "BBA §702 cap, costs incurred CY2025 (3.9% ECI escalation): OMB/OFPP "
            "Contractor Compensation Cap table (Nov 2024 update, whitehouse.gov). "
            "CY2026 amount not yet published in a primary source as of 2026-07-08 "
            "— deliberately unseeded; re-verify"
        ),
    ),
]
