"""Per-dependency health.

A bare 200 tells you the process is alive, which is the one thing you already
knew because it answered. This reports each dependency separately so a probe
failure names its own cause.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.db import RunStore


def check(settings: Settings, store: RunStore, index: Any | None) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}

    ok, detail = store.ping()
    checks["sqlite"] = {"status": "ok" if ok else "fail", "detail": detail, "path": settings.db_path}

    if index is None:
        checks["corpus"] = {"status": "fail", "detail": "index not loaded", "documents": 0}
    else:
        n = index.size()
        checks["corpus"] = {
            "status": "ok" if n > 0 else "fail",
            "detail": "ok" if n > 0 else "corpus_empty: no documents indexed",
            "documents": n,
            "dir": settings.corpus_dir,
        }

    checks["anthropic_api_key"] = {
        "status": "ok" if settings.api_key_present else "fail",
        "detail": "present" if settings.api_key_present else "ANTHROPIC_API_KEY not set",
    }

    overall = "ok" if all(c["status"] == "ok" for c in checks.values()) else "degraded"
    return {"status": overall, "model": settings.model, "checks": checks}
