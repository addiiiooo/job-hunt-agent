"""Phase 1: the app boots, config comes only from the environment, and /health
reports each dependency rather than a bare 200."""

from __future__ import annotations

import pytest

from app.config import Settings


def test_settings_read_only_from_env():
    s = Settings.from_env({"MAX_STEPS": "4", "TOOL_TIMEOUT_S": "2.5", "LOG_LEVEL": "debug"})
    assert (s.max_steps, s.tool_timeout_s, s.log_level) == (4, 2.5, "DEBUG")
    assert s.api_key_present is False


def test_settings_defaults():
    s = Settings.from_env({})
    assert s.max_steps == 12
    assert s.tool_timeout_s == 30.0
    assert s.db_path == "runs.db"


def test_missing_api_key_raises_only_when_used():
    s = Settings.from_env({})
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        s.require_api_key()


def test_health_reports_each_dependency(client):
    body = client.get("/health").json()
    assert set(body["checks"]) == {"sqlite", "corpus", "anthropic_api_key"}
    assert body["checks"]["sqlite"]["status"] == "ok"
    assert body["checks"]["anthropic_api_key"]["status"] == "ok"
    assert "status" in body and "model" in body


def test_index_page_serves_the_form(client):
    body = client.get("/").text
    assert client.get("/").status_code == 200
    assert 'name="jd_text"' in body and 'action="/analyse"' in body


def test_run_store_roundtrip(tmp_path):
    from app.db import RunStore
    from app.models import AgentResult, MatchReport

    store = RunStore(str(tmp_path / "r.db"))
    assert store.ping()[0] is True
    result = AgentResult(report=MatchReport(summary="s"), total_tokens=42)
    rid = store.save_run("req-1", "jd", result, role_title="SDE-1")
    loaded = store.load_run(rid)
    assert loaded["role_title"] == "SDE-1"
    assert loaded["result"].total_tokens == 42
    assert store.count() == 1
