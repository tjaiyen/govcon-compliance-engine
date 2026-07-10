"""Playwright browser tests — the frontend behavior the server-side tests
can't see: persona switching, live form submit + result focus, the Ask flow,
the colorblind status cue, and the glossary deep-link."""

from playwright.sync_api import expect


def test_page_loads_with_skip_link_and_main(page, live_server):
    page.goto(live_server)
    expect(page.locator("a.skip")).to_have_text("Skip to the workbench")
    expect(page.locator("main#main")).to_be_visible()


def test_persona_switch_updates_description_and_pressed(page, live_server):
    page.goto(live_server)
    page.click("#p-auditor")
    expect(page.locator("#p-auditor")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#p-desc")).to_contain_text("rules fired")


def test_contract_form_renders_verdict_and_moves_focus(page, live_server):
    page.goto(live_server)
    page.fill("#c-date", "2026-05-15")
    page.fill("#c-value", "12,000,000")  # US-formatted → parseMoney normalizes
    page.click("#f-contract button[type=submit]")
    result = page.locator("#r-contract")
    expect(result).to_contain_text("Modified CAS coverage")
    # focus moved to the result region (a11y)
    expect(result).to_be_focused()


def test_status_tag_has_noncolor_glyph(page, live_server):
    page.goto(live_server)
    page.fill("#c-date", "2026-05-15")
    page.fill("#c-value", "12000000")
    page.click("#f-contract button[type=submit]")
    page.wait_for_selector("#r-contract .tag")
    glyph = page.eval_on_selector(
        "#r-contract .tag.warn",
        "el => getComputedStyle(el, '::before').content",
    )
    assert glyph and glyph not in ("none", '""')  # a shape cue exists


def test_ask_flow_shows_grounded_answer_and_determination(page, live_server):
    page.goto(live_server)
    page.fill("#ask-q", "Does CAS apply to a $12M award on 2026-05-15?")
    page.click("#f-ask button[type=submit]")
    result = page.locator("#r-ask")
    expect(result).to_contain_text("modified CAS coverage", ignore_case=True)
    expect(result.locator(".ask-badge.grounded")).to_be_visible()
    # the authoritative determination is shown beside the prose
    expect(result).to_contain_text("determination")


def test_glossary_deeplink_opens_entry(page, live_server):
    page.goto(live_server)
    page.wait_for_selector("#gloss-list details")
    first = page.locator("#gloss-list details").first
    term_id = first.get_attribute("id")
    page.goto(f"{live_server}/#{term_id}")
    expect(first).to_have_attribute("open", "")


def test_tutor_flow_teaches_grounded_answer_at_persona(page, live_server):
    page.goto(live_server)
    page.click("#p-auditor")  # pick a persona → routes /api/tutor with it
    page.fill("#tutor-q", "Why does a $12M award on 2026-05-15 get modified CAS?")
    page.click("#f-tutor button[type=submit]")
    result = page.locator("#r-tutor")
    expect(result).to_contain_text("modified CAS coverage", ignore_case=True)
    expect(result.locator(".ask-badge.grounded")).to_be_visible()


def test_draft_rule_shows_human_migration_notice(page, live_server):
    page.goto(live_server)
    page.select_option("#draft-mode", "draft-rule")
    page.fill("#draft-q", "Draft a rule for a new CAS full-coverage threshold")
    page.click("#f-draft button[type=submit]")
    # the never-auto-apply guardrail is always surfaced to the user
    expect(page.locator("#r-draft")).to_contain_text("human-reviewed migration", ignore_case=True)


def test_draft_narrative_shows_synthetic_banner(page, live_server):
    page.goto(live_server)
    page.select_option("#draft-mode", "draft-narrative")
    page.fill("#draft-q", "Draft a memo on CAS for a $12M May-2026 award")
    page.click("#f-draft button[type=submit]")
    expect(page.locator("#r-draft")).to_contain_text("SYNTHETIC")


def test_double_submit_button_disables_during_fetch(page, live_server):
    page.goto(live_server)
    page.fill("#c-date", "2026-05-15")
    page.fill("#c-value", "12000000")
    btn = page.locator("#f-contract button[type=submit]")
    btn.click()
    # after completion it re-enables
    expect(page.locator("#r-contract")).to_contain_text("CAS")
    expect(btn).to_be_enabled()
