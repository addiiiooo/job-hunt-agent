from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DB_PATH", ":memory:")

# Tests assert exact retrieval results, so they run against a fixed fixture
# corpus, never the owner's real documents in app/corpus/documents/. Assigned,
# not defaulted: a CORPUS_DIR exported in the shell must not change test results.
FIXTURE_CORPUS = ROOT / "tests" / "fixtures" / "corpus"
os.environ["CORPUS_DIR"] = str(FIXTURE_CORPUS)


@pytest.fixture()
def settings(tmp_path):
    from app.config import Settings

    return Settings(anthropic_api_key="sk-test", db_path=str(tmp_path / "t.db"), max_steps=12)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config

    monkeypatch.setenv("DB_PATH", str(tmp_path / "runs.db"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    config.reset_settings_cache()
    from app.main import app

    with TestClient(app) as c:
        yield c
    config.reset_settings_cache()


@pytest.fixture(autouse=True)
def clean_metrics():
    """Metrics are a process-wide singleton; each test starts from zero."""
    from app.obs.metrics import METRICS

    METRICS.reset()
    yield
    METRICS.reset()


@pytest.fixture()
def scripted(client):
    """Install a scripted Claude client on the running app."""
    from app.agent.client import ScriptedClient
    from app.main import app

    def install(replies):
        fake = ScriptedClient(replies)
        app.state.client_factory = lambda: fake
        return fake

    return install


@pytest.fixture()
def corpus_index():
    from app.corpus.index import CorpusIndex

    return CorpusIndex.from_directory(FIXTURE_CORPUS)
