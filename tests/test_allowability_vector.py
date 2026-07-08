"""Five-part allowability evaluation (§3) → structured vector (§3a),
stamped at capture via post_transaction (append-only means never after)."""

import datetime
from decimal import Decimal

import pytest

from govcon.models import ContractClauseException
from govcon.services.allowability import evaluate_allowability, post_transaction
from tests.fixtures.synthetic_data import seed_all

D = datetime.date


def test_clean_direct_transaction_vector(session):
    data = seed_all(session)
    txn = post_transaction(
        session,
        account_id=data.acct_direct_labor.account_id,
        contract_id=data.contracts["pre_ndaa"].contract_id,
        amount=Decimal("1000.00"),
        transaction_date=D(2026, 6, 18),
        period_id=data.period_open.period_id,
    )
    session.commit()
    v = txn.allowability_vector
    assert v["reasonableness_result"] == "pass"
    assert v["allocability_classification"] == "direct_specific"
    assert v["far_31_2_result"] == {"result": "allowable", "far_citation": None}
    assert v["contract_terms_result"] == {"result": "pass", "exception_id": None}
    assert v["threshold_regime_context"]["tina_threshold_id"] == (
        data.contracts["pre_ndaa"].tina_threshold_id
    )


def test_unallowable_transaction_is_flagged_with_citation(session):
    data = seed_all(session)
    txn = post_transaction(
        session,
        account_id=data.acct_entertainment.account_id,
        contract_id=data.contracts["pre_ndaa"].contract_id,
        amount=Decimal("200.00"),
        transaction_date=D(2026, 6, 18),
        period_id=data.period_open.period_id,
    )
    session.commit()
    far = txn.allowability_vector["far_31_2_result"]
    assert far["result"] == "unallowable"
    assert far["far_citation"] == "31.205-14"  # from the migration-seeded category


def test_indirect_vs_ga_allocability(session):
    from tests.fixtures.synthetic_data import ga_pool
    from govcon.models import GLAccount
    from govcon.models.enums import CostType

    data = seed_all(session)
    gapool = ga_pool()
    session.add(gapool)
    session.flush()
    ga_account = GLAccount(
        account_code="8000",
        account_name="G&A Salaries",
        cost_type=CostType.INDIRECT,
        pool_assignment=gapool.pool_id,
    )
    session.add(ga_account)
    session.flush()
    v_fringe = evaluate_allowability(
        session,
        account=data.acct_fringe,
        amount=Decimal("100.00"),
        transaction_date=D(2026, 6, 18),
    )
    v_ga = evaluate_allowability(
        session,
        account=ga_account,
        amount=Decimal("100.00"),
        transaction_date=D(2026, 6, 18),
    )
    assert v_fringe["allocability_classification"] == "indirect_shared"
    assert v_ga["allocability_classification"] == "necessary_overhead"


def test_reasonableness_outlier_flags_for_review_never_autofails(session):
    data = seed_all(session)
    for i in range(6):  # build history above min_history
        post_transaction(
            session,
            account_id=data.acct_direct_labor.account_id,
            contract_id=data.contracts["pre_ndaa"].contract_id,
            amount=Decimal("1000.00") + Decimal(i),
            transaction_date=D(2026, 6, 10 + i),
            period_id=data.period_open.period_id,
        )
    outlier = post_transaction(
        session,
        account_id=data.acct_direct_labor.account_id,
        contract_id=data.contracts["pre_ndaa"].contract_id,
        amount=Decimal("50000.00"),
        transaction_date=D(2026, 6, 25),
        period_id=data.period_open.period_id,
    )
    session.commit()
    v = outlier.allowability_vector
    assert v["reasonableness_result"] == "flag_for_review"
    # Flag-for-review is a human-review path, not an auto-fail:
    assert v["far_31_2_result"]["result"] == "allowable"


def test_contract_clause_exception_overrides(session):
    data = seed_all(session)
    exception = ContractClauseException(
        contract_id=data.contracts["pre_ndaa"].contract_id,
        far_citation_overridden="31.205-14",
        override_reason="Synthetic award-specific clause permitting morale events",
        effective_date=D(2026, 1, 1),
    )
    session.add(exception)
    session.flush()
    txn = post_transaction(
        session,
        account_id=data.acct_entertainment.account_id,
        contract_id=data.contracts["pre_ndaa"].contract_id,
        amount=Decimal("120.00"),
        transaction_date=D(2026, 6, 18),
        period_id=data.period_open.period_id,
    )
    session.commit()
    terms = txn.allowability_vector["contract_terms_result"]
    assert terms == {"result": "overridden_by", "exception_id": exception.exception_id}


def test_gaap_governs_after_rescission_effective_date(session):
    """§4b: dual-tracked account switches basis at the CAS 408/411
    final-rule effective date (2026-08-07 per the seeded thresholds)."""
    from govcon.services.allowability import governing_treatment

    data = seed_all(session)
    data.acct_fringe.cas_treatment = "CAS 408 compensated absence accrual"
    data.acct_fringe.gaap_treatment = "ASC 710 accrual"
    session.flush()
    basis_before, _ = governing_treatment(session, data.acct_fringe, D(2026, 8, 6))
    basis_after, _ = governing_treatment(session, data.acct_fringe, D(2026, 8, 7))
    assert basis_before == "cas"
    assert basis_after == "gaap"
