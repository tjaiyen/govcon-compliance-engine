"""End-to-end demo: a fresh synthetic world through the REAL service layer.

Run:  uv run python scripts/demo.py
Creates demo.db (gitignored), migrates it, seeds a readable scenario,
closes the period, generates Schedules G/H/I/N, exports markdown to
demo_out/ (gitignored), and — the render-into-vault direction — writes one
audit-report note into the Obsidian vault's `01 - Notes/` (skipped cleanly
if the vault isn't present on this machine).

SYNTHETIC DATA — NOT FOR REGULATORY RELIANCE. Every figure below is
invented.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import subprocess
import sys
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / "demo.db"
OUT = ROOT / "demo_out"
VAULT_NOTES = pathlib.Path.home() / "Obsidian/TJ_Vault/govcon-compliance-engine/01 - Notes"

os.environ["GOVCON_DB_URL"] = f"sqlite:///{DB}"
D = datetime.date


def main() -> None:
    if DB.exists():
        DB.unlink()
    OUT.mkdir(exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT, check=True, capture_output=True,
    )

    from govcon.db.engine import make_engine, make_session_factory
    from govcon.models import (
        Contract,
        GLAccount,
        IndirectPool,
        JCLEntry,
        Period,
        Person,
        Voucher,
    )
    from govcon.models.billing import ScheduleType, SignerRole
    from govcon.models.enums import (
        AgencyType,
        CASCoverageType,
        ContractorSize,
        CostElement,
        CostType,
        PeriodStatus,
        PoolName,
        PoolStatus,
        RateType,
    )
    from govcon.services.allowability import post_transaction
    from govcon.services.cas_tina import determine_cas_coverage
    from govcon.services.contract_statement import contract_statement
    from govcon.services.export import render_markdown, render_schedule
    from govcon.services.ice_schedules import generate_schedule
    from govcon.services.period_close import close_period
    from govcon.services.rates import calculate_pool_rate, derive_pool_base

    factory = make_session_factory(make_engine())
    with factory() as session:
        # --- a small, readable world (all synthetic) -----------------------
        contract = Contract(
            agency_type=AgencyType.DOD,
            award_date=D(2026, 7, 15),  # post-NDAA: $10M TINA / $35M CAS regime
            performance_start_date=D(2026, 8, 1),
            performance_end_date=D(2027, 7, 31),
            contract_value=Decimal("40000000.00"),
            tina_threshold_snapshot=Decimal("10000000.00"),
            cas_trigger_threshold_snapshot=Decimal("35000000.00"),
            cas_coverage_type=CASCoverageType.MODIFIED,
            contractor_size=ContractorSize.OTHER_THAN_SMALL,
        )
        period = Period(
            fiscal_year=2026, period_number=6,
            start_date=D(2026, 6, 1), end_date=D(2026, 6, 30),
            status=PeriodStatus.OPEN,
        )
        fringe = IndirectPool(
            pool_name=PoolName.FRINGE, fiscal_year=2026,
            rate_type=RateType.ACTUAL_FINAL, status=PoolStatus.PENDING,
        )
        session.add_all([contract, period, fringe])
        session.flush()

        direct_labor = GLAccount(
            account_code="5000", account_name="Direct Labor",
            cost_type=CostType.DIRECT, is_labor=True,
        )
        fringe_acct = GLAccount(
            account_code="6100", account_name="Fringe — Health Insurance",
            cost_type=CostType.INDIRECT, pool_assignment=fringe.pool_id,
        )
        session.add_all([direct_labor, fringe_acct])
        session.flush()

        engineer = Person(person_name="Demo Engineer", role="Sr. Engineer")
        session.add(engineer)
        session.flush()

        # Post through the REAL write path (allowability stamped at capture).
        post_transaction(
            session, account_id=direct_labor.account_id,
            contract_id=contract.contract_id, person_id=engineer.person_id,
            amount=Decimal("120000.00"), transaction_date=D(2026, 6, 15),
            period_id=period.period_id, source_document="TS-DEMO-0615",
        )
        post_transaction(
            session, account_id=fringe_acct.account_id,
            amount=Decimal("36000.00"), transaction_date=D(2026, 6, 20),
            period_id=period.period_id, source_document="INV-DEMO-HC",
        )
        session.add(JCLEntry(
            contract_id=contract.contract_id, clin_id="0001", wbs_id="1.1",
            cost_element=CostElement.LABOR, amount=Decimal("120000.00"),
            quantity=Decimal("2000"), period_id=period.period_id,
        ))
        session.add(Voucher(
            contract_id=contract.contract_id, period_id=period.period_id,
            amount_billed=Decimal("100000.00"), billing_date=D(2026, 6, 30),
        ))
        session.flush()

        # Rates: derive the fringe base from the ledger, calculate, approve.
        derive_pool_base(session, fringe)
        calculate_pool_rate(session, fringe)
        from govcon.services.rates import approve_rate
        approve_rate(session, fringe)

        # Close the period (the gated three-way reconciliation) and generate.
        close_period(session, period, closed_by="demo")
        session.commit()

        schedules = {}
        for stype in (ScheduleType.G, ScheduleType.H, ScheduleType.I):
            schedules[stype] = generate_schedule(session, 2026, stype)
        schedules[ScheduleType.N] = generate_schedule(
            session, 2026, ScheduleType.N,
            signer_name="Demo CFO", signer_role=SignerRole.CFO,
        )
        session.commit()

        for stype, row in schedules.items():
            path = OUT / f"schedule_{stype.value}_FY2026.md"
            path.write_text(render_schedule(row), encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT)}")

        statement = contract_statement(session, contract)
        (OUT / "contract_statement.md").write_text(
            render_markdown(f"Contract {contract.contract_id} — Financial Statement", statement),
            encoding="utf-8",
        )
        print("wrote demo_out/contract_statement.md")

        cas = determine_cas_coverage(session, contract)
        g = schedules[ScheduleType.G]
        fringe_rate = fringe.calculated_rate

        # --- render INTO the vault (B41), if it exists ----------------------
        if VAULT_NOTES.is_dir():
            today = datetime.date.today().isoformat()
            status = "compliant" if g.reconciliation_status.value == "passed" else "flagged"
            note = VAULT_NOTES / f"{today} - Audit Report - demo-contract-{contract.contract_id}.md"
            note.write_text(f"""---
date: {today}
type: audit-report
contract_id: {contract.contract_id}
compliance_status: {status}
unallowable_total: "0.00"
rate_variance: "0.0000"
generated_by: govcon-engine demo ({today})
tags: [audit-report]
---

# Compliance Audit Report

**SYNTHETIC DATA — NOT FOR REGULATORY RELIANCE**

## Executive Summary

Demo run against a fully synthetic world: one post-NDAA DoD contract
($40M, modified CAS coverage — {cas.tier}), one closed period with a
passing GL/JCL/billing three-way reconciliation, Schedules G/H/I/N
generated, fringe rate {fringe_rate} derived from the ledger.

## Calculated Rates

| pool | rate_type | rate | basis |
|---|---|---|---|
| fringe | actual_final | {fringe_rate} | base derived from is_labor accounts |

## Unallowable Items Detected

None in this demo scenario (the engine's criterion-D filters were active
on every query).

## Variance Analysis

Schedule G reconciliation: {g.reconciliation_status.value} for every period.

## Action Items

None — demo data.

## Evidence / Audit Trail Reference

Full hash-chained audit trail in demo.db (`uv run govcon audit verify`);
schedule JSON in ice_schedules.content; markdown renders in demo_out/.
""", encoding="utf-8")
            print(f"wrote vault note: {note.name}")
        else:
            print("vault not present — skipped the vault audit-report note")

    print("\nDemo complete. Explore:")
    print("  GOVCON_DB_URL=sqlite:///demo.db uv run govcon sf1408")
    print("  GOVCON_DB_URL=sqlite:///demo.db uv run govcon audit verify")
    print(f"  GOVCON_DB_URL=sqlite:///demo.db uv run govcon contract {contract.contract_id}")


if __name__ == "__main__":
    main()
