"""Seed rows for decision_tables/decision_rules — the v1 rules-as-data
encoding of the CAS coverage order (spec §7) and the per-action TINA
exception ladder (spec §8).

v1 is a PARITY encoding: every predicate, outcome, and reason template
reproduces the behavior (and the exact reason strings) of the coded
determine_cas_coverage / determine_tina_applicability it replaces, proven by
tests/test_rules_parity.py against a frozen oracle copy of that code. The one
deliberate addition: the cumulative full-coverage rule carries its real
regulatory provenance (48 CFR 9903.201-2 is still a PROPOSED rule) as per-rule
status, so firing it emits a caveat the coded version only carried as a
comment.

These constants mirror migration 0015 exactly; a drift test asserts DB rows ==
these constants so the frozen migration and the importable constants can never
diverge silently (same discipline as seeds/regulatory_thresholds.py).
"""

_CAS_RULES: list[dict] = [
    dict(
        rule_order=1,
        rule_key="small_business_exempt",
        description="Small business: CAS-exempt regardless of value (§7 step 1).",
        when_ast={"lhs": {"input": "contractor_size"}, "op": "eq", "rhs": "small"},
        outcome={"tier": "exempt_small_business"},
        reason_template="small-business contractor: CAS-exempt regardless of value",
        stop=True,
        status=None,
        source_citation=None,
    ),
    dict(
        rule_order=2,
        rule_key="nontraditional_review",
        description=(
            "Nontraditional defense contractor: a DISTINCT exemption path, "
            "flagged for review, never silently applied (§7 step 2)."
        ),
        when_ast={"lhs": {"input": "is_nontraditional_dc"}, "op": "is_true"},
        outcome={"tier": "review_nontraditional", "requires_review": True},
        reason_template=(
            "nontraditional-defense-contractor award: likely exempt from CAS and "
            "FAR Part 31 cost principles (NDAA §1826) — REVIEW REQUIRED, not "
            "silently applied"
        ),
        stop=True,
        status=None,
        source_citation=None,
    ),
    dict(
        rule_order=3,
        rule_key="below_contract_trigger",
        description="Below the dated contract-level trigger: no CAS coverage (§7 step 3).",
        when_ast={"lhs": {"input": "value"}, "op": "lt", "rhs": {"threshold": "trigger"}},
        outcome={},
        reason_template=(
            "contract value {value} below the {trigger} CAS trigger in force on {award_date}"
        ),
        stop=True,
        status=None,
        source_citation=None,
    ),
    dict(
        rule_order=4,
        rule_key="modified_coverage",
        description="At/above the trigger: modified coverage; evaluation continues to the full-coverage rules.",
        when_ast={"lhs": {"input": "value"}, "op": "ge", "rhs": {"threshold": "trigger"}},
        outcome={"tier": "modified"},
        reason_template=(
            "contract value {value} meets the {trigger} trigger in force on "
            "{award_date}: modified coverage (CAS 401/402/405/406)"
        ),
        stop=False,
        status=None,
        source_citation=None,
    ),
    dict(
        rule_order=5,
        rule_key="full_coverage_single_award",
        description="Single award at/above the full-coverage threshold (§7 step 4).",
        when_ast={"lhs": {"input": "value"}, "op": "ge", "rhs": {"threshold": "full"}},
        outcome={"tier": "full", "disclosure_required": True},
        reason_template=(
            "single award {value} meets the {full} full-coverage threshold: all active "
            "standards apply and a CASB DS-1 Disclosure Statement obligation is triggered"
        ),
        stop=True,
        status=None,
        source_citation=None,
    ),
    dict(
        rule_order=6,
        rule_key="full_coverage_cumulative",
        description=(
            "Cumulative prior-fiscal-year CAS-covered awards + this award reach "
            "the full-coverage threshold (the §7 encoded cumulative window)."
        ),
        when_ast={
            "lhs": {"input": "cumulative_plus_value"},
            "op": "ge",
            "rhs": {"threshold": "full"},
        },
        outcome={"tier": "full", "disclosure_required": True},
        reason_template=(
            "cumulative prior-year CAS-covered awards {cumulative} + this award "
            "{value} meets the {full} full-coverage threshold: all active "
            "standards apply and a CASB DS-1 Disclosure Statement obligation is triggered"
        ),
        stop=True,
        # The honesty upgrade over the coded version: this window encodes
        # 9903.201-2 text that is still a PROPOSED rule — surfaced as a caveat
        # when the rule fires, not buried in a comment.
        status="proposed_rule",
        source_citation=(
            "48 CFR 9903.201-2 cumulative-award window — implementing CAS Board "
            "regulation still a PROPOSED rule as of 2026-07-08 (CASB Case 2021-01 "
            "NPRM, 91 FR 13559); verify current text"
        ),
    ),
]

_TINA_RULES: list[dict] = [
    dict(
        rule_order=1,
        rule_key="no_proposed_value",
        description="No proposed value on the action: cannot determine; flag (§8).",
        when_ast={"lhs": {"input": "proposed_value"}, "op": "is_null"},
        outcome={},
        reason_template="action has no proposed_value — cannot determine; flag",
        stop=True,
        status=None,
        source_citation=None,
    ),
    dict(
        rule_order=2,
        rule_key="at_or_below_threshold",
        description=(
            "TINA requires certified data only when the price EXCEEDS the "
            "threshold — an action exactly AT the threshold is not required."
        ),
        when_ast={
            "lhs": {"input": "proposed_value"},
            "op": "le",
            "rhs": {"threshold": "th"},
        },
        outcome={"certification_required": False},
        reason_template=(
            "action value {proposed_value} at or below the {th} TINA threshold in force "
            "on {action_date}: certified cost-or-pricing data not required"
        ),
        stop=True,
        status=None,
        source_citation=None,
    ),
    dict(
        rule_order=3,
        rule_key="above_threshold",
        description="Above the threshold: record it and fall through to the exception ladder.",
        when_ast={
            "lhs": {"input": "proposed_value"},
            "op": "gt",
            "rhs": {"threshold": "th"},
        },
        outcome={"above_threshold": True},
        reason_template=None,
        stop=False,
        status=None,
        source_citation=None,
    ),
    dict(
        rule_order=4,
        rule_key="exception_adequate_price_competition",
        description="Statutory exception 1 of 4, in 10 U.S.C. 3703 order.",
        when_ast={
            "lhs": {"input": "tina_exception_adequate_price_competition"},
            "op": "is_true",
        },
        outcome={
            "certification_required": False,
            "exception_applied": "tina_exception_adequate_price_competition",
        },
        reason_template=(
            "above threshold, but statutory exception "
            "tina_exception_adequate_price_competition applies to THIS "
            "action (recorded, not a bare 'exempt' flag)"
        ),
        stop=True,
        status=None,
        source_citation=None,
    ),
    dict(
        rule_order=5,
        rule_key="exception_commercial_product_service",
        description="Statutory exception 2 of 4.",
        when_ast={
            "lhs": {"input": "tina_exception_commercial_product_service"},
            "op": "is_true",
        },
        outcome={
            "certification_required": False,
            "exception_applied": "tina_exception_commercial_product_service",
        },
        reason_template=(
            "above threshold, but statutory exception "
            "tina_exception_commercial_product_service applies to THIS "
            "action (recorded, not a bare 'exempt' flag)"
        ),
        stop=True,
        status=None,
        source_citation=None,
    ),
    dict(
        rule_order=6,
        rule_key="exception_prices_set_by_law",
        description="Statutory exception 3 of 4.",
        when_ast={
            "lhs": {"input": "tina_exception_prices_set_by_law"},
            "op": "is_true",
        },
        outcome={
            "certification_required": False,
            "exception_applied": "tina_exception_prices_set_by_law",
        },
        reason_template=(
            "above threshold, but statutory exception "
            "tina_exception_prices_set_by_law applies to THIS "
            "action (recorded, not a bare 'exempt' flag)"
        ),
        stop=True,
        status=None,
        source_citation=None,
    ),
    dict(
        rule_order=7,
        rule_key="exception_waiver_granted",
        description="Statutory exception 4 of 4.",
        when_ast={
            "lhs": {"input": "tina_exception_waiver_granted"},
            "op": "is_true",
        },
        outcome={
            "certification_required": False,
            "exception_applied": "tina_exception_waiver_granted",
        },
        reason_template=(
            "above threshold, but statutory exception "
            "tina_exception_waiver_granted applies to THIS "
            "action (recorded, not a bare 'exempt' flag)"
        ),
        stop=True,
        status=None,
        source_citation=None,
    ),
    dict(
        rule_order=8,
        rule_key="exceptions_pending",
        description=(
            "Any exception still unevaluated: pending — never assume either "
            "way, never inherit from the vehicle."
        ),
        when_ast={"lhs": {"input": "unevaluated_count"}, "op": "gt", "rhs": 0},
        outcome={},
        reason_template=(
            "above threshold with exceptions not yet evaluated "
            "({unevaluated_count} of 4) — evaluate each explicitly "
            "on this action before concluding; do not inherit from the vehicle"
        ),
        stop=True,
        status=None,
        source_citation=None,
    ),
    dict(
        rule_order=9,
        rule_key="certification_required",
        description="Above threshold, all four exceptions evaluated False.",
        when_ast=None,  # the default row
        outcome={"certification_required": True},
        reason_template=(
            "action value {proposed_value} meets the {th} threshold and all four statutory "
            "exceptions are evaluated False: certified cost-or-pricing data required "
            "(FAR 15.403-4)"
        ),
        stop=True,
        status=None,
        source_citation=None,
    ),
]

DECISION_TABLE_SEEDS: list[dict] = [
    dict(
        table_name="CAS_COVERAGE",
        version=1,
        effective_date=None,
        superseded_date=None,
        source_citation=(
            "Architecture spec §7 determination order (FAR Part 30 / 48 CFR 9903.201); "
            "v1 = parity encoding of services.cas_tina.determine_cas_coverage "
            "as of main@0e244e6, proven by tests/test_rules_parity.py"
        ),
        description="CAS coverage tier: exempt/review/none/modified/full + DS-1 obligation.",
        threshold_context={"trigger": "CAS_CONTRACT_TRIGGER", "full": "CAS_FULL_COVERAGE"},
        # on_first_use: the small-business/nontraditional early exits return
        # with NO threshold lookup (and no caveats), exactly as the coded
        # service did; the context resolves as a unit at rule 3.
        threshold_resolution="on_first_use",
        initial_outcome={"tier": "none", "requires_review": False, "disclosure_required": False},
        rules=_CAS_RULES,
    ),
    dict(
        table_name="TINA_APPLICABILITY",
        version=1,
        effective_date=None,
        superseded_date=None,
        source_citation=(
            "Architecture spec §8 per-action evaluation (10 U.S.C. 3702/3703, "
            "FAR 15.403-4); v1 = parity encoding of "
            "services.cas_tina.determine_tina_applicability as of main@0e244e6"
        ),
        description=(
            "Per-action TINA applicability: threshold gate + the four statutory "
            "exceptions in 10 U.S.C. 3703 order; inheritance from the vehicle is "
            "impossible by construction (inputs come from THIS action only)."
        ),
        threshold_context={"th": "TINA_THRESHOLD"},
        # eager: the coded service resolved the threshold row (id, value,
        # caveat) before any branching, including the no-value flag path.
        threshold_resolution="eager",
        initial_outcome={
            "above_threshold": False,
            "certification_required": None,
            "exception_applied": None,
        },
        rules=_TINA_RULES,
    ),
]
