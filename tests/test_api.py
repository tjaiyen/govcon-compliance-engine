"""Phase 0 web/API tests — the FastAPI layer over the pure services.

Runs against the real migrated DB (thresholds seeded via migration 0002), so
the determinations exercise the actual dated-threshold logic. Every endpoint
must carry the service's reasons/caveats through to the response — that data
is what the guided UI turns into plain-language teaching.

Pre-registered expectations (B35):
  * threshold TINA on 2026-07-15 = $10,000,000.00, status class_deviation.
  * threshold TINA on 2024-06-01 = $2,000,000.00, status final_rule.
  * CAS: $50M award 2026-07-15, other-than-small => tier "modified" (>=35M
    trigger, <100M full), with a non-final-status caveat.
  * CAS: small business => "exempt_small_business".
  * TINA: $8M action 2026-08-01 => not above the $10M bar, cert not required.
  * TINA: $12M same date => above, certification_required True.
  * TINA: $12M with adequate-price-competition exception => not required.
"""

from fastapi.testclient import TestClient

from govcon.api import create_app


def client(session_factory) -> TestClient:
    return TestClient(create_app(session_factory=session_factory))


def test_threshold_dated_lookup(session_factory):
    c = client(session_factory)
    r = c.get("/api/threshold", params={"rule": "TINA_THRESHOLD", "on": "2026-07-15"})
    body = r.json()
    assert body["in_force"] is True
    assert body["value"] == "10000000.00"
    assert body["status"] == "class_deviation"
    assert body["source_citation"]  # citation always present

    r2 = c.get("/api/threshold", params={"rule": "TINA_THRESHOLD", "on": "2024-06-01"})
    b2 = r2.json()
    assert b2["value"] == "2000000.00"
    assert b2["status"] == "final_rule"


def test_threshold_none_in_force_is_graceful(session_factory):
    c = client(session_factory)
    r = c.get("/api/threshold", params={"rule": "NO_SUCH_RULE", "on": "2026-07-15"})
    assert r.status_code == 200
    assert r.json()["in_force"] is False  # flagged, not a 500


def test_cas_modified_tier_with_caveat(session_factory):
    c = client(session_factory)
    r = c.post("/api/cas", json={
        "award_date": "2026-07-15",
        "contract_value": "50000000.00",
        "contractor_size": "other_than_small",
        "is_nontraditional_dc": False,
        "agency_type": "dod",
    })
    body = r.json()
    assert body["available"] is True
    assert body["tier"] == "modified"
    assert body["reasons"]  # explains WHY
    assert body["caveats"]  # surfaces the non-final regulatory status


def test_cas_small_business_exempt(session_factory):
    c = client(session_factory)
    r = c.post("/api/cas", json={
        "award_date": "2026-07-15",
        "contract_value": "50000000.00",
        "contractor_size": "small",
    })
    assert r.json()["tier"] == "exempt_small_business"


def test_tina_below_bar_not_required(session_factory):
    c = client(session_factory)
    r = c.post("/api/tina", json={
        "action_date": "2026-08-01",
        "proposed_value": "8000000.00",
    })
    body = r.json()
    assert body["available"] is True
    assert body["threshold_value"] == "10000000.00"
    assert body["above_threshold"] is False
    assert body["certification_required"] is False


def test_tina_above_bar_required(session_factory):
    c = client(session_factory)
    r = c.post("/api/tina", json={
        "action_date": "2026-08-01",
        "proposed_value": "12000000.00",
    })
    body = r.json()
    assert body["above_threshold"] is True
    assert body["certification_required"] is True
    assert body["reasons"]


def test_tina_exception_waives(session_factory):
    c = client(session_factory)
    r = c.post("/api/tina", json={
        "action_date": "2026-08-01",
        "proposed_value": "12000000.00",
        "tina_exception_adequate_price_competition": True,
    })
    body = r.json()
    assert body["exception_applied"] == "tina_exception_adequate_price_competition"
    assert body["certification_required"] is False


def test_sf1408_and_reverify_and_about(session_factory):
    c = client(session_factory)
    # Empty migrated DB: the engine honestly refuses to imply adequacy — it
    # returns a single "no data to verify" guard rather than 6 vacuous passes.
    sf = c.get("/api/sf1408").json()
    assert sf["has_data"] is False
    assert len(sf["criteria"]) == 1
    assert sf["criteria"][0]["passed"] is False
    assert "vacuously" in " ".join(sf["criteria"][0]["findings"])

    rv = c.get("/api/reverify").json()
    assert isinstance(rv["items"], list) and rv["items"]  # non-final thresholds present

    about = c.get("/api/about").text
    assert "SYNTHETIC DATA" in about  # the limitations statement is upper-case


def test_index_serves_selfcontained_ui(session_factory):
    c = client(session_factory)
    r = c.get("/")
    assert r.status_code == 200
    html = r.text
    assert "GovCon Compliance Workbench" in html
    assert "Synthetic data" in html  # persistent advisory banner
    assert "certified accounting system" in html
    assert "fonts.googleapis" not in html  # self-contained (fonts inlined)
