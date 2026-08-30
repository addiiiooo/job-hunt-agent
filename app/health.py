"""Per-dependency health.

A bare 200 tells you the process is alive, which is the one thing you already
knew because it answered. This reports each dependency separately so a probe
failure names its own cause.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.corpus.index import PLACEHOLDER_MARKER
from app.db import RunStore


def check(settings: Settings, store: RunStore, index: Any | None) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}

    ok, detail = store.ping()
    checks["sqlite"] = {"status": "ok" if ok else "fail", "detail": detail, "path": settings.db_path}

    if index is None:
        checks["corpus"] = {"status": "fail", "detail": "index not loaded", "documents": 0}
    else:
        n = index.size()
        placeholders = index.placeholder_doc_ids()
        if n == 0:
            status, detail = "fail", "corpus_empty: no documents indexed"
        elif placeholders:
            # A corpus of TODOs retrieves fluently and answers nothing, which is
            # a worse failure than an empty one because it looks like it works.
            status = "fail"
            detail = (
                "corpus_empty: documents still contain placeholder text "
                f"({PLACEHOLDER_MARKER!r}): " + ", ".join(placeholders)
            )
        else:
            status, detail = "ok", "ok"
        checks["corpus"] = {
            "status": status,
            "detail": detail,
            "documents": n,
            "chunks": index.chunk_count(),
            "placeholder_documents": placeholders,
            "dir": settings.corpus_dir,
        }

    if not settings.api_key_required:
        checks["anthropic_api_key"] = {
            "status": "ok",
            "detail": "not required (LLM_CLIENT=scripted)",
        }
    else:
        checks["anthropic_api_key"] = {
            "status": "ok" if settings.api_key_present else "fail",
            "detail": "present" if settings.api_key_present else "ANTHROPIC_API_KEY not set",
        }

    overall = "ok" if all(c["status"] == "ok" for c in checks.values()) else "degraded"
    return {
        "status": overall,
        "model": settings.model,
        "llm_client": settings.llm_client,
        "checks": checks,
    }
