"""TINA compliance sweep workflow simulator (spec §8).

Five steps: assemble data → baseline locked → fact-finding → SWEEP →
certification. This module is steps 4-5. The sweep is deterministic and
reproducible: for each locked baseline assumption, compare subsequent
ledger activity for the same contract between the baseline date and the
price-agreement date, and write ONE tina_sweep_findings row per comparison
— including the match method and the materiality threshold actually used.
The full set of rows for a baseline IS the sweep log; a DCAA auditor runs
equivalent logic post-award, so the log must show exactly what was
compared, how, and against what threshold.

v1 matching (the §8 "real design decision," decided here): an assumption
matches a subsequent gl_transaction on the same contract when the
assumption's description appears (case-insensitive) in the transaction's
source_document. Recorded verbatim in match_method per finding.
"""

from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.core.decimal_config import quantize_money
from govcon.core.errors import GovconError
from govcon.models import (
    GLTransaction,
    TINABaseline,
    TINABaselineAssumption,
    TINASweepFinding,
)
from govcon.models.tina import CertificationStatus, SweepStatus

#: Configurable engineering default, NOT a regulatory figure — recorded on
#: every finding row either way (§0.1: never a bare hard-coded number).
DEFAULT_MATERIALITY = Decimal("500.00")


class SweepError(GovconError):
    pass


def run_sweep(
    session: Session,
    baseline: TINABaseline,
    *,
    materiality_threshold: Decimal = DEFAULT_MATERIALITY,
) -> list[TINASweepFinding]:
    """Step 4: the deterministic sweep at price-agreement date.

    Refuses to re-run a completed sweep — the findings are the immutable
    log; a corrected sweep is a new baseline scenario, not an overwrite.
    """
    if baseline.sweep_status == SweepStatus.COMPLETE:
        raise SweepError(
            f"sweep for baseline {baseline.baseline_id} already complete — findings "
            "are the immutable log; do not overwrite"
        )
    baseline.sweep_status = SweepStatus.IN_PROGRESS
    session.flush()

    assumptions = session.execute(
        sa.select(TINABaselineAssumption)
        .where(TINABaselineAssumption.baseline_id == baseline.baseline_id)
        .order_by(TINABaselineAssumption.assumption_id)
    ).scalars().all()

    findings: list[TINASweepFinding] = []
    for assumption in assumptions:
        method = f"source_document contains (case-insensitive): {assumption.description!r}"
        matches = session.execute(
            sa.select(GLTransaction)
            .where(GLTransaction.contract_id == baseline.contract_id)
            .where(GLTransaction.transaction_date > baseline.baseline_date)
            .where(GLTransaction.transaction_date <= baseline.price_agreement_date)
            .where(
                sa.func.lower(GLTransaction.source_document).contains(
                    assumption.description.lower()
                )
            )
            .order_by(GLTransaction.transaction_id)
        ).scalars().all()

        if not matches:
            findings.append(
                TINASweepFinding(
                    baseline_id=baseline.baseline_id,
                    assumption_id=assumption.assumption_id,
                    materiality_threshold_used=materiality_threshold,
                    match_method=method + " — no subsequent activity matched",
                    flagged=False,
                )
            )
            continue

        for txn in matches:
            subsequent = Decimal(txn.amount)
            # "More favorable" = subsequent cost LOWER than the baseline
            # assumption (the government would have negotiated a lower price
            # had it known) — the defective-pricing exposure.
            variance = quantize_money(assumption.baseline_value - subsequent)
            findings.append(
                TINASweepFinding(
                    baseline_id=baseline.baseline_id,
                    assumption_id=assumption.assumption_id,
                    subsequent_transaction_id=txn.transaction_id,
                    subsequent_value=subsequent,
                    variance_amount=variance,
                    materiality_threshold_used=materiality_threshold,
                    match_method=method,
                    flagged=variance > materiality_threshold,
                )
            )

    session.add_all(findings)
    baseline.sweep_status = SweepStatus.COMPLETE
    session.flush()
    return findings


def sweep_delta_report(session: Session, baseline: TINABaseline) -> dict:
    """The Sweep Delta Reconciliation Report — generated FROM the findings
    table (the log is the source, never opaque side logic)."""
    rows = session.execute(
        sa.select(TINASweepFinding, TINABaselineAssumption)
        .join(
            TINABaselineAssumption,
            TINASweepFinding.assumption_id == TINABaselineAssumption.assumption_id,
        )
        .where(TINASweepFinding.baseline_id == baseline.baseline_id)
        .order_by(TINASweepFinding.finding_id)
    ).all()
    flagged = [f for f, _ in rows if f.flagged]
    return dict(
        banner="SYNTHETIC DATA — NOT FOR REGULATORY RELIANCE",
        baseline_id=baseline.baseline_id,
        window=dict(
            baseline_date=baseline.baseline_date.isoformat(),
            price_agreement_date=baseline.price_agreement_date.isoformat(),
        ),
        comparisons=[
            dict(
                finding_id=f.finding_id,
                assumption=a.description,
                assumption_type=a.assumption_type.value,
                baseline_value=str(a.baseline_value),
                subsequent_value=None if f.subsequent_value is None else str(f.subsequent_value),
                variance=None if f.variance_amount is None else str(f.variance_amount),
                threshold_used=str(f.materiality_threshold_used),
                match_method=f.match_method,
                flagged=f.flagged,
            )
            for f, a in rows
        ],
        flagged_count=len(flagged),
        total_flagged_variance=str(
            sum((Decimal(f.variance_amount) for f in flagged), Decimal("0.00"))
        ),
    )


CERTIFICATION_TEXT = (
    "Certificate of Current Cost or Pricing Data (FAR 15.406-2 structure, per "
    "the FAR 15.403-4 requirement; SYNTHETIC exercise): This is to certify "
    "that, to the best of my knowledge and belief, the cost or pricing data "
    "submitted are accurate, complete, and current as of the date of price "
    "agreement."
)


def generate_certification(session: Session, baseline: TINABaseline) -> dict:
    """Step 5: certification — refuses until the sweep has actually run
    (certifying un-swept data is the defective-pricing failure mode this
    whole workflow exists to prevent)."""
    if baseline.sweep_status != SweepStatus.COMPLETE:
        raise SweepError(
            f"baseline {baseline.baseline_id}: cannot certify before the sweep is "
            "complete — a certificate over un-swept data is exactly the defective-"
            "pricing exposure this workflow prevents"
        )
    baseline.certification_status = CertificationStatus.CERTIFIED
    session.flush()
    return dict(
        banner="SYNTHETIC DATA — NOT FOR REGULATORY RELIANCE",
        baseline_id=baseline.baseline_id,
        price_agreement_date=baseline.price_agreement_date.isoformat(),
        certification=CERTIFICATION_TEXT,
    )
