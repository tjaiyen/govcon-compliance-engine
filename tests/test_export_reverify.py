"""Phase 10: markdown exporter (banner-first, tables from canonical JSON)
and the re-verification checkpoint list."""

import datetime

from govcon.models.billing import ScheduleType
from govcon.services.export import render_markdown, render_schedule
from govcon.services.ice_schedules import BANNER, generate_schedule
from govcon.services.period_close import close_period
from govcon.services.reverification import reverification_items
from tests.fixtures.synthetic_data import seed_all

D = datetime.date


def test_rendered_schedule_leads_with_banner_and_tables(session):
    data = seed_all(session)
    close_period(session, data.period_open, closed_by="export-test")
    row = generate_schedule(session, 2026, ScheduleType.G)
    md = render_schedule(row)
    lines = md.splitlines()
    assert lines[0].startswith("# ICE Schedule G — FY2026")
    assert lines[2] == f"**{BANNER}**"  # the banner is line one of the body, always
    assert "## reconciliation by period" in md
    # list-of-dicts → table (columns alphabetical — canonical JSON is
    # stored sort_keys=True, and the renderer preserves that order):
    assert "| gl_jcl_variances | passed | period | period_id |" in md
    assert "| [] | yes | 2026-06 | 1 |" in md


def test_render_markdown_forces_banner_even_if_absent(session):
    md = render_markdown("Anything", {"total": "5.00"})
    assert f"**{BANNER}**" in md


def test_reverification_items_watch_and_due(session):
    seed_all(session)
    # Before the first checkpoint: nothing due, but every non-final
    # threshold row (class_deviation TINA, statute CAS pair, proposed CAS
    # 407, statute CDA cert) is on the watch list.
    before = reverification_items(session, D(2026, 7, 8))
    assert not any(i.due for i in before)
    watch = [i for i in before if i.kind == "non_final_threshold"]
    text = " ".join(i.description for i in watch)
    for expected in ("TINA_THRESHOLD", "CAS_CONTRACT_TRIGGER", "CAS_FULL_COVERAGE",
                     "CAS_407_STATUS", "CDA_CLAIM_CERT"):
        assert expected in text
    # After both checkpoint dates: both date checkpoints come due.
    after = reverification_items(session, D(2026, 8, 8))
    due = [i for i in after if i.due]
    assert len(due) == 2
    assert any("RFO Phase II" in i.description for i in due)
    assert any("91 FR 42139" in i.description for i in due)
