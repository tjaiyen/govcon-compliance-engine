"""Phase 2 education layer: the scenario library is EXECUTABLE TRUTH — every
scenario run is posted through the real API and its pre-registered `expect`
block must hold, so the teaching content can never drift from the engine.
Plus glossary structural checks (grounded figures) and provenance exposure.
"""

import re
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from govcon.api import create_app
from govcon.education import GLOSSARY, SCENARIOS


def client(session_factory) -> TestClient:
    return TestClient(create_app(session_factory=session_factory))


# --- scenarios: self-verifying curriculum -------------------------------------


def _scenario_runs():
    for scenario in SCENARIOS:
        for run in scenario["runs"]:
            yield pytest.param(
                scenario, run, id=f"{scenario['id']}:{run['label'][:40]}"
            )


@pytest.mark.parametrize("scenario,run", list(_scenario_runs()))
def test_every_scenario_run_matches_its_preregistered_expectation(
    session_factory, scenario, run
):
    c = client(session_factory)
    response = c.post(f"/api/{run['endpoint']}", json=run["inputs"])
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    for key, expected in run["expect"].items():
        if key == "caveat_contains":
            assert any(expected in cv for cv in body["caveats"]), (
                f"no caveat contains {expected!r}: {body['caveats']}"
            )
        elif key == "reason_contains":
            assert any(expected in r for r in body["reasons"]), (
                f"no reason contains {expected!r}: {body['reasons']}"
            )
        else:
            assert body[key] == expected, (
                f"scenario {scenario['id']}: {key} = {body[key]!r}, "
                f"pre-registered {expected!r}"
            )


def test_scenario_library_structure():
    assert len(SCENARIOS) == 6  # restated so shrinkage fails loudly
    ids = [s["id"] for s in SCENARIOS]
    assert len(ids) == len(set(ids))
    glossary_terms = {g["term"] for g in GLOSSARY}
    for s in SCENARIOS:
        assert s["title"] and s["story"] and s["runs"] and s["notice"]
        for run in s["runs"]:
            assert run["endpoint"] in ("cas", "tina")
            assert run["inputs"] and run["expect"]
        # every referenced term must actually exist in the glossary
        for term in s["terms"]:
            assert term in glossary_terms, f"{s['id']} references unknown term {term!r}"


def test_scenarios_endpoint_serves_the_library(session_factory):
    c = client(session_factory)
    body = c.get("/api/scenarios").json()
    assert [s["id"] for s in body["scenarios"]] == [s["id"] for s in SCENARIOS]


# --- glossary: grounded plain language -----------------------------------------


def test_glossary_completeness_and_endpoint(session_factory):
    assert len(GLOSSARY) == 18  # restated so shrinkage fails loudly (+4 FAR 15.404, incl. cost realism)
    terms = [g["term"] for g in GLOSSARY]
    assert len(terms) == len(set(terms))
    for entry in GLOSSARY:
        for field in ("term", "plain", "why", "example"):
            assert entry[field] and entry[field].strip(), (
                f"{entry.get('term')}: empty {field}"
            )
    c = client(session_factory)
    body = c.get("/api/glossary").json()
    assert [t["term"] for t in body["terms"]] == terms


def test_glossary_dollar_figures_match_threshold_seeds(session):
    """Ground rule 2 applied to curriculum: every dollar figure quoted in a
    glossary example must exist in the seeded regulatory_thresholds (or be a
    scenario input value used against them)."""
    from govcon.models import RegulatoryThreshold

    seeded = {
        str(v)
        for v in (
            session.execute(sa.select(RegulatoryThreshold.value)).scalars().all()
        )
        if v is not None
    }
    #: input values the examples use AGAINST the bars (not thresholds themselves)
    non_threshold_inputs = {
        "8000000.00", "12000000.00", "500000000.00", "50000000.00",
        "4000000.00",
    }
    for entry in GLOSSARY:
        for figure in re.findall(r"\$([\d,]+\.\d{2})", entry["example"]):
            value = figure.replace(",", "")
            assert value in seeded or value in non_threshold_inputs, (
                f"glossary term {entry['term']!r} quotes ${figure}, which is "
                "neither a seeded threshold nor a declared example input"
            )
            Decimal(value)  # and it parses as money


# --- provenance: the Auditor persona's payload ---------------------------------


def test_determinations_expose_decision_table_provenance(session_factory):
    c = client(session_factory)
    cas = c.post("/api/cas", json={
        "award_date": "2026-07-15",
        "contract_value": "50000000.00",
        "contractor_size": "other_than_small",
    }).json()
    assert cas["provenance"]["decision_table"] == "CAS_COVERAGE"
    assert cas["provenance"]["version"] == 1
    assert cas["provenance"]["fired_rules"] == ["modified_coverage"]

    small = c.post("/api/cas", json={
        "award_date": "2026-07-15",
        "contract_value": "500000000.00",
        "contractor_size": "small",
    }).json()
    assert small["provenance"]["fired_rules"] == ["small_business_exempt"]

    tina = c.post("/api/tina", json={
        "action_date": "2026-08-01",
        "proposed_value": "12000000.00",
    }).json()
    assert tina["provenance"]["decision_table"] == "TINA_APPLICABILITY"
    assert tina["provenance"]["fired_rules"] == [
        "above_threshold", "exceptions_pending",
    ]


# --- the guided UI carries the education layer ----------------------------------


def test_index_escapes_quotes_and_guards_external_url_sink(session_factory):
    """XSS hardening (stress test): esc() must neutralize quotes (attribute
    context), and the ONE sink that renders an external, fetched URL into an
    href must go through safeUrl() (scheme allow-list), not bare esc()."""
    c = client(session_factory)
    html = c.get("/").text
    assert '.replace(/"/g,"&quot;")' in html  # esc now escapes quotes
    assert "function safeUrl(" in html
    # the suggestions href — the untrusted-URL sink — uses safeUrl, not esc
    import re
    m = re.search(r'href="[^"]*\+\s*(\w+)\(sg\.url\)', html)
    assert m and m.group(1) == "safeUrl", "external URL sink must use safeUrl()"


def test_index_has_personas_scenarios_glossary_and_tristate(session_factory):
    c = client(session_factory)
    html = c.get("/").text
    # persona selector: all five modes, ARIA-pressed toggles
    for pid in ("p-newcomer", "p-analyst", "p-controller", "p-executive", "p-auditor"):
        assert f'id="{pid}"' in html
    assert 'aria-pressed' in html
    # scenario library + glossary sections wired to the endpoints
    assert 'id="scenario-list"' in html and "/api/scenarios" in html
    assert 'id="gloss-list"' in html and "/api/glossary" in html
    # the TINA exceptions are tri-state selects now — no checkbox can express
    # "not yet evaluated", and the old bool default was an overclaim. (4 TINA
    # exceptions + the FAR 15.404-1 adequate-price-competition select = 5.)
    assert html.count('<option value="">Not evaluated</option>') == 5
    assert 'id="e-apc"' in html and "checkbox" not in html.split('id="e-apc"')[1][:200]
    # still self-contained
    assert "fonts.googleapis" not in html


def test_index_has_ask_ui_and_a11y_landmarks(session_factory):
    """PR-3: the /api/ask endpoint now has a UI; a11y landmarks are present."""
    html = client(session_factory).get("/").text
    # the Ask card is wired to /api/ask
    assert 'id="f-ask"' in html and 'id="r-ask"' in html and "/api/ask" in html
    # a11y: skip link, main landmark, non-color status glyph, print styles,
    # locale-robust money parse
    assert 'class="skip"' in html and '<main id="main"' in html
    assert ".tag.ok::before" in html and "@media print" in html
    assert "function parseMoney" in html


def test_index_has_tutor_and_drafting_ui(session_factory):
    """All four AI patterns are reachable in the workbench: ask, tutor, and the
    two drafting modes (rule + narrative)."""
    html = client(session_factory).get("/").text
    assert 'id="f-tutor"' in html and "/api/tutor" in html
    assert 'id="f-draft"' in html
    assert 'value="draft-rule"' in html and 'value="draft-narrative"' in html
    # the never-auto-apply guardrail is surfaced in the drafting card copy
    assert "human-reviewed migration" in html
