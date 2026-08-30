"""Phase 5: scoring.

The two properties worth asserting are that the headline number is computed
from the verdicts rather than taken from the model, and that a citation the
model made up cannot survive into the report."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.client import FakeClient
from app.agent.tools import ev_key, score_match
from app.config import Settings
from app.corpus.index import CorpusIndex
from app.models import AgentError, Requirements
from tests.fakes import R_K8S, R_POSTGRES, R_PYTHON, SMALL_REQUIREMENTS, tool_reply

DOCS = Path(__file__).resolve().parents[1] / "app" / "corpus" / "documents"


@pytest.fixture(scope="module")
def index():
    return CorpusIndex.from_directory(DOCS)


@pytest.fixture()
def reqs():
    return Requirements.model_validate(SMALL_REQUIREMENTS)


@pytest.fixture()
def evidence_map(index):
    return {
        ev_key(R_PYTHON): index.query(R_PYTHON, k=3),
        ev_key(R_POSTGRES): index.query(R_POSTGRES, k=3),
        ev_key(R_K8S): index.query(R_K8S, k=3),
    }


def cfg():
    return Settings(anthropic_api_key="sk-test", db_path=":memory:")


def _scoring(verdicts, summary="ok"):
    return tool_reply("emit_match_report", {"verdicts": verdicts, "summary": summary})


def test_totals_are_computed_from_verdicts_not_taken_from_the_model(reqs, evidence_map):
    top_py = evidence_map[ev_key(R_PYTHON)][0].doc_id
    top_pg = evidence_map[ev_key(R_POSTGRES)][0].doc_id
    client = FakeClient(
        [
            _scoring(
                [
                    {"requirement_text": R_PYTHON, "status": "met",
                     "cited_doc_ids": [top_py], "rationale": "resume shows FastAPI services"},
                    {"requirement_text": R_POSTGRES, "status": "met",
                     "cited_doc_ids": [top_pg], "rationale": "EXPLAIN ANALYZE on hot paths"},
                    {"requirement_text": R_K8S, "status": "gap",
                     "cited_doc_ids": [], "rationale": "nothing in the corpus"},
                ]
            )
        ]
    )
    report, _, _ = score_match(reqs, evidence_map, client=client, settings=cfg())

    assert (report.hard_met, report.hard_total) == (2, 2)
    assert len(report.verdicts) == 3
    # The emitted schema gives the model no place to put a total at all.
    assert "hard_met" not in client.calls[0]["tools"][0]["input_schema"]["properties"]


def test_fabricated_citation_is_dropped_and_the_verdict_downgraded(reqs, evidence_map):
    client = FakeClient(
        [
            _scoring(
                [
                    {"requirement_text": R_PYTHON, "status": "met",
                     "cited_doc_ids": ["resume#nonexistent-section"],
                     "rationale": "invented citation"},
                    {"requirement_text": R_POSTGRES, "status": "gap",
                     "cited_doc_ids": [], "rationale": "n/a"},
                    {"requirement_text": R_K8S, "status": "gap",
                     "cited_doc_ids": [], "rationale": "n/a"},
                ]
            )
        ]
    )
    report, _, _ = score_match(reqs, evidence_map, client=client, settings=cfg())
    python_verdict = report.verdicts[0]

    assert python_verdict.evidence == []
    assert python_verdict.status == "gap", "met with no real citation is a gap"
    assert report.hard_met == 0


def test_citation_belonging_to_another_requirement_does_not_transfer(reqs):
    """Evidence is scoped per requirement, not pooled across the run.

    Built by hand rather than from real retrieval: BM25 legitimately returns
    overlapping chunks for overlapping requirements, so a controlled map is the
    only way to assert the scoping rule itself.
    """
    from app.models import Evidence

    pg_only = Evidence(doc_id="resume#databases", snippet="postgres work", score=5.0)
    scoped = {
        ev_key(R_PYTHON): [],
        ev_key(R_POSTGRES): [pg_only],
        ev_key(R_K8S): [],  # retrieval ran for Kubernetes and found nothing
    }
    client = FakeClient(
        [
            _scoring(
                [
                    {"requirement_text": R_K8S, "status": "met",
                     "cited_doc_ids": [pg_only.doc_id], "rationale": "borrowed from another requirement"},
                    {"requirement_text": R_PYTHON, "status": "gap", "cited_doc_ids": [], "rationale": "n/a"},
                    {"requirement_text": R_POSTGRES, "status": "met",
                     "cited_doc_ids": [pg_only.doc_id], "rationale": "legitimately cited here"},
                ]
            )
        ]
    )
    report, _, _ = score_match(reqs, scoped, client=client, settings=cfg())

    k8s = next(v for v in report.verdicts if v.requirement.text == R_K8S)
    postgres = next(v for v in report.verdicts if v.requirement.text == R_POSTGRES)

    assert k8s.evidence == [] and k8s.status == "gap"
    assert [e.doc_id for e in postgres.evidence] == [pg_only.doc_id]


def test_evidence_is_rehydrated_from_retrieval_not_from_the_model(reqs, evidence_map):
    real = evidence_map[ev_key(R_POSTGRES)][0]
    client = FakeClient(
        [
            _scoring(
                [
                    {"requirement_text": R_POSTGRES, "status": "met",
                     "cited_doc_ids": [real.doc_id], "rationale": "query plans"},
                    {"requirement_text": R_PYTHON, "status": "gap", "cited_doc_ids": [], "rationale": "n/a"},
                    {"requirement_text": R_K8S, "status": "gap", "cited_doc_ids": [], "rationale": "n/a"},
                ]
            )
        ]
    )
    report, _, _ = score_match(reqs, evidence_map, client=client, settings=cfg())
    cited = next(v for v in report.verdicts if v.requirement.text == R_POSTGRES).evidence[0]

    assert cited.snippet == real.snippet and cited.score == real.score


def test_a_requirement_the_model_skipped_becomes_a_gap(reqs, evidence_map):
    client = FakeClient(
        [_scoring([{"requirement_text": R_PYTHON, "status": "gap", "cited_doc_ids": [], "rationale": "n/a"}])]
    )
    report, _, _ = score_match(reqs, evidence_map, client=client, settings=cfg())
    assert len(report.verdicts) == 3
    assert all(v.status == "gap" for v in report.verdicts)
    assert "No verdict was returned" in report.verdicts[1].rationale


def test_verdict_order_follows_the_requirements_not_the_model(reqs, evidence_map):
    client = FakeClient(
        [
            _scoring(
                [
                    {"requirement_text": R_K8S, "status": "gap", "cited_doc_ids": [], "rationale": "n/a"},
                    {"requirement_text": R_PYTHON, "status": "gap", "cited_doc_ids": [], "rationale": "n/a"},
                    {"requirement_text": R_POSTGRES, "status": "gap", "cited_doc_ids": [], "rationale": "n/a"},
                ]
            )
        ]
    )
    report, _, _ = score_match(reqs, evidence_map, client=client, settings=cfg())
    assert [v.requirement.text for v in report.verdicts] == [R_PYTHON, R_POSTGRES, R_K8S]


def test_requirement_text_matching_is_whitespace_and_case_insensitive(reqs, evidence_map):
    top_py = evidence_map[ev_key(R_PYTHON)][0].doc_id
    client = FakeClient(
        [
            _scoring(
                [{"requirement_text": "  strong   PYTHON ", "status": "met",
                  "cited_doc_ids": [top_py], "rationale": "matched despite sloppy echo"}]
            )
        ]
    )
    report, _, _ = score_match(reqs, evidence_map, client=client, settings=cfg())
    assert report.verdicts[0].status == "met"


def test_scoring_without_requirements_is_a_tool_execution_error(evidence_map):
    with pytest.raises(AgentError) as exc:
        score_match(
            Requirements(role_title="x", requirements=[]),
            evidence_map,
            client=FakeClient([]),
            settings=cfg(),
        )
    assert exc.value.kind.value == "tool_execution"
