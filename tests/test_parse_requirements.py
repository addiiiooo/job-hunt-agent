"""Phase 2: the first tool.

The offline test proves the contract — schema forced, output validated, output
atomic. The live test proves the prompt actually works against the real model,
and is skipped unless a key is present so CI stays green and free.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.agent.client import FakeClient
from app.agent.tools import EMIT_REQUIREMENTS, parse_requirements
from app.config import Settings
from app.models import AgentError, Requirements
from tests.fakes import SAMPLE_REQUIREMENTS, tool_reply

SAMPLE_JD = (Path(__file__).parent / "fixtures" / "sample_jd.txt").read_text()


@pytest.fixture()
def cfg():
    return Settings(anthropic_api_key="sk-test", db_path=":memory:")


def test_parses_sample_jd_into_at_least_five_requirements(cfg):
    client = FakeClient([tool_reply("emit_requirements", SAMPLE_REQUIREMENTS)])
    reqs, in_tok, out_tok = parse_requirements(SAMPLE_JD, client=client, settings=cfg)

    assert isinstance(reqs, Requirements)
    assert len(reqs.requirements) >= 5
    assert reqs.role_title
    assert (in_tok, out_tok) == (120, 60)


def test_output_separates_hard_from_preferred(cfg):
    client = FakeClient([tool_reply("emit_requirements", SAMPLE_REQUIREMENTS)])
    reqs, _, _ = parse_requirements(SAMPLE_JD, client=client, settings=cfg)
    assert len(reqs.hard()) >= 5
    assert any(r.kind == "preferred" for r in reqs.requirements)
    assert any(r.years == 3 for r in reqs.requirements)


def test_schema_is_forced_as_a_real_tool_definition(cfg):
    client = FakeClient([tool_reply("emit_requirements", SAMPLE_REQUIREMENTS)])
    parse_requirements(SAMPLE_JD, client=client, settings=cfg)
    sent = client.calls[0]
    assert sent["tool_choice"] == {"type": "tool", "name": "emit_requirements"}
    assert sent["tools"][0]["input_schema"]["properties"]["requirements"]["type"] == "array"
    assert sent["timeout"] == cfg.tool_timeout_s


def test_missing_tool_call_is_a_schema_validation_error(cfg):
    from app.agent.client import LLMReply

    client = FakeClient([LLMReply(text="I'd rather chat", stop_reason="end_turn")])
    with pytest.raises(AgentError) as exc:
        parse_requirements(SAMPLE_JD, client=client, settings=cfg)
    assert exc.value.kind.value == "schema_validation"


def test_empty_jd_is_rejected_before_any_model_call(cfg):
    client = FakeClient([])
    with pytest.raises(AgentError):
        parse_requirements("   ", client=client, settings=cfg)
    assert client.calls == []


def test_emitted_schema_is_self_contained():
    import json

    blob = json.dumps(EMIT_REQUIREMENTS["input_schema"])
    assert "$ref" not in blob and "$defs" not in blob


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="needs ANTHROPIC_API_KEY; run with -m live",
)
def test_live_parse_against_real_api():
    from app.agent.client import AnthropicClient
    from app.config import get_settings

    cfg = get_settings()
    client = AnthropicClient(cfg.require_api_key(), cfg.model)
    reqs, _, _ = parse_requirements(SAMPLE_JD, client=client, settings=cfg)
    assert len(reqs.requirements) >= 5
    assert len(reqs.hard()) >= 3
    assert any(r.years == 3 for r in reqs.requirements), "3+ years should normalise to 3"
