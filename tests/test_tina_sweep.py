"""Phase 8: the deterministic TINA sweep — window discipline, per-comparison
logging with match_method + threshold, flagging semantics, baseline lock,
and certification gating."""

import datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa

from govcon.models import TINABaseline, TINABaselineAssumption, TINASweepFinding
from govcon.models.tina import AssumptionType, CertificationStatus, SweepStatus
from govcon.services.allowability import post_transaction
from govcon.services.tina_sweep import (
    SweepError,
    generate_certification,
    run_sweep,
    sweep_delta_report,
)
from tests.fixtures.synthetic_data import seed_all

D = datetime.date


def _baseline(session, data, *, baseline=D(2026, 6, 1), agreement=D(2026, 6, 30)):
    row = TINABaseline(
        contract_id=data.contracts["pre_ndaa"].contract_id,
        baseline_date=baseline,
        price_agreement_date=agreement,
    )
    session.add(row)
    session.flush()
    return row


def _assumption(session, baseline, *, description, value, atype=AssumptionType.VENDOR_QUOTE):
    row = TINABaselineAssumption(
        baseline_id=baseline.baseline_id,
        assumption_type=atype,
        description=description,
        baseline_value=Decimal(value),
        source_document=f"QUOTE-{description}",
    )
    session.add(row)
    session.flush()
    return row


def _txn(session, data, *, amount, date, doc):
    return post_transaction(
        session,
        account_id=data.acct_direct_labor.account_id,
        contract_id=data.contracts["pre_ndaa"].contract_id,
        amount=Decimal(amount),
        transaction_date=date,
        period_id=data.period_open.period_id,
        source_document=doc,
    )


def test_sweep_flags_favorable_variance_beyond_threshold(session):
    """Baseline vendor quote $10,000; a $7,000 subsequent invoice inside the
    window is $3,000 more favorable → flagged (threshold $500)."""
    data = seed_all(session)
    baseline = _baseline(session, data)
    assumption = _assumption(session, baseline, description="ACME widget", value="10000.00")
    _txn(session, data, amount="7000.00", date=D(2026, 6, 20), doc="INV ACME WIDGET 991")
    findings = run_sweep(session, baseline)
    session.commit()
    flagged = [f for f in findings if f.flagged]
    assert len(flagged) == 1
    assert flagged[0].variance_amount == Decimal("3000.00")
    assert flagged[0].materiality_threshold_used == Decimal("500.00")
    assert "acme widget" in flagged[0].match_method.lower()
    assert baseline.sweep_status == SweepStatus.COMPLETE


def test_small_variance_is_logged_not_flagged(session):
    data = seed_all(session)
    baseline = _baseline(session, data)
    _assumption(session, baseline, description="cable assembly", value="1000.00")
    _txn(session, data, amount="700.00", date=D(2026, 6, 20), doc="INV cable assembly 12")
    findings = run_sweep(session, baseline, materiality_threshold=Decimal("400.00"))
    assert len(findings) == 1
    assert findings[0].flagged is False  # 300 variance <= 400 threshold
    assert findings[0].materiality_threshold_used == Decimal("400.00")


def test_window_discipline(session):
    """Transactions before the baseline date or after the price-agreement
    date never enter the sweep — no date gaps, no date leaks."""
    data = seed_all(session)
    baseline = _baseline(session, data, baseline=D(2026, 6, 10), agreement=D(2026, 6, 25))
    _assumption(session, baseline, description="fiber spool", value="9000.00")
    _txn(session, data, amount="1.00", date=D(2026, 6, 5), doc="fiber spool early")   # before
    _txn(session, data, amount="2.00", date=D(2026, 6, 28), doc="fiber spool late")   # after
    inside = _txn(session, data, amount="5000.00", date=D(2026, 6, 20), doc="fiber spool mid")
    findings = run_sweep(session, baseline)
    matched = [f for f in findings if f.subsequent_transaction_id is not None]
    assert [f.subsequent_transaction_id for f in matched] == [inside.transaction_id]


def test_unmatched_assumption_still_logged(session):
    data = seed_all(session)
    baseline = _baseline(session, data)
    _assumption(session, baseline, description="never purchased", value="500.00")
    findings = run_sweep(session, baseline)
    assert len(findings) == 1
    assert findings[0].subsequent_transaction_id is None
    assert "no subsequent activity matched" in findings[0].match_method
    assert findings[0].flagged is False


def test_completed_sweep_cannot_be_rerun(session):
    data = seed_all(session)
    baseline = _baseline(session, data)
    _assumption(session, baseline, description="one-shot", value="100.00")
    run_sweep(session, baseline)
    with pytest.raises(SweepError, match="immutable log"):
        run_sweep(session, baseline)


def test_baseline_locked_by_trigger(session):
    data = seed_all(session)
    baseline = _baseline(session, data)
    session.commit()
    with pytest.raises(sa.exc.IntegrityError, match="locked"):
        session.execute(
            sa.text("UPDATE tina_baselines SET baseline_date = '2020-01-01' WHERE baseline_id = :b"),
            {"b": baseline.baseline_id},
        )
    session.rollback()


def test_report_reads_from_findings_table(session):
    data = seed_all(session)
    baseline = _baseline(session, data)
    _assumption(session, baseline, description="ACME widget", value="10000.00")
    _txn(session, data, amount="7000.00", date=D(2026, 6, 20), doc="INV ACME WIDGET 991")
    run_sweep(session, baseline)
    report = sweep_delta_report(session, baseline)
    assert report["flagged_count"] == 1
    assert report["total_flagged_variance"] == "3000.00"
    assert report["comparisons"][0]["match_method"].startswith("source_document contains")
    assert report["banner"].startswith("SYNTHETIC")


def test_certification_gated_on_completed_sweep(session):
    data = seed_all(session)
    baseline = _baseline(session, data)
    _assumption(session, baseline, description="gate check", value="100.00")
    with pytest.raises(SweepError, match="cannot certify"):
        generate_certification(session, baseline)
    run_sweep(session, baseline)
    cert = generate_certification(session, baseline)
    session.commit()
    assert "accurate, complete, and current" in cert["certification"]
    assert baseline.certification_status == CertificationStatus.CERTIFIED
