"""Browser-test harness (Playwright). Skipped entirely unless the `frontend`
extra is installed (`uv sync --extra frontend --extra ai` + `playwright install
chromium`). Runs a real uvicorn server in a background thread with a migrated
temp DB and a REUSABLE fake LLM (grounded answer for any question), so the
Ask flow is deterministic in the browser.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright")  # skip the whole dir without the extra
pytest.importorskip("uvicorn")

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _ReusableFakeLLM:
    """Stateless across questions: returns a tool_use turn until it sees a
    tool_result in the transcript, then a grounded final answer. So it can
    serve many browser questions from one instance."""

    def create(self, *, system, messages, tools, max_tokens=4096):
        from govcon.ai.client import LLMResponse, ToolUse

        saw_result = any(
            isinstance(m.get("content"), list)
            and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"])
            for m in messages
        )
        if not saw_result:
            return LLMResponse(
                text="",
                tool_uses=[ToolUse(id="t1", name="determine_cas_coverage", input={
                    "award_date": "2026-05-15", "contract_value": "12000000.00",
                    "contractor_size": "other_than_small"})],
                stop_reason="tool_use", input_tokens=50, output_tokens=10,
            )
        return LLMResponse(
            text="This $12,000,000.00 award has modified CAS coverage.",
            stop_reason="end_turn", input_tokens=50, output_tokens=20,
        )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    import uvicorn

    db = tmp_path_factory.mktemp("fe") / "fe.db"
    r = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "GOVCON_DB_URL": f"sqlite:///{db}", "GOVCON_DATA_MODE": "synthetic"},
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr

    os.environ["GOVCON_DATA_MODE"] = "synthetic"
    from govcon.api import create_app
    from govcon.db.engine import make_engine, make_session_factory

    factory = make_session_factory(make_engine(f"sqlite:///{db}"))
    app = create_app(session_factory=factory, llm_client=_ReusableFakeLLM())

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)
