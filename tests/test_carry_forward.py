"""carry_forward threshold status: a value in force by carry-forward (its
scheduled adjustment was formally waived) returns a real value WITH a caveat
and rides the reverify watch list — distinct from a settled value and from an
unseeded gap (which raises).

Motivating precedent: OMB M-26-11 cancelled the 2026 federal civil-penalty
inflation adjustment, so prior-year values carried forward. That memo governs
civil monetary penalties, NOT the exec-comp cap — so the row here is a
CLEARLY-SYNTHETIC fixture, never a real seeded value.

Pre-registered expectations (B35):
  * threshold_in_force on a date inside the carry_forward window RETURNS the row
    (does not raise); the pre-existing CY2026 exec-comp gap STILL raises.
  * _status_caveat on a carry_forward row returns a "carried forward" caveat.
  * reverification_items LISTS the carry_forward row (non-final, not superseded).
  * exec_comp_status with a carry_forward cap returns a VALUE, not a raise.
"""

import datetime

import pytest

from govcon.models.enums import ThresholdStatus
from govcon.services.cas_tina import _status_caveat
from govcon.services.compensation import exec_comp_status
from govcon.services.reverification import reverification_items
from govcon.services.thresholds import threshold_in_force
from tests.fixtures.synthetic_data import seed_all, synthetic_carry_forward_cap

D = datetime.date


def test_carry_forward_returns_value_not_raise(session):
    """The headline: carry_forward converts an unknown-year raise into a
    real, dated value — while the genuinely-unseeded CY2026 gap still raises."""
    seed_all(session)
    session.add(synthetic_carry_forward_cap())  # CY2027, status carry_forward
    session.flush()

    row = threshold_in_force(session, "EXEC_COMP_CAP", D(2027, 6, 1))
    assert row.status is ThresholdStatus.CARRY_FORWARD
    # The carried-forward value is the prior-year cap, returned (not invented).
    assert str(row.value) == "671000.00"

    # The real CY2026 gap is untouched — still an open question, still raises.
    with pytest.raises(LookupError, match="do not invent"):
        threshold_in_force(session, "EXEC_COMP_CAP", D(2026, 7, 8))


def test_carry_forward_rides_a_caveat(session):
    row = synthetic_carry_forward_cap()
    caveat = _status_caveat(row)
    assert caveat is not None
    assert "carried forward" in caveat
    # The caveat quotes the governing citation so the freeze is auditable.
    assert "SYNTHETIC TEST FIXTURE" in caveat


def test_carry_forward_on_reverify_watch_list(session):
    seed_all(session)
    session.add(synthetic_carry_forward_cap())
    session.flush()

    items = reverification_items(session, as_of=D(2027, 6, 1))
    carry = [
        i
        for i in items
        if i.kind == "non_final_threshold"
        and "carried forward" in i.description
        and "EXEC_COMP_CAP" in i.description
    ]
    assert len(carry) == 1, "the carry_forward cap must sit on the reverify watch list"


def test_carry_forward_exec_comp_returns_value(session):
    """A real consumer (exec-comp tracker) now bounds compensation off the
    carried-forward cap instead of raising. YTD is zero (no CY2027 comp
    transactions), so the point of the test is cap resolution, not the level."""
    data = seed_all(session)
    session.add(synthetic_carry_forward_cap())
    session.flush()

    status = exec_comp_status(session, data.exec_person, 2027, as_of=D(2027, 6, 1))
    assert str(status.cap) == "671000.00"
    assert status.alert_level == "ok"
