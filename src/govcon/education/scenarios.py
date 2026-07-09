"""Scenario/learning library (Phase 2): worked examples the guided UI can
run live against the real engine.

Every scenario is EXECUTABLE TRUTH, not prose: each run carries the exact
form inputs plus a pre-registered `expect` block, and tests/test_education.py
posts every run through the API asserting the expectations hold — so the
teaching content can never drift from the engine's behavior (B35 discipline
applied to curriculum).

Grounding: each scenario is lifted from a real engine test
(tests/test_cas_tina.py) and the seeded thresholds; nothing invented.

Schema per scenario:
  id, title, story          — what the learner is looking at and why it matters
  runs: [{label, endpoint ("cas"|"tina"), inputs, expect}]
        expect keys are top-level response fields compared by equality, plus
        the optional pseudo-keys caveat_contains / reason_contains (substring
        must appear in some caveat / reason).
  notice: [...]             — what to look at in the results
  terms: [...]              — glossary terms this scenario exercises
"""

SCENARIOS: list[dict] = [
    dict(
        id="ndaa_boundary",
        title="Same contract, two dates, opposite answers",
        story=(
            "Thresholds are dated. A $12M award lands in CAS modified coverage "
            "in May 2026 (trigger $7.5M) — and in NO coverage seven weeks later "
            "(trigger $35M). Nothing about the contract changed; the regulation "
            "under it did."
        ),
        runs=[
            dict(
                label="Awarded 2026-05-15 (pre-NDAA trigger $7.5M)",
                endpoint="cas",
                inputs={
                    "award_date": "2026-05-15",
                    "contract_value": "12000000.00",
                    "contractor_size": "other_than_small",
                },
                expect={"tier": "modified"},
            ),
            dict(
                label="Awarded 2026-07-15 (post-NDAA trigger $35M)",
                endpoint="cas",
                inputs={
                    "award_date": "2026-07-15",
                    "contract_value": "12000000.00",
                    "contractor_size": "other_than_small",
                },
                expect={"tier": "none", "caveat_contains": "statute"},
            ),
        ],
        notice=[
            "The reasons name the exact trigger value in force on each date.",
            "The July result carries a caveat: the new figures are statute with "
            "the implementing regulation still PROPOSED — operative, not settled.",
        ],
        terms=["dated threshold", "CAS", "regulatory status"],
    ),
    dict(
        id="small_business_gate",
        title="A $500M award with zero CAS coverage",
        story=(
            "Size is checked before any dollar math: a qualifying small "
            "business is CAS-exempt regardless of contract value. The dollar "
            "thresholds are never even looked up."
        ),
        runs=[
            dict(
                label="$500M award, small-business contractor",
                endpoint="cas",
                inputs={
                    "award_date": "2026-07-15",
                    "contract_value": "500000000.00",
                    "contractor_size": "small",
                },
                expect={"tier": "exempt_small_business"},
            ),
        ],
        notice=[
            "No threshold caveats appear — the exemption fired before any "
            "threshold lookup (watch the Auditor view: only one rule fired).",
        ],
        terms=["small business exemption", "CAS", "decision table"],
    ),
    dict(
        id="nontraditional_review",
        title="An exemption the engine refuses to apply silently",
        story=(
            "Nontraditional defense contractors are LIKELY exempt from CAS — "
            "but 'likely' is a judgment a human must confirm. The engine flags "
            "it for review instead of quietly deciding."
        ),
        runs=[
            dict(
                label="$50M award, nontraditional defense contractor",
                endpoint="cas",
                inputs={
                    "award_date": "2026-07-15",
                    "contract_value": "50000000.00",
                    "contractor_size": "other_than_small",
                    "is_nontraditional_dc": True,
                },
                expect={"tier": "review_nontraditional", "requires_review": True},
            ),
        ],
        notice=[
            "requires_review=true — the tool advises and hands the judgment "
            "to a person; it never silently applies a discretionary exemption.",
        ],
        terms=["nontraditional defense contractor", "CAS"],
    ),
    dict(
        id="inheritance_trap",
        title="The task-order inheritance trap",
        story=(
            "The umbrella vehicle was competitively priced, so its own award "
            "action needs no certified data. A later sole-source task order "
            "under it does NOT inherit that: TINA is evaluated per action, and "
            "the order's own exceptions start unevaluated."
        ),
        runs=[
            dict(
                label="The vehicle's competitive award action ($12M, June 2026)",
                endpoint="tina",
                inputs={
                    "action_date": "2026-06-15",
                    "proposed_value": "12000000.00",
                    "tina_exception_adequate_price_competition": True,
                    "tina_exception_commercial_product_service": False,
                    "tina_exception_prices_set_by_law": False,
                    "tina_exception_waiver_granted": False,
                },
                expect={
                    "certification_required": False,
                    "exception_applied": "tina_exception_adequate_price_competition",
                },
            ),
            dict(
                label="A later sole-source task order ($4M) — exceptions unset",
                endpoint="tina",
                inputs={
                    "action_date": "2026-06-20",
                    "proposed_value": "4000000.00",
                },
                expect={
                    "certification_required": None,
                    "exception_applied": None,
                    "reason_contains": "do not inherit from the vehicle",
                },
            ),
        ],
        notice=[
            "The task order's answer is PENDING (null), not exempt — each of "
            "the four statutory exceptions must be evaluated on THIS action.",
            "This is a real defective-pricing failure mode, made impossible "
            "here by construction: the evaluation never reads the vehicle.",
        ],
        terms=["task order", "TINA", "certified cost or pricing data"],
    ),
    dict(
        id="exactly_at_the_bar",
        title="Exactly $10,000,000.00 — not required",
        story=(
            "TINA requires certified data only when the price EXCEEDS the "
            "threshold. An action exactly AT the bar is not required — a "
            "boundary an earlier stress test caught being coded wrong."
        ),
        runs=[
            dict(
                label="Exactly at the bar ($10,000,000.00, Aug 2026)",
                endpoint="tina",
                inputs={
                    "action_date": "2026-08-01",
                    "proposed_value": "10000000.00",
                },
                expect={"above_threshold": False, "certification_required": False},
            ),
            dict(
                label="One cent over ($10,000,000.01)",
                endpoint="tina",
                inputs={
                    "action_date": "2026-08-01",
                    "proposed_value": "10000000.01",
                },
                expect={"above_threshold": True, "certification_required": None},
            ),
        ],
        notice=[
            "One cent flips above_threshold — and the answer becomes 'pending' "
            "because the four exceptions are still unevaluated, not an "
            "automatic 'required'.",
        ],
        terms=["TINA", "dated threshold"],
    ),
    dict(
        id="operative_but_not_settled",
        title="A $10M threshold that is not settled law",
        story=(
            "The TINA bar jumped to $10M for actions after 2026-06-30 — via a "
            "DoD class deviation, while the DFARS rulebook text is still "
            "unamended. The engine applies it AND tells you its status."
        ),
        runs=[
            dict(
                label="$12M sole-source action, all exceptions evaluated False",
                endpoint="tina",
                inputs={
                    "action_date": "2026-07-15",
                    "proposed_value": "12000000.00",
                    "tina_exception_adequate_price_competition": False,
                    "tina_exception_commercial_product_service": False,
                    "tina_exception_prices_set_by_law": False,
                    "tina_exception_waiver_granted": False,
                },
                expect={
                    "certification_required": True,
                    "caveat_contains": "class_deviation",
                },
            ),
        ],
        notice=[
            "The determination is definitive (required) AND caveated — "
            "'operative' and 'settled' are different things, and the caveat "
            "cites the deviation number so you can verify it yourself.",
        ],
        terms=["class deviation", "regulatory status", "TINA"],
    ),
]
