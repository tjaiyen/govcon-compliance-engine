"""Phase 6: each of the six SF 1408 assertions passes against the clean
fixture state AND fails against a deliberately-broken one — a self-check
that could never fail is worse than none (roadmap Phase 6)."""

import datetime
from decimal import Decimal

import sqlalchemy as sa

from govcon.models import Period, Voucher
from govcon.models.enums import PeriodStatus, ReconciliationStatus
from govcon.services.sf1408 import explain_limitations, run_self_check
from tests.fixtures.synthetic_data import seed_all

D = datetime.date


def _results_by_criterion(session):
    return {r.criterion: r for r in run_self_check(session)}


def test_clean_state_passes_all_six(session):
    seed_all(session)
    results = run_self_check(session)
    assert [r.criterion for r in results] == ["A", "B", "C", "D", "E", "F"]
    assert all(r.passed for r in results), [
        (r.criterion, r.findings) for r in results if not r.passed
    ]


def _bypass_checks(session, ddl: str, *, pg_drop_constraints=()) -> None:
    """Simulate out-of-band corruption to seed a structurally-invalid row the
    self-check must catch. On SQLite the CHECK constraints are suspended via
    PRAGMA; on Postgres (which has no session-level PRAGMA) the specific named
    constraint is dropped — the test database is per-test disposable, so this
    is contained. Runs the criterion DETECTION SQL on BOTH backends (a
    Postgres-only bug in that SQL would otherwise hide behind a green suite)."""
    if session.get_bind().dialect.name == "postgresql":
        for constraint in pg_drop_constraints:
            session.execute(
                sa.text(f"ALTER TABLE gl_accounts DROP CONSTRAINT IF EXISTS {constraint}")
            )
        session.execute(sa.text(ddl))
    else:
        session.execute(sa.text("PRAGMA ignore_check_constraints = ON"))
        session.execute(sa.text(ddl))
        session.execute(sa.text("PRAGMA ignore_check_constraints = OFF"))


def test_criterion_a_fails_on_poolless_indirect_account(session):
    seed_all(session)
    session.commit()
    _bypass_checks(
        session,
        "INSERT INTO gl_accounts (account_code, account_name, cost_type) "
        "VALUES ('6666', 'Orphan Indirect', 'indirect')",
        pg_drop_constraints=["ck_gl_accounts_indirect_requires_pool"],
    )
    r = _results_by_criterion(session)["A"]
    assert not r.passed and "6666" in r.findings[0]


def test_criterion_b_fails_on_direct_txn_without_contract(session):
    data = seed_all(session)
    session.commit()
    drop = ("DROP TRIGGER trg_gl_transactions_direct_needs_contract ON gl_transactions"
            if session.get_bind().dialect.name == "postgresql"
            else "DROP TRIGGER trg_gl_transactions_direct_needs_contract")
    session.execute(sa.text(drop))
    session.execute(
        sa.text(
            "INSERT INTO gl_transactions (account_id, amount, transaction_date, period_id) "
            "VALUES (:a, '10.00', '2026-06-23', :p)"
        ),
        {"a": data.acct_direct_labor.account_id, "p": data.period_open.period_id},
    )
    r = _results_by_criterion(session)["B"]
    assert not r.passed and "no contract" in r.findings[0]


def test_criterion_c_fails_on_pool_with_costs_but_no_base(session):
    """Achievable through the ORM — the base is legitimately nullable until
    rate calculation; the self-check flags pools that already carry costs."""
    data = seed_all(session)
    data.pool.allocation_base_amount = None
    session.flush()
    r = _results_by_criterion(session)["C"]
    assert not r.passed and "allocation base" in r.findings[0]


def test_criterion_d_fails_on_unallowable_with_pool_or_missing_citation(session):
    data = seed_all(session)
    session.commit()
    _bypass_checks(
        session,
        f"INSERT INTO gl_accounts (account_code, account_name, cost_type, pool_assignment) "
        f"VALUES ('7999', 'Bad Unallowable', 'unallowable', {data.pool.pool_id})",
        pg_drop_constraints=["ck_gl_accounts_pool_only_if_indirect"],
    )
    r = _results_by_criterion(session)["D"]
    assert not r.passed
    assert any("rate numerator" in f for f in r.findings)
    assert any("no FAR 31.205 citation" in f for f in r.findings)


def test_criterion_e_fails_on_closed_period_without_reconciliation(session):
    """INSERTing a pre-closed period bypasses the close-gate trigger (which
    fires on UPDATE only) — precisely the hole this data-state check covers."""
    seed_all(session)
    session.add(
        Period(
            fiscal_year=2025, period_number=12,
            start_date=D(2025, 12, 1), end_date=D(2025, 12, 31),
            status=PeriodStatus.CLOSED,
            reconciliation_status=ReconciliationStatus.PENDING,
        )
    )
    session.flush()
    r = _results_by_criterion(session)["E"]
    assert not r.passed and "2025-12" in r.findings[0]


def test_criterion_f_fails_on_billing_beyond_ledger(session):
    data = seed_all(session)
    session.add(
        Voucher(
            contract_id=data.contracts["pre_ndaa"].contract_id,
            period_id=data.period_open.period_id,
            amount_billed=Decimal("50000.00"),
            billing_date=D(2026, 6, 29),
        )
    )
    session.flush()
    r = _results_by_criterion(session)["F"]
    assert not r.passed and "billed" in r.findings[0]


def test_limitations_text_states_the_hard_truths():
    text = explain_limitations()
    for required in (
        "SYNTHETIC DATA",
        "SEGREGATION OF DUTIES",
        "DESIGN vs. OPERATION",
        "NOT A CERTIFICATION",
        "six verified structural",
    ):
        assert required in text
