"""The Postgres-only probe from docs/POSTGRES.md §3: two concurrent writers
must not fork the audit hash chain.

SQLite's single-writer serializes the read-last-hash/insert pair by
construction, so this scenario is inexpressible there; on Postgres the
transaction-scoped advisory lock (AUDIT_CHAIN_LOCK_KEY) is what serializes
it — this test is the empirical proof that it does. Without the lock, two
simultaneous flushes read the same previous hash and the chain forks
(two rows sharing one previous_entry_hash), which verify_audit_chain
reports as a linkage failure.
"""

import concurrent.futures
import datetime
import os

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.skipif(
    not os.environ.get("GOVCON_TEST_PG"),
    reason="Postgres-only concurrency probe (SQLite's single-writer covers "
    "this by construction)",
)


def test_concurrent_writers_cannot_fork_the_audit_chain(session_factory):
    from govcon.db.audit import verify_audit_chain
    from govcon.models import AuditTrail, Period
    from govcon.models.enums import PeriodStatus

    writes_per_thread = 10

    def writer(thread_no: int) -> None:
        # each thread: its own sessions, distinct fiscal years (unique key)
        for i in range(writes_per_thread):
            with session_factory() as session:
                session.add(Period(
                    fiscal_year=2100 + thread_no,
                    period_number=i + 1,
                    start_date=datetime.date(2030, 1, 1),
                    end_date=datetime.date(2030, 1, 28),
                    status=PeriodStatus.OPEN,
                ))
                session.commit()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(writer, [1, 2]))  # raises if any writer failed

    with session_factory() as session:
        n = session.execute(
            sa.select(sa.func.count())
            .select_from(AuditTrail)
            .where(AuditTrail.table_name == "periods")
        ).scalar()
        assert n == 2 * writes_per_thread  # every write audited exactly once

        # the linchpin: a forked chain would reuse a previous_entry_hash
        dupes = session.execute(
            sa.select(AuditTrail.previous_entry_hash, sa.func.count())
            .group_by(AuditTrail.previous_entry_hash)
            .having(sa.func.count() > 1)
        ).all()
        assert dupes == [], f"chain forked at {dupes}"

        ok, bad = verify_audit_chain(session)
        assert ok, f"chain failed verification at trail_id {bad}"
