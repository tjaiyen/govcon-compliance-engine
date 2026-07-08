"""Seed rows for unallowable_cost_categories — transcribed from the vault's
01_System_Architecture.md §4 table (18 categories). Per that section: this
is "a solid working set to seed from", NOT a claim of completeness — FAR
31.205 runs through numerous subsections and no exact total is adopted as
fact (see 02_Regulatory_Reference_Verified.md §10 on the unverified "52
categories" claim). Expand against current FAR 31.205 text when
completeness matters for a phase.

Mirrored by migration 0003; tests assert DB rows == these constants.
"""

SEED_CATEGORIES: list[dict] = [
    dict(far_citation="31.205-1", category_name="Advertising & Public Relations",
         detection_method="keyword_pattern",
         trap_logic_description="Flag promotional/marketing/sponsorship spend; note the narrow help-wanted-advertising and required-trade-show exceptions rather than blanket-flagging all advertising"),
    dict(far_citation="31.205-3", category_name="Bad Debts",
         detection_method="account_code",
         trap_logic_description="Dedicated account code; excluded from G&A base"),
    dict(far_citation="31.205-5", category_name="Depreciation (excess/unallowable portion)",
         detection_method="rate_lookup",
         trap_logic_description="Flag depreciation exceeding CAS-allowable methods or on assets not used in contract performance"),
    dict(far_citation="31.205-6(p)", category_name="Executive Compensation Limit",
         detection_method="rate_lookup",
         trap_logic_description="Flag compensation above the statutory annual cap; route excess to unallowable"),
    dict(far_citation="31.205-8", category_name="Contributions/Donations",
         detection_method="account_code",
         trap_logic_description="Flag all civic/charitable/political donations"),
    dict(far_citation="31.205-14", category_name="Entertainment & Recreation",
         detection_method="keyword_pattern",
         trap_logic_description="Keyword/category flag (tickets, parties, golf, social events)"),
    dict(far_citation="31.205-15", category_name="Fines and Penalties",
         detection_method="account_code",
         trap_logic_description="Auto-flag regulatory fines, tax penalties, late fees"),
    dict(far_citation="31.205-19", category_name="Insurance & Indemnification",
         detection_method="account_code",
         trap_logic_description="Flag self-insurance reserves and indemnification costs exceeding allowable limits"),
    dict(far_citation="31.205-20", category_name="Interest Expense",
         detection_method="account_code",
         trap_logic_description="Auto-flag interest on borrowings, bond discounts, financing fees"),
    dict(far_citation="31.205-22", category_name="Lobbying & Political Activity",
         detection_method="keyword_pattern",
         trap_logic_description="Flag legal/executive costs tied to influencing elections/legislation"),
    dict(far_citation="31.205-27", category_name="Organization Costs",
         detection_method="account_code",
         trap_logic_description="Flag costs of organizing/reorganizing the corporate structure (raising capital, mergers)"),
    dict(far_citation="31.205-32", category_name="Pre-contract Costs",
         detection_method="account_code",
         trap_logic_description="Flag costs incurred before contract effective date without written CO authorization"),
    dict(far_citation="31.205-38", category_name="Selling Costs",
         detection_method="account_code",
         trap_logic_description="Flag and require the bid-and-proposal (B&P) vs. selling-cost distinction rather than a single bucket"),
    dict(far_citation="31.205-43", category_name="Trade, Business, or Technical Activity Costs",
         detection_method="keyword_pattern",
         trap_logic_description="Flag membership/subscription costs not tied to a documented business purpose"),
    dict(far_citation="31.205-44", category_name="Training & Conference Costs",
         detection_method="keyword_pattern",
         trap_logic_description="Require documented business purpose; flag general-education tuition as distinct from job-related training"),
    dict(far_citation="31.205-46", category_name="Excess Travel Costs",
         detection_method="rate_lookup",
         trap_logic_description="Cross-reference against a per-diem reference table; flag excess as unallowable"),
    dict(far_citation="31.205-47", category_name="Costs of legal/other proceedings",
         detection_method="account_code",
         trap_logic_description="Claim-prosecution costs are generally unallowable, REA-prep costs generally are not (REA-vs-Claim module)"),
    dict(far_citation="31.205-51", category_name="Alcoholic Beverages",
         detection_method="receipt_parsing",
         trap_logic_description="Require line-item receipt detail; isolate alcohol into unallowable code"),
]
