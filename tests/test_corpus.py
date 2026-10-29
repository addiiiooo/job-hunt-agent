"""Phase 3: the corpus and BM25 retrieval.

Retrieval is the part of this system with no model in it, which makes it the
part that can be asserted exactly."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.agent.tools import retrieve_evidence
from app.corpus.index import CorpusIndex, parse_document, tokenize
from app.models import AgentError

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "tests" / "fixtures" / "corpus"
SHIPPED = ROOT / "app" / "corpus" / "documents"


@pytest.fixture(scope="module")
def index():
    return CorpusIndex.from_directory(DOCS)


def test_corpus_loads_three_documents(index):
    assert index.size() == 3
    assert index.chunk_count() > index.size(), "documents should split into sections"
    assert not index.is_empty()


def test_front_matter_id_becomes_the_doc_id(index):
    assert {"resume", "project-ledger-service", "project-observability-pipeline"} == {
        c.doc_id for c in index.chunks
    }


def test_known_term_returns_the_expected_document(index):
    hits = index.query("PostgreSQL query plans and indexing", k=5)
    assert hits, "a term present in the corpus must return something"
    assert hits[0].doc_id == "resume#databases"
    assert hits[0].score > 0
    assert "PostgreSQL" in hits[0].snippet


def test_retrieval_discriminates_between_documents(index):
    k8s = index.query("Kubernetes readiness probes", k=3)
    assert k8s[0].doc_id.startswith("project-observability-pipeline")

    ledger = index.query("double-entry journal entries idempotency", k=3)
    assert ledger[0].doc_id.startswith("project-ledger-service")


def test_absent_term_returns_nothing_rather_than_weak_noise(index):
    assert index.query("underwater basket weaving certification", k=5) == []


def test_k_is_respected(index):
    assert len(index.query("Python", k=2)) <= 2


def test_tokenizer_keeps_technical_tokens():
    assert "c++" in tokenize("Experience with C++")
    assert "ci" in tokenize("CI/CD pipelines") and "cd" in tokenize("CI/CD pipelines")
    assert "the" not in tokenize("the database")


def test_html_comments_are_stripped_from_chunks():
    chunks = parse_document(DOCS / "resume.md")
    assert all("<!--" not in c.text for c in chunks)


def test_retrieve_evidence_raises_corpus_empty_on_empty_index():
    with pytest.raises(AgentError) as exc:
        retrieve_evidence("Python", index=CorpusIndex([]))
    assert exc.value.kind.value == "corpus_empty"


def test_health_fails_on_a_placeholder_corpus(client):
    """The fixture corpus is the placeholder template, and /health must say so.

    A corpus of TODOs is a worse failure than an empty one: it indexes, it
    retrieves, and every answer it produces is about nothing."""
    body = client.get("/health").json()
    corpus = body["checks"]["corpus"]

    assert corpus["documents"] == 3, "the documents are loaded"
    assert corpus["status"] == "fail", "but they are not usable"
    assert "TODO: replace" in corpus["detail"]
    assert set(corpus["placeholder_documents"]) == {
        "resume", "project-ledger-service", "project-observability-pipeline"
    }
    assert body["status"] == "degraded"


def test_health_is_ok_once_the_corpus_has_real_content(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config

    (tmp_path / "resume.md").write_text(
        "---\nid: resume\ntitle: Resume\n---\n\n"
        "## Databases\n\nPostgreSQL schema design, indexing and EXPLAIN ANALYZE.\n"
    )
    monkeypatch.setenv("CORPUS_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    config.reset_settings_cache()
    from app.main import app

    with TestClient(app) as c:
        body = c.get("/health").json()
    config.reset_settings_cache()

    assert body["status"] == "ok"
    assert body["checks"]["corpus"]["status"] == "ok"
    assert body["checks"]["corpus"]["placeholder_documents"] == []


def test_shipped_corpus_is_real_content():
    """The corpus the app and the image ship with has no placeholders left.

    This is the corpus the CI container is health-checked against, so a
    placeholder sneaking back in fails here first, with a clearer message."""
    index = CorpusIndex.from_directory(SHIPPED)
    assert index.size() >= 3
    assert index.placeholder_doc_ids() == []
    for doc_id in {c.doc_id for c in index.chunks}:
        assert sum(c.doc_id == doc_id for c in index.chunks) > 1, f"{doc_id} has no sections"


def test_shipped_corpus_carries_no_contact_details():
    """Snippets are rendered on the result page, and the public demo serves it."""
    text = "\n".join(p.read_text(encoding="utf-8") for p in SHIPPED.glob("*.md"))
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), "email address in corpus"
    assert not re.search(r"\+?\d[\d ]{9,}\d", text), "phone number in corpus"


def test_health_fails_loudly_when_corpus_is_empty(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config

    monkeypatch.setenv("CORPUS_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    config.reset_settings_cache()
    from app.main import app

    with TestClient(app) as c:
        r = c.get("/health")
    config.reset_settings_cache()
    assert r.status_code == 503
    assert r.json()["checks"]["corpus"]["status"] == "fail"
    assert "corpus_empty" in r.json()["checks"]["corpus"]["detail"]
