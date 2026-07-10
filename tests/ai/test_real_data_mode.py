"""Local-only real-data mode. GOVCON_DATA_MODE=real routes to a LOCAL model
(Ollama); the gate REFUSES real data to any non-local (cloud/absent) client, so
real data can never leave the machine. The tool stays advisory / not-certified.
The Ollama adapter's conversion + parsing are tested without Ollama running.
"""

import pytest
from fastapi.testclient import TestClient

from govcon.ai.client import (
    LocalClient,
    _parse_ollama,
    _to_ollama_messages,
    _to_ollama_tools,
    build_llm_client,
)
from govcon.ai.errors import SyntheticGateError
from govcon.ai.gate import assert_data_mode, is_real
from govcon.api import create_app
from tests.ai.conftest import FakeLLMClient, final_turn, tool_turn


# ------------------------------------------------ the gate: real → LOCAL only
def test_synthetic_mode_allows_any_client(monkeypatch):
    monkeypatch.setenv("GOVCON_DATA_MODE", "synthetic")
    assert_data_mode(FakeLLMClient([]))  # cloud/fake fine on synthetic data
    assert_data_mode(None)


def test_real_mode_requires_local_client(monkeypatch):
    monkeypatch.setenv("GOVCON_DATA_MODE", "real")
    assert is_real()
    with pytest.raises(SyntheticGateError):  # a cloud/fake client is refused
        assert_data_mode(FakeLLMClient([]))
    with pytest.raises(SyntheticGateError):  # an absent client is refused
        assert_data_mode(None)
    assert_data_mode(LocalClient())  # a LOCAL client is allowed — no raise


def test_unknown_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("GOVCON_DATA_MODE", "production")
    with pytest.raises(SyntheticGateError):
        assert_data_mode(LocalClient())


# ------------------------------------------------ build_llm_client picks by mode
def test_build_client_real_mode_is_local(monkeypatch):
    monkeypatch.setenv("GOVCON_DATA_MODE", "real")
    assert getattr(build_llm_client(), "is_local", False) is True


def test_build_client_synthetic_no_key_is_none(monkeypatch):
    monkeypatch.setenv("GOVCON_DATA_MODE", "synthetic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert build_llm_client() is None


def test_build_client_unknown_mode_is_none(monkeypatch):
    monkeypatch.setenv("GOVCON_DATA_MODE", "production")
    assert build_llm_client() is None


# ------------------------------------------ Ollama adapter (no Ollama running)
def test_tools_convert_to_ollama_functions():
    out = _to_ollama_tools([{"name": "t", "description": "d", "input_schema": {"type": "object"}}])
    assert out == [{"type": "function",
                    "function": {"name": "t", "description": "d", "parameters": {"type": "object"}}}]


def test_messages_convert_tool_use_and_result():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "id": "t1", "name": "cas", "input": {"x": 1}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": '{"tier":"modified"}',
             "is_error": False}]},
    ]
    out = _to_ollama_messages(msgs)
    assert out[0] == {"role": "user", "content": "hi"}
    assert out[1]["role"] == "assistant"
    assert out[1]["tool_calls"][0]["function"] == {"name": "cas", "arguments": {"x": 1}}
    assert out[2] == {"role": "tool", "content": '{"tier":"modified"}'}


def test_parse_ollama_maps_tool_calls_and_tokens():
    data = {"message": {"content": "",
                        "tool_calls": [{"function": {"name": "cas", "arguments": {"a": 1}}}]},
            "prompt_eval_count": 5, "eval_count": 3}
    r = _parse_ollama(data, "llama3.1")
    assert r.stop_reason == "tool_use"
    assert r.tool_uses[0].name == "cas" and r.tool_uses[0].input == {"a": 1}
    assert r.input_tokens == 5 and r.output_tokens == 3


def test_parse_ollama_string_arguments():
    data = {"message": {"tool_calls": [{"function": {"name": "cas", "arguments": '{"a": 2}'}}]}}
    assert _parse_ollama(data, "m").tool_uses[0].input == {"a": 2}


def test_parse_ollama_plain_text():
    r = _parse_ollama({"message": {"content": "modified CAS coverage"}, "eval_count": 4}, "m")
    assert r.text == "modified CAS coverage" and r.stop_reason == "end_turn" and not r.tool_uses


def test_local_client_create_roundtrip_and_is_local():
    # stub the HTTP layer → the full create() mapping is exercised without Ollama
    class _Stub(LocalClient):
        def _post(self, path, payload):
            self.seen = payload
            return {"message": {"content": "modified CAS coverage"}, "eval_count": 4}

    c = _Stub()
    assert c.is_local is True
    resp = c.create(system="sys", messages=[{"role": "user", "content": "q"}],
                    tools=[{"name": "cas", "input_schema": {}}])
    assert resp.text == "modified CAS coverage" and resp.stop_reason == "end_turn"
    assert c.seen["messages"][0] == {"role": "system", "content": "sys"}  # system prepended
    assert c.seen["tools"][0]["type"] == "function"  # tools converted
    assert c.seen["stream"] is False


# ------------------------------------------ endpoint: real → local works, cloud refused
class _LocalFake(FakeLLMClient):
    is_local = True


def test_endpoint_real_mode_works_with_local_client(session_factory, monkeypatch):
    monkeypatch.setenv("GOVCON_DATA_MODE", "real")
    fake = _LocalFake([
        tool_turn(("t1", "determine_cas_coverage",
                   {"award_date": "2026-05-15", "contract_value": "12000000.00",
                    "contractor_size": "other_than_small"})),
        final_turn("This $12,000,000.00 award has modified CAS coverage."),
    ])
    c = TestClient(create_app(session_factory=session_factory, llm_client=fake))
    body = c.post("/api/ask", json={"question": "..."}).json()
    assert body["ai_available"] is True
    assert body["determinations"][0]["result"]["tier"] == "modified"


def test_endpoint_real_mode_refuses_cloud_client(session_factory, monkeypatch):
    monkeypatch.setenv("GOVCON_DATA_MODE", "real")
    fake = FakeLLMClient([final_turn("should never run")])  # NOT local
    c = TestClient(create_app(session_factory=session_factory, llm_client=fake))
    body = c.post("/api/ask", json={"question": "..."}).json()
    assert body["ai_available"] is False  # gate refused → real data never sent
    assert fake.calls == []  # the cloud client was never called


# ------------------------------------------ honesty: /api/about tracks the mode
def test_about_states_real_data_mode(session_factory, monkeypatch):
    monkeypatch.setenv("GOVCON_DATA_MODE", "real")
    about = TestClient(create_app(session_factory=session_factory)).get("/api/about").text
    flat = " ".join(about.split())  # collapse the wrapped-text line breaks
    assert "REAL-DATA MODE (LOCAL ONLY)" in flat
    assert "never sent to an external service" in flat
    assert "NOT a certified accounting system" in flat
    assert "SYNTHETIC DATA ONLY" not in flat  # the synthetic claim is gone in real mode


def test_response_notice_reflects_real_mode(session_factory, monkeypatch):
    # the per-answer notice must NOT claim "synthetic" when running on real data
    monkeypatch.setenv("GOVCON_DATA_MODE", "real")
    fake = _LocalFake([
        tool_turn(("t1", "determine_cas_coverage",
                   {"award_date": "2026-05-15", "contract_value": "12000000.00",
                    "contractor_size": "other_than_small"})),
        final_turn("This $12,000,000.00 award has modified CAS coverage."),
    ])
    c = TestClient(create_app(session_factory=session_factory, llm_client=fake))
    body = c.post("/api/ask", json={"question": "..."}).json()
    assert "real data (local model)" in body["notice"]
    assert "synthetic" not in body["notice"].lower()


def test_system_prompts_reflect_live_data_mode(monkeypatch):
    # stress-test finding: ask/draft-rule/draft-narrative prompts were frozen at
    # module import → could tell the model the WRONG data mode. Now per-call.
    from govcon.ai.prompts import system_for

    monkeypatch.setenv("GOVCON_DATA_MODE", "real")
    for pattern in ("ask", "tutor", "draft_rule", "draft_narrative"):
        sys = system_for(pattern)
        assert "REAL-DATA MODE" in sys, pattern
        assert "SYNTHETIC DATA ONLY" not in sys, pattern
    monkeypatch.setenv("GOVCON_DATA_MODE", "synthetic")
    assert "SYNTHETIC DATA ONLY" in system_for("ask")
    assert "REAL-DATA MODE" not in system_for("draft_narrative")


def test_draft_rule_notice_reflects_real_mode(session_factory, monkeypatch):
    # the draft-rule notice hardcoded "Synthetic data." — missed by the earlier fix
    monkeypatch.setenv("GOVCON_DATA_MODE", "real")
    fake = _LocalFake([final_turn("draft only; requires a human migration")])
    c = TestClient(create_app(session_factory=session_factory, llm_client=fake))
    body = c.post("/api/draft-rule", json={"instruction": "..."}).json()
    assert "Real data (local model)." in body["notice"]
    assert "Synthetic data" not in body["notice"]


def test_narrative_banner_reflects_real_mode(session_factory, monkeypatch):
    monkeypatch.setenv("GOVCON_DATA_MODE", "real")
    fake = _LocalFake([
        tool_turn(("t1", "determine_cas_coverage",
                   {"award_date": "2026-05-15", "contract_value": "12000000.00",
                    "contractor_size": "other_than_small"})),
        final_turn("MEMO: This $12,000,000.00 award has modified CAS coverage."),
    ])
    c = TestClient(create_app(session_factory=session_factory, llm_client=fake))
    body = c.post("/api/draft-narrative", json={"instruction": "..."}).json()
    assert "REAL DATA (LOCAL MODEL)" in body["synthetic_banner"]
    assert "NOT FOR FILING" in body["synthetic_banner"]


def test_health_reports_data_mode(session_factory, monkeypatch):
    monkeypatch.setenv("GOVCON_DATA_MODE", "real")
    body = TestClient(create_app(session_factory=session_factory)).get("/health").json()
    assert body["data_mode"] == "real"


def test_index_banner_swaps_on_real_mode(session_factory):
    # the UI reads /health and turns the banner red in real mode (honesty in the UI)
    html = TestClient(create_app(session_factory=session_factory)).get("/").text
    assert 'id="banner-mode"' in html and 'id="banner"' in html
    assert '"/health"' in html and 'data_mode === "real"' in html
    assert ".banner.real" in html  # the loud real-data style exists
