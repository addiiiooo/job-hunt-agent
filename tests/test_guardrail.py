"""Phase 5: the grounding validator.

The headline test is `test_fabricated_citation_becomes_a_refusal`. Everything
the drafting prompt asks for, this proves — in code, deterministically, without
sampling a model."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.client import ScriptedClient
from app.agent.guardrail import REFUSAL_REASON, enforce
from app.agent.loop import run
from app.agent.tools import ev_key
from app.config import Settings
from app.corpus.index import CorpusIndex
from app.models import BulletSuggestion, Evidence
from tests.fakes import (
    R_K8S,
    R_POSTGRES,
    R_PYTHON,
    SMALL_REQUIREMENTS,
    planner_reply,
    tool_reply,
)

DOCS = Path(__file__).resolve().parents[1] / "app" / "corpus" / "documents"
REAL_DOC = "resume#databases"


@pytest.fixture()
def evidence_map():
    return {
        ev_key(R_POSTGRES): [
            Evidence(doc_id=REAL_DOC, snippet="EXPLAIN ANALYZE on hot paths", score=6.3)
        ],
        ev_key(R_K8S): [],
    }


# -- unit -------------------------------------------------------------------


def test_fabricated_citation_becomes_a_refusal(evidence_map):
    """The one that matters: the model cites a doc that retrieval never returned."""
    fabricated = BulletSuggestion(
        requirement_text=R_POSTGRES,
        bullet="Led a 12-node PostgreSQL sharding programme across three regions.",
        refused=False,
        grounded_in=["resume#sharding-programme"],  # never retrieved, does not exist
    )

    outcome = enforce([fabricated], evidence_map)
    result = outcome.suggestions[0]

    assert result.refused is True
    assert result.refusal_reason == REFUSAL_REASON
    assert result.bullet is None
    assert result.grounded_in == []
    assert outcome.rejected_count == 1
    assert "resume#sharding-programme" in outcome.rejected[0]["detail"]
    assert "sharding programme" in outcome.rejected[0]["discarded_bullet"]


def test_bullet_citing_nothing_becomes_a_refusal(evidence_map):
    ungrounded = BulletSuggestion(
        requirement_text=R_POSTGRES, bullet="Deep PostgreSQL expertise.", grounded_in=[]
    )
    result = enforce([ungrounded], evidence_map).suggestions[0]
    assert result.refused is True and result.refusal_reason == REFUSAL_REASON


def test_partially_fabricated_citation_is_still_a_refusal(evidence_map):
    """One real citation does not launder a fake one alongside it."""
    mixed = BulletSuggestion(
        requirement_text=R_POSTGRES,
        bullet="Tuned queries and ran a sharding programme.",
        grounded_in=[REAL_DOC, "resume#sharding-programme"],
    )
    assert enforce([mixed], evidence_map).suggestions[0].refused is True


def test_citation_from_another_requirement_is_a_refusal(evidence_map):
    borrowed = BulletSuggestion(
        requirement_text=R_K8S,
        bullet="Operated Kubernetes clusters in production.",
        grounded_in=[REAL_DOC],  # real, but retrieved for PostgreSQL, not for this
    )
    assert enforce([borrowed], evidence_map).suggestions[0].refused is True


def test_a_properly_grounded_bullet_is_left_alone(evidence_map):
    good = BulletSuggestion(
        requirement_text=R_POSTGRES,
        bullet="Removed sequential scans from hot paths by reading query plans with EXPLAIN ANALYZE.",
        grounded_in=[REAL_DOC],
    )
    outcome = enforce([good], evidence_map)
    assert outcome.suggestions[0] == good
    assert outcome.rejected_count == 0


def test_an_honest_refusal_from_the_model_is_preserved_verbatim(evidence_map):
    honest = BulletSuggestion(
        requirement_text=R_K8S,
        refused=True,
        refusal_reason="no Kubernetes work appears anywhere in the corpus",
        grounded_in=[],
    )
    kept = enforce([honest], evidence_map).suggestions[0]
    assert kept.refusal_reason == "no Kubernetes work appears anywhere in the corpus"
    assert enforce([honest], evidence_map).rejected_count == 0


def test_requirement_with_no_retrieval_at_all_can_ground_nothing():
    lonely = BulletSuggestion(
        requirement_text="Rust", bullet="Wrote Rust.", grounded_in=["resume#summary"]
    )
    assert enforce([lonely], {}).suggestions[0].refused is True


# -- integration: the guardrail runs inside a real run ----------------------


def test_full_run_applies_the_guardrail_before_results_are_shown():
    """A complete four-tool run. The model drafts one honest bullet and one
    fabricated one; only the honest one survives."""
    index = CorpusIndex.from_directory(DOCS)
    pg_hits = index.query(R_POSTGRES, k=3)
    top_pg = pg_hits[0].doc_id

    client = ScriptedClient(
        [
            planner_reply("parse_requirements", why="the JD is not parsed yet"),
            tool_reply("emit_requirements", SMALL_REQUIREMENTS),
            planner_reply("retrieve_evidence", why="look for Python", requirement=R_PYTHON, k=3),
            planner_reply("retrieve_evidence", why="look for Postgres", requirement=R_POSTGRES, k=3),
            planner_reply("retrieve_evidence", why="look for Kubernetes", requirement=R_K8S, k=3),
            planner_reply("score_match", why="evidence gathered for every requirement"),
            tool_reply(
                "emit_match_report",
                {
                    "verdicts": [
                        {"requirement_text": R_PYTHON, "status": "met",
                         "cited_doc_ids": [index.query(R_PYTHON, k=3)[0].doc_id],
                         "rationale": "resume describes FastAPI services"},
                        {"requirement_text": R_POSTGRES, "status": "partial",
                         "cited_doc_ids": [top_pg],
                         "rationale": "query-plan work present, scale unstated"},
                        {"requirement_text": R_K8S, "status": "gap",
                         "cited_doc_ids": [], "rationale": "no Kubernetes evidence"},
                    ],
                    "summary": "Strong on Python and Postgres; no Kubernetes evidence.",
                },
            ),
            planner_reply("draft_bullets", why="two requirements came back short"),
            tool_reply(
                "emit_bullets",
                {
                    "suggestions": [
                        {"requirement_text": R_POSTGRES,
                         "bullet": "Read query plans with EXPLAIN ANALYZE to remove sequential scans from hot paths.",
                         "refused": False, "grounded_in": [top_pg]},
                        {"requirement_text": R_K8S,
                         "bullet": "Ran production Kubernetes clusters at scale.",
                         "refused": False, "grounded_in": ["resume#kubernetes"]},
                    ]
                },
            ),
            planner_reply("finish", why="report and bullets are done"),
        ]
    )

    result = run(
        "Backend Engineer. Python, PostgreSQL, Kubernetes.",
        client=client,
        index=index,
        settings=Settings(anthropic_api_key="sk-test", db_path=":memory:", max_steps=12),
    )

    assert result.terminated_reason == "complete"
    assert [s.tool for s in result.trace] == [
        "parse_requirements", "retrieve_evidence", "retrieve_evidence",
        "retrieve_evidence", "score_match", "draft_bullets", "finish",
    ]
    assert len(result.trace) >= 4
    assert result.report.hard_met == 1 and result.report.hard_total == 2

    by_req = {s.requirement_text: s for s in result.suggestions}
    assert by_req[R_POSTGRES].refused is False
    assert by_req[R_K8S].refused is True
    assert by_req[R_K8S].refusal_reason == REFUSAL_REASON
    assert by_req[R_K8S].bullet is None
    assert "rewritten by the grounding validator" in result.trace[5].result_summary
