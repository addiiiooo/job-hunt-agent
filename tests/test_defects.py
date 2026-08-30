"""Regressions for the four defects found in a real sample run.

Each test is named for the thing that was wrong, so a future break says what it
broke rather than which assertion tripped."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.client import ScriptedClient
from app.agent.guardrail import REFUSAL_REASON, enforce
from app.agent.loop import run
from app.agent.tools import ev_key, score_match
from app.config import Settings
from app.corpus.index import RELEVANCE_THRESHOLD, CorpusIndex
from app.models import BulletSuggestion, Evidence, Requirements
from tests.fakes import (
    R_K8S,
    R_POSTGRES,
    R_PYTHON,
    SMALL_REQUIREMENTS,
    full_run_script,
    tool_reply,
)

DOCS = Path(__file__).resolve().parents[1] / "app" / "corpus" / "documents"
JD = "Backend Engineer. Python, PostgreSQL, Kubernetes."


def cfg(**over):
    base = {"anthropic_api_key": "sk-test", "db_path": ":memory:", "max_steps": 20}
    base.update(over)
    return Settings(**base)


@pytest.fixture(scope="module")
def index():
    return CorpusIndex.from_directory(DOCS)


# ---------------------------------------------------------------------------
# 2.1 — the trace and the report must not contradict each other
# ---------------------------------------------------------------------------


def test_retrieved_but_uncited_passages_stay_on_the_verdict(index):
    """The exact defect: retrieval returned 3 passages, the report said none."""
    reqs = Requirements.model_validate(SMALL_REQUIREMENTS)
    evidence_map = {ev_key(r["text"]): index.query(r["text"], k=3) for r in SMALL_REQUIREMENTS["requirements"]}
    assert len(evidence_map[ev_key(R_K8S)]) > 0, "precondition: retrieval finds something"

    client = ScriptedClient(
        [
            tool_reply(
                "emit_match_report",
                {
                    "verdicts": [
                        {"requirement_text": R_K8S, "status": "gap", "cited_doc_ids": [],
                         "rationale": "no direct Kubernetes ownership evidenced"},
                        {"requirement_text": R_PYTHON, "status": "gap", "cited_doc_ids": [], "rationale": "n/a"},
                        {"requirement_text": R_POSTGRES, "status": "gap", "cited_doc_ids": [], "rationale": "n/a"},
                    ],
                    "summary": "s",
                },
            )
        ]
    )
    report, _, _ = score_match(reqs, evidence_map, client=client, settings=cfg())
    k8s = next(v for v in report.verdicts if v.requirement.text == R_K8S)

    assert len(k8s.evidence) == len(evidence_map[ev_key(R_K8S)])
    assert not any(e.cited for e in k8s.evidence)
    assert k8s.status == "gap"


def test_trace_and_report_agree_on_what_retrieval_returned(index):
    """The invariant, asserted over a whole run.

    For every retrieve_evidence step that returned N > 0 passages, the verdict
    for that requirement must not present as having retrieved zero."""
    client = ScriptedClient(full_run_script(index))
    result = run(JD, client=client, index=index, settings=cfg())

    retrieved_counts: dict[str, int] = {}
    for step in result.trace:
        if step.tool != "retrieve_evidence" or step.error:
            continue
        requirement = step.args.get("requirement", "")
        n = index.query(requirement, k=int(step.args.get("k") or 5))
        retrieved_counts[ev_key(requirement)] = len(n)

    assert any(v > 0 for v in retrieved_counts.values()), "precondition: something was retrieved"

    for verdict in result.report.verdicts:
        n = retrieved_counts.get(ev_key(verdict.requirement.text))
        if n:
            assert verdict.evidence, (
                f"trace shows {n} passages for {verdict.requirement.text!r} "
                "but the verdict presents as having retrieved none"
            )
            assert len(verdict.evidence) == n


def test_weak_passages_are_marked_not_dropped(index):
    hits = index.query("Kubernetes in production", k=5)
    assert hits and all(0.0 <= h.score_normalized <= 1.0 for h in hits)
    assert hits[0].score_normalized == 1.0, "top hit normalises to 1.0 by construction"
    assert all(h.relevant == (h.score_normalized >= RELEVANCE_THRESHOLD) for h in hits)


def test_a_zero_overlap_query_still_returns_nothing(index):
    assert index.query("underwater basket weaving certification", k=5) == []


# ---------------------------------------------------------------------------
# 2.2 — no raw BM25 score reaches the reader
# ---------------------------------------------------------------------------


def test_raw_score_is_kept_in_the_model(index):
    hit = index.query("PostgreSQL query plans and indexing", k=1)[0]
    assert hit.score > 1.0, "raw BM25 is unbounded and retained for debugging"
    assert hit.score != hit.score_normalized


def test_no_raw_score_is_rendered_on_the_page(client, scripted, corpus_index):
    scripted(full_run_script(corpus_index))
    html = client.post("/analyse", data={"jd_text": JD}).text

    raw_values = {str(e.score) for r in SMALL_REQUIREMENTS["requirements"]
                  for e in corpus_index.query(r["text"], k=3)}
    assert raw_values, "precondition: there are raw scores to leak"
    leaked = [v for v in raw_values if v in html]
    assert not leaked, f"raw BM25 scores rendered in the UI: {leaked}"
    assert 'class="relfill"' in html and "%" in html


# ---------------------------------------------------------------------------
# 2.3 — refusal provenance
# ---------------------------------------------------------------------------


def test_validator_override_is_labelled_validator():
    evidence_map = {ev_key(R_POSTGRES): [Evidence(doc_id="resume#databases", snippet="x", score=6.0)]}
    fabricated = BulletSuggestion(
        requirement_text=R_POSTGRES, bullet="Led a sharding programme.",
        grounded_in=["resume#invented"],
    )
    out = enforce([fabricated], evidence_map).suggestions[0]
    assert out.refused is True
    assert out.refusal_source == "validator"
    assert out.refusal_reason == REFUSAL_REASON


def test_model_refusal_keeps_model_provenance():
    honest = BulletSuggestion(
        requirement_text=R_K8S, refused=True, refusal_source="model",
        refusal_reason="no Kubernetes work appears anywhere in the corpus",
    )
    out = enforce([honest], {}).suggestions[0]
    assert out.refusal_source == "model"
    assert out.refusal_reason == "no Kubernetes work appears anywhere in the corpus"


def test_the_drafting_model_cannot_claim_validator_provenance():
    from app.agent.tools import EMIT_BULLETS

    props = EMIT_BULLETS["input_schema"]["properties"]["suggestions"]["items"]["properties"]
    assert "refusal_source" not in props


def test_page_distinguishes_an_override_from_a_model_refusal(client, scripted, corpus_index):
    scripted(full_run_script(corpus_index))
    html = client.post("/analyse", data={"jd_text": JD}).text
    assert "Overridden by the grounding validator" in html
    assert "discarded by the code, not by the model" in html
    assert "refused override" in html or "override" in html


# ---------------------------------------------------------------------------
# 2.4 — latency accounting
# ---------------------------------------------------------------------------


def test_total_latency_equals_the_sum_of_step_latencies(index):
    client = ScriptedClient(full_run_script(index))
    result = run(JD, client=client, index=index, settings=cfg())
    assert result.total_latency_ms == sum(s.latency_ms for s in result.trace)


def test_latency_reconciles_when_the_numbers_are_not_all_zero(index):
    """The scripted client is instant, so the plain assertion can hold at 0 == 0.

    This one puts real milliseconds on the clock so the reconciliation is
    actually exercised."""
    import time

    inner = ScriptedClient(full_run_script(index))

    class Slow:
        def complete(self, **kwargs):
            time.sleep(0.012)
            return inner.complete(**kwargs)

    result = run(JD, client=Slow(), index=index, settings=cfg())

    assert result.total_latency_ms == sum(s.latency_ms for s in result.trace)
    assert result.total_latency_ms > 0, "precondition: the run took measurable time"
    assert all(s.latency_ms > 0 for s in result.trace if s.tool != "retrieve_evidence")


def test_latency_still_reconciles_when_steps_fail(index):
    from tests.fakes import planner_reply

    client = ScriptedClient([planner_reply("teleport"), planner_reply("finish")])
    result = run(JD, client=client, index=index, settings=cfg())
    assert result.trace[0].error is not None
    assert result.total_latency_ms == sum(s.latency_ms for s in result.trace)
