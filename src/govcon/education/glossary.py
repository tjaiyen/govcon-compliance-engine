"""Workbench glossary — plain-language entries for the terms the guided UI
surfaces (Phase 2 education layer).

Content discipline: definitions use everyday words; every example quotes the
engine's own seeded figures (regulatory_thresholds via migration 0002/0012),
never invented numbers; regulatory statuses are stated honestly (a draft is
called a draft). tests/test_education.py checks structural completeness and
that quoted dollar figures exist in the threshold seeds."""

GLOSSARY: list[dict] = [
    dict(
        term="TINA",
        plain=(
            "The Truthful Cost or Pricing Data Act: when the government cannot "
            "rely on competition to set a fair price, the contractor must hand "
            "over the cost data behind its price and certify the data is "
            "complete and current."
        ),
        why=(
            "Above a dollar bar this is mandatory — and getting it wrong later "
            "counts as defective pricing, which the government can claw back."
        ),
        example=(
            "An $8,000,000.00 task order in August 2026 does not need certified "
            "data (the bar that day is $10,000,000.00); the same order at "
            "$12,000,000.00 does."
        ),
    ),
    dict(
        term="certified cost or pricing data",
        plain=(
            "The full cost backup behind a proposed price — labor, materials, "
            "rates, quotes — with a signed certificate saying it was accurate, "
            "complete, and current as of the price agreement date."
        ),
        why=(
            "The certificate is what turns an estimating mistake into a legal "
            "liability, so knowing when it is required matters."
        ),
        example=(
            "A $12,000,000.00 sole-source action above the TINA bar requires "
            "the certificate; the same action with adequate price competition "
            "does not — the exception is recorded by name, never assumed."
        ),
    ),
    dict(
        term="CAS",
        plain=(
            "Cost Accounting Standards: federal rules for HOW a contractor "
            "measures and assigns costs — consistently, the same way every "
            "time — separate from whether a cost is allowed at all."
        ),
        why=(
            "Coverage switches on by contract size and brings audits and a "
            "disclosure obligation with it."
        ),
        example=(
            "A $50,000,000.00 award on 2026-07-15 lands in modified coverage "
            "(the trigger in force is $35,000,000.00); below that trigger, no "
            "CAS coverage at all."
        ),
    ),
    dict(
        term="modified vs full coverage",
        plain=(
            "Two CAS intensity levels. Modified = four standards about basic "
            "consistency. Full = all active standards plus a formal disclosure "
            "of your accounting practices."
        ),
        why="The jump to full coverage is a big compliance step change.",
        example=(
            "On 2026-07-15 a $50,000,000.00 award is modified; a "
            "$100,000,000.00 award is full and triggers the DS-1 disclosure."
        ),
    ),
    dict(
        term="DS-1 Disclosure Statement",
        plain=(
            "The formal document where a fully CAS-covered contractor writes "
            "down exactly how it accounts for costs, so auditors can hold it "
            "to its own stated practices."
        ),
        why=(
            "Once disclosed, changing a practice has consequences — the "
            "government measures you against what you wrote."
        ),
        example=(
            "Crossing the $100,000,000.00 full-coverage threshold triggers the "
            "DS-1 obligation; the workbench flags it as disclosure_required."
        ),
    ),
    dict(
        term="class deviation",
        plain=(
            "An agency-wide official exception: a rule everyone must follow "
            "before the main rulebook catches up. Real and binding, but not "
            "yet the settled published regulation."
        ),
        why=(
            "You can rely on it operationally, but you should watch it — the "
            "final rulebook text could differ."
        ),
        example=(
            "The $10,000,000.00 TINA threshold operates via DoD class "
            "deviation 2026-O0048 while the DFARS text is still unamended — "
            "every determination built on it carries that caveat."
        ),
    ),
    dict(
        term="regulatory status",
        plain=(
            "Every threshold and rule here is tagged with where it stands: "
            "statute (law passed, rulebook pending), proposed_rule (a draft), "
            "final_rule (settled), class_deviation (binding agency exception), "
            "or carry_forward (an old value still governing because its "
            "scheduled update was formally waived)."
        ),
        why=(
            "A number can be operative without being settled — presenting a "
            "draft as settled law is exactly the mistake this engine refuses "
            "to make."
        ),
        example=(
            "The post-2026 CAS thresholds ($35,000,000.00 / $100,000,000.00) "
            "are statute with the implementing regulation still PROPOSED — so "
            "results built on them always carry a status caveat."
        ),
    ),
    dict(
        term="dated threshold",
        plain=(
            "Dollar bars here are looked up BY DATE: the engine applies the "
            "value in force on the award or action date, not today's value."
        ),
        why=(
            "Regulations change mid-stream; using today's number on last "
            "year's contract is a classic compliance error."
        ),
        example=(
            "The same $12,000,000.00 contract is CAS modified coverage if "
            "awarded 2026-05-15 (trigger $7,500,000.00) and NOT covered if "
            "awarded 2026-07-15 (trigger $35,000,000.00)."
        ),
    ),
    dict(
        term="SF 1408",
        plain=(
            "The government's pre-award checklist asking one question six "
            "ways: is this accounting system capable of tracking costs by "
            "contract the way cost-reimbursement work requires?"
        ),
        why=(
            "Failing it can keep you out of cost-type contracts entirely."
        ),
        example=(
            "The workbench self-check runs the six criteria against the "
            "database structure — and honestly refuses to grade an empty "
            "database as adequate."
        ),
    ),
    dict(
        term="small business exemption",
        plain=(
            "Contractors that qualify as small businesses are exempt from CAS "
            "entirely, no matter the contract size."
        ),
        why="It is the first gate checked — before any dollar math.",
        example=(
            "A $500,000,000.00 award to a small business is CAS-exempt; the "
            "same award to anyone else is full coverage."
        ),
    ),
    dict(
        term="nontraditional defense contractor",
        plain=(
            "A company that has not recently done CAS-covered defense work — "
            "the law gives it a likely exemption to lower the barrier to "
            "entry."
        ),
        why=(
            "It is an exemption someone must CONFIRM, not assume — this "
            "engine flags it for review instead of silently applying it."
        ),
        example=(
            "A nontraditional award comes back review_nontraditional with "
            "requires_review=true, never a quiet exempt."
        ),
    ),
    dict(
        term="task order",
        plain=(
            "A specific job placed under an existing umbrella contract "
            "(vehicle). Each order is priced and evaluated on its own."
        ),
        why=(
            "The classic defective-pricing trap: assuming the umbrella's "
            "competitive pricing covers a later sole-source order. It does "
            "not — TINA is evaluated per action, on its own date and value."
        ),
        example=(
            "A vehicle priced with adequate competition in May does not "
            "exempt a $4,000,000.00 sole-source task order in June — the "
            "order's own exceptions start unevaluated and the answer is "
            "'pending', never inherited."
        ),
    ),
    dict(
        term="decision table",
        plain=(
            "The rule logic itself, stored as versioned dated data rows the "
            "engine evaluates — not buried in code. You can read every rule, "
            "its order, and where it came from with 'govcon rules show'."
        ),
        why=(
            "When a regulation changes shape, the change lands as a new "
            "reviewable table version, and rules encoded from drafts carry "
            "their own status caveats."
        ),
        example=(
            "CAS coverage is 6 ordered rules; the cumulative full-coverage "
            "rule is tagged proposed_rule because 48 CFR 9903.201-2 is still "
            "a draft — results it fires on say so."
        ),
    ),
    dict(
        term="re-verification watch list",
        plain=(
            "The standing to-do list of everything built on not-yet-settled "
            "regulation: date checkpoints, non-final thresholds, and "
            "non-final decision rules."
        ),
        why=(
            "Advisory tools go stale silently; this one keeps a visible list "
            "of exactly what to re-check against primary sources."
        ),
        example=(
            "'govcon reverify' lists the $10,000,000.00 TINA class deviation "
            "and the proposed-rule CAS figures as [watch] items."
        ),
    ),
    dict(
        term="price analysis vs. cost analysis (FAR 15.404-1)",
        plain=(
            "Two ways to decide a proposed price is fair and reasonable: price "
            "analysis compares the whole price to the market; cost analysis "
            "breaks down and evaluates each individual cost element."
        ),
        why=(
            "Which one you owe drives the entire proposal, negotiation, and "
            "audit workflow — and it is not a free choice."
        ),
        example=(
            "When certified cost or pricing data are required (TINA applies, no "
            "exception), cost analysis is required; otherwise price analysis is "
            "the basis. The engine's /api/pricing-analysis returns which, cited."
        ),
    ),
    dict(
        term="subcontractor certified cost or pricing data (FAR 15.404-3)",
        plain=(
            "When a prime contractor must make its subcontractor furnish "
            "certified cost or pricing data — because a large enough sub price "
            "flows straight into the prime's price."
        ),
        why=(
            "The prime is responsible for the reasonableness of subcontract "
            "prices; missing this flowdown is a classic defective-pricing "
            "exposure at the prime level."
        ),
        example=(
            "Required when the subcontract price is both more than the dated "
            "certified-data threshold and more than 10 percent of the prime's "
            "proposed price, or $20 million or more."
        ),
    ),
    dict(
        term="weighted guidelines profit objective (FAR 15.404-4 / DFARS 215.404-71)",
        plain=(
            "The DoD structured method (the DD-1547 form) for building a target "
            "profit before negotiation — risk factors expressed as percentages "
            "of cost, each assigned within a set designated range."
        ),
        why=(
            "Profit is always negotiated, but the government starts from this "
            "structured objective; knowing the factor ranges is how you argue "
            "your number."
        ),
        example=(
            "Performance-risk factors (technical, management) run 3 to 7 percent "
            "of cost; contract-type risk runs from about 4 to 6 percent for "
            "firm-fixed-price down to 0 to 1 percent for cost-plus-fixed-fee. The "
            "engine's /api/weighted-guidelines computes the objective and flags "
            "any factor outside its range."
        ),
    ),
    dict(
        term="cost realism / probable cost (FAR 15.404-1(d))",
        plain=(
            "Checking whether each proposed cost is realistic for the work, then "
            "adjusting it to a 'probable cost' — what the job will most likely "
            "actually cost — which is what the government evaluates instead of the "
            "proposed number."
        ),
        why=(
            "It is required on cost-reimbursement contracts (the government, not "
            "the contractor, bears the overrun), so an unrealistically low bid "
            "does not win by lowballing — it gets scored at its probable cost."
        ),
        example=(
            "An offeror proposes $100,000 of labor, but the realistic level of "
            "effort is $120,000; the $20,000 upward adjustment makes the probable "
            "cost $120,000. The engine's /api/cost-realism totals each element's "
            "probable cost and reports the adjustment."
        ),
    ),
]
