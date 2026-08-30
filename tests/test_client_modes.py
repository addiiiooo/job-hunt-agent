"""§3 — the client is selected by configuration, and both modes work.

Scripted mode is not a test fixture bolted into production; it is a supported
runtime mode, so it gets the same treatment as the real one."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agent.client import ScriptedClient, classify_error, is_transient
from app.config import Settings


class _Status(Exception):
    def __init__(self, code: int):
        super().__init__(f"http {code}")
        self.status_code = code


# -- selector ---------------------------------------------------------------


def test_llm_client_defaults_to_anthropic():
    assert Settings.from_env({}).llm_client == "anthropic"
    assert Settings.from_env({}).api_key_required is True


def test_llm_client_can_be_switched_to_scripted():
    s = Settings.from_env({"LLM_CLIENT": "scripted"})
    assert s.llm_client == "scripted"
    assert s.api_key_required is False


def test_an_unknown_llm_client_is_rejected_at_config_time():
    with pytest.raises(ValueError):
        Settings.from_env({"LLM_CLIENT": "openai"})


def test_scripted_client_is_the_class_tests_inject():
    c = ScriptedClient([])
    assert hasattr(c, "complete") and c.calls == []


# -- retry scope ------------------------------------------------------------


def test_retries_cover_429_and_5xx():
    assert is_transient(_Status(429)) is True
    assert is_transient(_Status(500)) is True
    assert is_transient(_Status(503)) is True


@pytest.mark.parametrize("code", [400, 401, 403, 404, 409, 422])
def test_other_4xx_are_never_retried(code):
    assert is_transient(_Status(code)) is False


def test_a_status_code_beats_the_class_name():
    """A class called RateLimitError carrying a 400 is still not retried."""

    class RateLimitError(_Status):
        pass

    assert is_transient(RateLimitError(400)) is False


def test_schema_validation_is_not_transient():
    from pydantic import ValidationError

    from app.models import Requirements

    try:
        Requirements.model_validate({"role_title": 1, "requirements": "nope"})
    except ValidationError as exc:
        assert is_transient(exc) is False
        assert classify_error(exc).value == "schema_validation"
    else:  # pragma: no cover
        pytest.fail("expected a ValidationError")


# -- scripted mode as a served runtime --------------------------------------


@pytest.fixture()
def demo_client(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setenv("LLM_CLIENT", "scripted")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "runs.db"))
    config.reset_settings_cache()
    from app.main import app

    with TestClient(app) as c:
        yield c
    config.reset_settings_cache()


def test_scripted_mode_serves_without_an_api_key(demo_client):
    body = demo_client.get("/health").json()
    assert body["llm_client"] == "scripted"
    assert body["checks"]["anthropic_api_key"]["status"] == "ok"
    assert "not required" in body["checks"]["anthropic_api_key"]["detail"]


def test_scripted_mode_runs_the_whole_agent(demo_client):
    body = demo_client.post("/api/analyse", json={"jd_text": "Backend Engineer. Python."}).json()

    assert body["terminated_reason"] == "complete"
    assert len(body["trace"]) >= 4
    assert [s["tool"] for s in body["trace"]][-1] == "finish"
    assert body["total_tokens"] > 0
    assert body["report"]["hard_total"] > 0


def test_scripted_mode_still_enforces_the_guardrail(demo_client):
    body = demo_client.post("/api/analyse", json={"jd_text": "Backend Engineer."}).json()
    overrides = [s for s in body["suggestions"] if s["refusal_source"] == "validator"]
    assert overrides, "the demo script includes an ungrounded draft on purpose"


def test_the_page_says_when_the_model_output_is_canned(demo_client):
    html = demo_client.post("/analyse", data={"jd_text": "Backend Engineer."}).text
    assert "Demo mode" in html
    assert "LLM_CLIENT=scripted" in html


def test_each_run_gets_a_fresh_script(demo_client):
    """The scripted client consumes its script, so a second run must not 404."""
    first = demo_client.post("/api/analyse", json={"jd_text": "Backend Engineer."})
    second = demo_client.post("/api/analyse", json={"jd_text": "Backend Engineer."})
    assert first.status_code == second.status_code == 200
    assert second.json()["terminated_reason"] == "complete"
