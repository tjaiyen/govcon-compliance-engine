"""Regression tests for the 2026-07-08 stress-test findings. Each test
would FAIL against the pre-fix code and pins the fix so it can't silently
regress. IDs reference the stress-test session note.
"""

import datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa

from govcon.models import GLAccount, GLTransaction, IndirectPool, Voucher
from govcon.models.enums import CostType, PoolName, PoolStatus, RateType
from tests.fixtures.synthetic_data import (
    ga_pool,
    seed_all,
    synthetic_exec_comp_cap,
)

D = datetime.date


# --- CRIT-1: exec-comp YTD counts only compensation accounts ----------------


def test_ytd_compensation_ignores_non_comp_person_transactions(session):
    from govcon.services.allowability import post_transaction
    from govcon.services.compensation import exec_comp_status, ytd_compensation

    data = seed_all(session)
    s = session
    s.add(synthetic_exec_comp_cap())
    gp = ga_pool()
    s.add(gp)
    s.flush()
    comp = GLAccount(account_code="8100", account_name="Exec Comp", cost_type=CostType.INDIRECT,
                     pool_assignment=gp.pool_id, is_compensation=True)
    s.add(comp)
    s.flush()
    # 400k comp + a 5k travel reimbursement on the SAME person (not comp).
    post_transaction(s, account_id=comp.account_id, person_id=data.exec_person.person_id,
                     amount=Decimal("400000.00"), transaction_date=D(2026, 6, 20),
                     period_id=data.period_open.period_id, source_document="PAY")
    post_transaction(s, account_id=data.acct_direct_labor.account_id,
                     person_id=data.exec_person.person_id,
                     contract_id=data.contracts["pre_ndaa"].contract_id,
                     amount=Decimal("5000.00"), transaction_date=D(2026, 6, 21),
                     period_id=data.period_open.period_id, source_document="TRAVEL")
    assert ytd_compensation(s, data.exec_person, 2026) == Decimal("400000.00")  # not 405000
    # 400k / 500k cap = 80% informational, NOT exceeded.
    assert exec_comp_status(s, data.exec_person, 2026, D(2026, 6, 20)).alert_level == "informational"


# --- CRIT-2 / F2: pool numerator by (name, fy), scoped to the fiscal year ---


def _final_fringe(session, base="100000.00"):
    row = IndirectPool(pool_name=PoolName.FRINGE, fiscal_year=2026,
                       rate_type=RateType.ACTUAL_FINAL, status=PoolStatus.PENDING,
                       allocation_base_amount=Decimal(base))
    session.add(row)
    session.flush()
    return row


def test_actual_final_pool_sees_the_same_numerator_as_provisional(session):
    from govcon.services.rates import calculate_pool_rate

    data = seed_all(session)  # provisional fringe has the $400 fixture txn
    final = _final_fringe(session)
    calculate_pool_rate(session, data.pool)
    calculate_pool_rate(session, final)
    # Both pools (same name+fy) compute the SAME numerator — not 0 for final.
    assert final.pool_balance == data.pool.pool_balance == Decimal("400.00")
    assert final.calculated_rate == Decimal("0.0040")


def test_pool_numerator_excludes_other_fiscal_years(session):
    from govcon.services.rates import calculate_pool_rate

    data = seed_all(session)
    # An FY2027 period + a $999 fringe txn in it must NOT contaminate FY2026.
    from govcon.models import Period

    p2027 = Period(fiscal_year=2027, period_number=1, start_date=D(2027, 1, 1),
                   end_date=D(2027, 1, 31), status=__import__("govcon.models.enums", fromlist=["PeriodStatus"]).PeriodStatus.OPEN)
    session.add(p2027)
    session.flush()
    session.add(GLTransaction(account_id=data.acct_fringe.account_id, amount=Decimal("999.00"),
                              transaction_date=D(2027, 1, 15), period_id=p2027.period_id,
                              source_document="NEXT-YEAR"))
    session.flush()
    calculate_pool_rate(session, data.pool)
    assert data.pool.pool_balance == Decimal("400.00")  # not 1399.00


# --- F3: corrections carry a freshly-evaluated allowability vector ----------


def test_correction_rows_are_stamped_with_allowability(session):
    from govcon.services.corrections import post_correction

    data = seed_all(session)
    session.commit()
    reversal, replacement = post_correction(session, data.txn_direct, amount=Decimal("1300.00"))
    session.commit()
    for row in (reversal, replacement):
        assert row.allowability_vector is not None
        assert row.allowability_vector["allocability_classification"] == "direct_specific"


# --- F5: Eichleay billings are windowed to the performance period -----------


def test_eichleay_billings_windowed(session):
    from govcon.models import Contract, JCLEntry
    from govcon.models.enums import AgencyType, CASCoverageType, ContractorSize, CostElement
    from govcon.services.allowability import post_transaction
    from govcon.services.eichleay import calculate_eichleay
    from govcon.services.period_close import close_period

    data = seed_all(session)
    delayed = Contract(agency_type=AgencyType.DOD, award_date=D(2025, 12, 1),
                       performance_start_date=D(2026, 6, 1), performance_end_date=D(2026, 6, 30),
                       contract_value=Decimal("2000000.00"), tina_threshold_snapshot=Decimal("2500000.00"),
                       cas_trigger_threshold_snapshot=Decimal("7500000.00"),
                       cas_coverage_type=CASCoverageType.NONE, contractor_size=ContractorSize.OTHER_THAN_SMALL)
    session.add(delayed)
    session.flush()
    # In-window voucher (counts) + out-of-window voucher (must NOT count).
    for contract, amt, bdate, doc in (
        (delayed, Decimal("200000.00"), D(2026, 6, 15), "in"),
        (data.contracts["post_ndaa"], Decimal("800000.00"), D(2026, 6, 15), "in-other"),
        (delayed, Decimal("500000.00"), D(2020, 1, 1), "OUT-of-window"),
    ):
        post_transaction(session, account_id=data.acct_direct_labor.account_id,
                         contract_id=contract.contract_id, amount=amt,
                         transaction_date=D(2026, 6, 15), period_id=data.period_open.period_id,
                         source_document=doc)
        session.add(JCLEntry(contract_id=contract.contract_id, clin_id="1", wbs_id="9",
                             cost_element=CostElement.LABOR, amount=amt, period_id=data.period_open.period_id))
        session.add(Voucher(contract_id=contract.contract_id, period_id=data.period_open.period_id,
                            amount_billed=amt, billing_date=bdate))
    session.flush()
    close_period(session, data.period_open, closed_by="t")
    claim, _ = calculate_eichleay(session, delayed, delay_start=D(2026, 5, 1), delay_end=D(2026, 5, 10),
                                  total_home_office_overhead=Decimal("150000.00"),
                                  government_caused_delay=True, contractor_on_standby=True,
                                  no_replacement_work=True)
    # In-window billings only: contract 200k of company (200k+800k)=1,000,000.
    # The 500k OUT-of-window voucher must be excluded from BOTH.
    assert claim.contract_billings_amount == Decimal("200000.00")
    assert claim.total_company_billings_amount == Decimal("1000000.00")


# --- F6: TINA certification requires EXCEEDING the threshold ----------------


def test_tina_exactly_at_threshold_not_required(session):
    from govcon.models import ContractAction
    from govcon.models.enums import ContractActionType
    from govcon.services.cas_tina import determine_tina_applicability

    data = seed_all(session)
    action = ContractAction(contract_id=data.contracts["pre_ndaa"].contract_id,
                            action_type=ContractActionType.TASK_ORDER, action_date=D(2026, 6, 15),
                            proposed_value=Decimal("2500000.00"))  # exactly the $2.5M threshold
    session.add(action)
    session.flush()
    result = determine_tina_applicability(session, action)
    assert result.certification_required is False  # at threshold = not required
    assert result.above_threshold is False


# --- ENFORCEMENT: immutability triggers on the compliance-critical tables ---


def test_regulatory_thresholds_are_append_only(session):
    data = seed_all(session)
    session.commit()
    with pytest.raises(sa.exc.IntegrityError, match="append-only"):
        session.execute(sa.text("UPDATE regulatory_thresholds SET value='1.00' WHERE rule_name='SAT'"))
    session.rollback()
    with pytest.raises(sa.exc.IntegrityError, match="append-only"):
        session.execute(sa.text("DELETE FROM regulatory_thresholds WHERE rule_name='SAT'"))
    session.rollback()


def test_standard_costs_substance_frozen(session):
    from govcon.models import StandardCost
    from govcon.models.standard_costing import StandardCostElement

    seed_all(session)
    s = StandardCost(cost_element=StandardCostElement.LABOR, operation_or_product_code="X",
                     standard_quantity=Decimal("1"), standard_rate=Decimal("10"), effective_date=D(2026, 1, 1))
    session.add(s)
    session.commit()
    with pytest.raises(sa.exc.IntegrityError, match="frozen"):
        session.execute(sa.text("UPDATE standard_costs SET standard_rate='99' WHERE standard_cost_id=:i"),
                        {"i": s.standard_cost_id})
    session.rollback()
    # superseded_date IS updatable (supersession path):
    session.execute(sa.text("UPDATE standard_costs SET superseded_date='2026-06-01' WHERE standard_cost_id=:i"),
                    {"i": s.standard_cost_id})
    session.commit()


def test_locked_pool_rate_cannot_be_recalculated_at_db_layer(session):
    data = seed_all(session)
    from govcon.services.rates import calculate_pool_rate
    calculate_pool_rate(session, data.pool)
    session.execute(sa.text("UPDATE indirect_pools SET status='locked' WHERE pool_id=:p"),
                    {"p": data.pool.pool_id})
    session.commit()
    with pytest.raises(sa.exc.IntegrityError, match="LOCKED"):
        session.execute(sa.text("UPDATE indirect_pools SET calculated_rate='0.99' WHERE pool_id=:p"),
                        {"p": data.pool.pool_id})
    session.rollback()


# --- AUDIT: contiguity catches mid-chain deletion ---------------------------


def test_audit_chain_detects_mid_chain_deletion(session, engine):
    from govcon.db.audit import verify_audit_chain

    seed_all(session)
    session.commit()
    ok, _ = verify_audit_chain(session)
    assert ok
    # Delete a middle row out of band (drop the guard trigger first).
    pg = engine.dialect.name == "postgresql"
    drop = ("DROP TRIGGER trg_audit_trail_no_delete ON audit_trail" if pg
            else "DROP TRIGGER trg_audit_trail_no_delete")
    session.rollback()  # release the session's PG locks before out-of-band DDL
    with engine.connect() as conn:
        conn.execute(sa.text(drop))
        conn.execute(sa.text("DELETE FROM audit_trail WHERE trail_id = 3"))
        conn.commit()
    session.expire_all()
    ok, bad = verify_audit_chain(session)
    # SQLite's gapless-id belt reports the missing id itself; Postgres (where
    # sequences legitimately gap on rollback) detects via hash linkage, so the
    # first surviving row whose previous_entry_hash no longer matches is
    # reported — row 4. Either way the deletion is caught.
    assert not ok and bad == (4 if pg else 3)


# --- ROBUSTNESS regressions -------------------------------------------------


def test_empty_audit_chain_verifies_ok(session):
    """A freshly-migrated DB with no writes yet has a trivially-valid chain."""
    from govcon.db.audit import verify_audit_chain

    ok, bad = verify_audit_chain(session)
    assert ok and bad is None


def test_sf1408_empty_db_reports_no_data_not_vacuous_pass(session):
    """An empty DB must not read as six passing criteria (F06)."""
    from govcon.services.sf1408 import run_self_check

    results = run_self_check(session)  # no seed_all
    assert len(results) == 1
    assert results[0].passed is False
    assert "empty database" in results[0].findings[0]


def test_reverify_is_advisory_by_default(session):
    """reverify lists items but exits 0 by default (F02) — probed at the
    service level; the CLI wraps this and only exits 1 under --strict."""
    import datetime

    from govcon.services.reverification import reverification_items

    seed_all(session)
    items = reverification_items(session, datetime.date(2030, 1, 1))  # long after checkpoints
    assert any(i.due for i in items)  # items ARE due...
    # ...but the surface is a list; nothing here forces a nonzero exit.


def test_audit_response_tasks_have_a_service(session):
    """audit_response_tasks is no longer a dead table (F01)."""
    import datetime

    from govcon.models.monitoring import AuditType, TaskStatus
    from govcon.services.audit_response import (
        add_task,
        complete_task,
        create_notification,
        sweep_overdue_tasks,
    )

    seed_all(session)
    n = create_notification(session, audit_type=AuditType.INCURRED_COST,
                            received_date=datetime.date(2026, 7, 1), response_deadline_days=45)
    t1 = add_task(session, n, description="collect FY2025 ICS", owner="tj",
                  due_date=datetime.date(2026, 7, 5))
    t2 = add_task(session, n, description="labor distribution", owner="tj",
                  due_date=datetime.date(2026, 7, 20))
    complete_task(session, t1)
    # Sweep after BOTH due dates: t1 is complete (not swept), t2 is open+past-due.
    overdue = sweep_overdue_tasks(session, n, as_of=datetime.date(2026, 7, 25))
    assert t1.status == TaskStatus.COMPLETE
    assert overdue == [t2] and t2.status == TaskStatus.OVERDUE


def test_no_core_dml_on_business_tables_in_services():
    """Architectural guard (AFI-008): the audit listener + ORM guards fire on
    session flush events, so a service that mutates a business table via Core
    DML (session.execute(insert/update/delete)) would bypass BOTH. Only the
    audit listener itself may use Core insert (into audit_trail). This test
    fails if any service introduces Core DML on a business table."""
    import pathlib
    import re

    services = pathlib.Path(__file__).resolve().parent.parent / "src" / "govcon" / "services"
    offenders = []
    # Flag BOTH the expression form (sa.insert(...)) AND raw-SQL DML
    # (session.execute(sa.text("UPDATE ..."))) — either bypasses the audit
    # listener. The raw-SQL branch was a latent hole this stress test closed.
    expr_dml = re.compile(r"session\.execute\(\s*sa\.(insert|update|delete)\(", re.S)
    text_dml = re.compile(
        r"""session\.execute\(\s*sa\.text\(\s*["']?\s*(insert|update|delete)\b""",
        re.S | re.I,
    )
    for py in services.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        if expr_dml.search(text) or text_dml.search(text):
            offenders.append(py.name)
    assert offenders == [], f"Core DML on business tables bypasses audit+guards: {offenders}"
