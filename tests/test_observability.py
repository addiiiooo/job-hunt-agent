"""Phase 6: logs, metrics and the error taxonomy.

Observability is only real if it is asserted. These tests check that a request
id threads all the way through, that /metrics is non-zero after a run, and that
every failure lands in exactly one taxonomy bucket."""

from __future__ import annotations

import json
import logging

from app.models import ErrorKind
from app.obs.logging import JsonFormatter, configure_logging, log_event, request_id_var
from app.obs.metrics import Metrics, _percentile
from tests.fakes import full_run_script, planner_reply

# -- structured logging -----------------------------------------------------


def _render(record_kwargs: dict) -> dict:
    logger = logging.getLogger("jobhunt.test")
    record = logger.makeRecord("jobhunt", logging.INFO, __file__, 1, "msg", (), None)
    for k, v in record_kwargs.items():
        setattr(record, k, v)
    return json.loads(JsonFormatter().format(record))


def test_every_line_is_one_json_object_with_a_request_id():
    token = request_id_var.set("abc123")
    try:
        line = _render({"event": "step_ok", "tool": "retrieve_evidence", "latency_ms": 12})
    finally:
        request_id_var.reset(token)

    assert line["request_id"] == "abc123"
    assert line["event"] == "step_ok"
    assert line["tool"] == "retrieve_evidence" and line["latency_ms"] == 12
    assert {"ts", "level", "logger"}.issubset(line)


def test_request_id_defaults_when_outside_a_request():
    assert _render({"event": "startup"})["request_id"] == "-"


def test_log_event_emits_a_single_parseable_line(capsys):
    configure_logging("INFO")
    log_event("run_complete", steps=7, terminated_reason="complete")
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    parsed = json.loads(out[0])
    assert parsed["event"] == "run_complete" and parsed["steps"] == 7


# -- metrics ----------------------------------------------------------------


def test_percentile_is_nearest_rank():
    assert _percentile([], 95) == 0.0
    assert _percentile([10], 95) == 10
    assert _percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5
    assert _percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10


def test_metrics_start_empty():
    snap = Metrics().snapshot()
    assert snap["runs"]["total"] == 0
    assert snap["tokens"]["total"] == 0
    assert set(snap["errors"]) == {k.value for k in ErrorKind}


def test_every_taxonomy_member_has_a_counter_slot():
    snap = Metrics().snapshot()
    assert len(snap["errors"]) == 6
    assert all(v == 0 for v in snap["errors"].values())


def test_max_steps_rate_is_computed_over_the_recent_window():
    from app.models import AgentResult, MatchReport

    m = Metrics()
    for reason in ["complete"] * 9 + ["max_steps"]:
        m.observe_run(
            AgentResult(report=MatchReport(), terminated_reason=reason, total_latency_ms=10)
        )
    assert m.max_steps_rate_recent() == 0.1
    assert m.snapshot()["errors"]["max_steps_exceeded"] == 1


# -- end to end through the app --------------------------------------------


def test_metrics_are_non_zero_after_one_run(client, scripted, corpus_index):
    scripted(full_run_script(corpus_index))

    assert client.get("/metrics").json()["runs"]["total"] == 0

    r = client.post("/api/analyse", json={"jd_text": "Backend Engineer. Python, Postgres, K8s."})
    assert r.status_code == 200

    snap = client.get("/metrics").json()
    assert snap["runs"]["total"] == 1
    assert snap["runs"]["by_terminated_reason"] == {"complete": 1}
    assert snap["tools"]["calls"]["retrieve_evidence"] == 3
    assert snap["tools"]["calls"]["parse_requirements"] == 1
    assert snap["tokens"]["total"] > 0
    assert snap["latency_ms"]["window"] == 1
    assert snap["guardrail"]["rejected_suggestions"] == 1
    assert snap["alerts"]["max_steps_rate_last_20"] == 0.0
    assert snap["alerts"]["p95_latency_threshold_ms"] == 30000


def test_request_id_is_echoed_and_threaded(client, scripted, corpus_index):
    scripted(full_run_script(corpus_index))
    r = client.post(
        "/api/analyse",
        json={"jd_text": "Backend Engineer."},
        headers={"x-request-id": "trace-me-123"},
    )
    assert r.headers["x-request-id"] == "trace-me-123"


def test_failures_are_counted_by_taxonomy_kind(client, scripted, corpus_index):
    scripted([planner_reply("teleport"), planner_reply("finish")])
    client.post("/api/analyse", json={"jd_text": "Backend Engineer."})
    snap = client.get("/metrics").json()
    assert snap["errors"]["tool_execution"] == 1
    assert snap["tools"]["failures"]["teleport"] == 1


def test_max_steps_run_increments_its_own_error_counter(client, scripted, corpus_index):
    from app import config
    from app.main import app

    app.state.settings = config.Settings(
        anthropic_api_key="sk-test", db_path=":memory:", max_steps=2
    )
    scripted([planner_reply("retrieve_evidence", requirement="Python") for _ in range(6)])
    body = client.post("/api/analyse", json={"jd_text": "Backend Engineer."}).json()

    assert body["terminated_reason"] == "max_steps"
    snap = client.get("/metrics").json()
    assert snap["errors"]["max_steps_exceeded"] == 1
    assert snap["alerts"]["max_steps_rate_last_20"] == 1.0


def test_run_is_persisted_with_its_trace(client, scripted, corpus_index):
    from app.main import app

    scripted(full_run_script(corpus_index))
    client.post("/api/analyse", json={"jd_text": "Backend Engineer. Python."})

    store = app.state.store
    assert store.count() == 1
    loaded = store.load_run(1)
    assert loaded["role_title"] == "Backend Engineer, Payments Platform"
    assert len(loaded["result"].trace) == 7
    assert loaded["terminated_reason"] == "complete"


def test_api_rejects_an_empty_jd(client):
    assert client.post("/api/analyse", json={"jd_text": "  "}).status_code == 400
