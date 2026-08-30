"""Phase 4: the loop.

These tests are the reason the Claude client is an injected protocol. Every one
of them runs offline, in milliseconds, and asserts on a real trace."""

from __future__ import annotations

import pytest

from app.agent import loop as loop_mod
from app.agent.client import FakeClient, LLMReply
from app.agent.loop import call_with_policy, run
from app.config import Settings
from app.corpus.index import CorpusIndex
from app.models import ErrorKind
from tests.fakes import (
    SAMPLE_REQUIREMENTS,
    APITimeoutError,
    Boom,
    RateLimitError,
    SlowClient,
    planner_reply,
    tool_reply,
)

JD = "Backend Engineer. Requirements: 3+ years Python. PostgreSQL. Docker."


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch):
    monkeypatch.setattr(loop_mod, "BACKOFF_BASE_S", 0.0)


@pytest.fixture()
def index():
    from pathlib import Path

    return CorpusIndex.from_directory(
        Path(__file__).resolve().parents[1] / "app" / "corpus" / "documents"
    )


def cfg(**over):
    base = {
        "anthropic_api_key": "sk-test",
        "db_path": ":memory:",
        "max_steps": 12,
        "tool_timeout_s": 5.0,
    }
    base.update(over)
    return Settings(**base)


# -- happy path -------------------------------------------------------------


def test_happy_path_produces_a_trace_of_real_steps(index):
    client = FakeClient(
        [
            planner_reply("parse_requirements", why="need the requirements before anything else"),
            tool_reply("emit_requirements", SAMPLE_REQUIREMENTS),
            planner_reply("retrieve_evidence", why="check the corpus for Python",
                          requirement="Strong Python", k=3),
            planner_reply("finish", why="nothing further would improve the answer"),
        ]
    )
    result = run(JD, client=client, index=index, settings=cfg())

    assert [s.tool for s in result.trace] == [
        "parse_requirements",
        "retrieve_evidence",
        "finish",
    ]
    assert result.terminated_reason == "complete"
    assert all(s.error is None for s in result.trace)
    assert result.total_tokens > 0
    assert result.total_latency_ms >= 0
    assert result.trace[0].index == 1 and result.trace[-1].index == 3


def test_trace_carries_the_planner_rationale(index):
    client = FakeClient(
        [
            planner_reply("parse_requirements", why="the JD has not been parsed yet"),
            tool_reply("emit_requirements", SAMPLE_REQUIREMENTS),
            planner_reply("finish", why="requirements are enough for this test"),
        ]
    )
    result = run(JD, client=client, index=index, settings=cfg())
    assert result.trace[0].rationale == "the JD has not been parsed yet"
    assert result.trace[-1].rationale == "requirements are enough for this test"


def test_call_order_is_the_planner_s_choice_not_hardcoded(index):
    # The planner retrieves *before* parsing. The loop must obey.
    client = FakeClient(
        [
            planner_reply("retrieve_evidence", requirement="PostgreSQL indexing", k=2),
            planner_reply("parse_requirements"),
            tool_reply("emit_requirements", SAMPLE_REQUIREMENTS),
            planner_reply("finish"),
        ]
    )
    result = run(JD, client=client, index=index, settings=cfg())
    assert [s.tool for s in result.trace][:2] == ["retrieve_evidence", "parse_requirements"]


def test_retrieval_finding_nothing_is_a_result_not_an_error(index):
    client = FakeClient(
        [
            planner_reply("retrieve_evidence", requirement="underwater basket weaving"),
            planner_reply("finish"),
        ]
    )
    result = run(JD, client=client, index=index, settings=cfg())
    assert result.trace[0].error is None
    assert "no matching passages" in result.trace[0].result_summary


# -- termination ------------------------------------------------------------


def test_loop_terminates_at_max_steps(index):
    # The planner never says finish. Retrieval makes no model call, so each
    # scripted reply is exactly one loop iteration.
    client = FakeClient([planner_reply("retrieve_evidence", requirement="Python") for _ in range(20)])
    result = run(JD, client=client, index=index, settings=cfg(max_steps=3))

    assert result.terminated_reason == "max_steps"
    assert len(result.trace) == 3
    assert result.report.verdicts == []
    assert "step budget" in result.report.summary


def test_max_steps_of_one_still_returns_a_well_formed_result(index):
    client = FakeClient([planner_reply("retrieve_evidence", requirement="Python") for _ in range(5)])
    result = run(JD, client=client, index=index, settings=cfg(max_steps=1))
    assert len(result.trace) == 1
    assert result.terminated_reason == "max_steps"


def test_repeated_planner_failure_terminates_with_error(index):
    client = FakeClient([Boom("planner exploded"), Boom("planner exploded again")])
    result = run(JD, client=client, index=index, settings=cfg(max_steps=8))
    assert result.terminated_reason == "error"
    assert [s.tool for s in result.trace] == ["planner", "planner"]
    assert all(s.error == "tool_execution" for s in result.trace)


# -- failure handling -------------------------------------------------------


def test_tool_failure_is_recorded_as_a_step_and_the_loop_continues(index):
    client = FakeClient(
        [
            planner_reply("retrieve_evidence", requirement="   ", why="malformed on purpose"),
            planner_reply("parse_requirements", why="recovering after the failed retrieval"),
            tool_reply("emit_requirements", SAMPLE_REQUIREMENTS),
            planner_reply("finish"),
        ]
    )
    result = run(JD, client=client, index=index, settings=cfg())

    assert result.trace[0].error == ErrorKind.TOOL_EXECUTION.value
    assert result.trace[1].tool == "parse_requirements" and result.trace[1].error is None
    assert result.terminated_reason == "complete"


def test_unknown_tool_is_recorded_rather_than_raised(index):
    client = FakeClient([planner_reply("teleport"), planner_reply("finish")])
    result = run(JD, client=client, index=index, settings=cfg())
    assert result.trace[0].error == ErrorKind.TOOL_EXECUTION.value
    assert "no such tool" in result.trace[0].result_summary
    assert result.terminated_reason == "complete"


def test_empty_corpus_surfaces_as_corpus_empty(index):
    client = FakeClient([planner_reply("retrieve_evidence", requirement="Python"), planner_reply("finish")])
    result = run(JD, client=client, index=CorpusIndex([]), settings=cfg())
    assert result.trace[0].error == ErrorKind.CORPUS_EMPTY.value


# -- error policy -----------------------------------------------------------


def test_transient_error_is_retried_twice_then_succeeds():
    attempts = {"n": 0}

    def flaky(_note):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise RateLimitError("slow down")
        return "ok"

    assert call_with_policy(flaky, cfg(), repairable=False, sleep=lambda _s: None) == "ok"
    assert attempts["n"] == 3  # original + two retries


def test_transient_error_gives_up_after_two_retries():
    attempts = {"n": 0}

    def always_flaky(_note):
        attempts["n"] += 1
        raise RateLimitError("still rate limited")

    with pytest.raises(RateLimitError):
        call_with_policy(always_flaky, cfg(), repairable=False, sleep=lambda _s: None)
    assert attempts["n"] == 3


def test_backoff_is_exponential(monkeypatch):
    # This one test wants the real backoff constant, not the zeroed fixture.
    monkeypatch.setattr(loop_mod, "BACKOFF_BASE_S", 0.5)
    slept: list[float] = []

    def always(_note):
        raise APITimeoutError("nope")

    with pytest.raises(APITimeoutError):
        call_with_policy(always, cfg(), repairable=False, sleep=slept.append)
    assert slept == [0.5, 1.0]


def test_timeout_is_enforced_and_classified(index):
    client = SlowClient(delay=3.0)
    result = run(JD, client=client, index=index, settings=cfg(max_steps=1, tool_timeout_s=0.2))
    assert result.trace[0].error == ErrorKind.UPSTREAM_TIMEOUT.value


def test_schema_failure_is_repaired_exactly_once(index):
    bad = dict(SAMPLE_REQUIREMENTS)
    bad = {**bad, "requirements": [{"text": "x", "kind": "mandatory", "category": "language"}]}

    client = FakeClient(
        [
            planner_reply("parse_requirements"),
            tool_reply("emit_requirements", bad),           # invalid -> repair
            tool_reply("emit_requirements", SAMPLE_REQUIREMENTS),  # repaired
            planner_reply("finish"),
        ]
    )
    result = run(JD, client=client, index=index, settings=cfg())

    assert result.trace[0].error is None, "the repaired call should succeed"
    parse_calls = [c for c in client.calls if c.get("tool_choice", {}).get("name") == "emit_requirements"]
    assert len(parse_calls) == 2
    assert "did not satisfy the required schema" in parse_calls[1]["messages"][0]["content"]


def test_schema_failure_twice_is_recorded_as_a_failed_step(index):
    bad = {"role_title": "x", "requirements": [{"text": "x", "kind": "mandatory", "category": "language"}]}
    client = FakeClient(
        [
            planner_reply("parse_requirements"),
            tool_reply("emit_requirements", bad),
            tool_reply("emit_requirements", bad),
            planner_reply("finish", why="parsing will not succeed"),
        ]
    )
    result = run(JD, client=client, index=index, settings=cfg())

    assert result.trace[0].error == ErrorKind.SCHEMA_VALIDATION.value
    assert result.terminated_reason == "complete"


def test_planner_gets_a_tool_call_or_we_call_it_a_schema_error(index):
    client = FakeClient([LLMReply(text="I'd rather not", stop_reason="end_turn"), planner_reply("finish")])
    result = run(JD, client=client, index=index, settings=cfg())
    assert result.trace[0].tool == "planner"
    assert result.trace[0].error == ErrorKind.SCHEMA_VALIDATION.value


def test_planner_receives_the_trace_so_far(index):
    client = FakeClient(
        [
            planner_reply("retrieve_evidence", requirement="Docker"),
            planner_reply("finish"),
        ]
    )
    run(JD, client=client, index=index, settings=cfg())
    second_planner_prompt = client.calls[1]["messages"][0]["content"]
    assert "1. retrieve_evidence" in second_planner_prompt
    assert "<trace steps=\"1\">" in second_planner_prompt


def test_planner_tool_schemas_carry_a_why_and_a_finish_option(index):
    client = FakeClient([planner_reply("finish")])
    run(JD, client=client, index=index, settings=cfg())
    tools = client.calls[0]["tools"]
    names = [t["name"] for t in tools]
    assert "finish" in names and "parse_requirements" in names
    parse = next(t for t in tools if t["name"] == "parse_requirements")
    assert "why" in parse["input_schema"]["properties"]
    assert "why" in parse["input_schema"]["required"]


def test_why_is_stripped_before_reaching_the_tool(index):
    client = FakeClient(
        [
            planner_reply("retrieve_evidence", why="looking for Python", requirement="Python", k=2),
            planner_reply("finish"),
        ]
    )
    result = run(JD, client=client, index=index, settings=cfg())
    assert "why" not in result.trace[0].args
    assert result.trace[0].args == {"requirement": "Python", "k": 2}
