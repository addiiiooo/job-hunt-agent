from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DB_PATH", ":memory:")


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
