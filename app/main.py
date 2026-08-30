"""FastAPI application: routes, middleware, wiring.

Dependency construction happens once at startup and hangs off ``app.state``,
which is also the seam tests use to inject a scripted Claude client.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app import health
from app.agent import loop as agent_loop
from app.agent.client import AnthropicClient
from app.config import Settings, get_settings
from app.corpus.index import CorpusIndex
from app.db import RunStore
from app.models import AgentResult
from app.obs.logging import (
    configure_logging,
    get_request_id,
    log_event,
    new_request_id,
    request_id_var,
)
from app.obs.metrics import METRICS

_HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))


def default_client_factory(settings: Settings) -> Any:
    return AnthropicClient(settings.require_api_key(), settings.model)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    app.state.settings = settings
    app.state.store = RunStore(settings.db_path)
    app.state.index = CorpusIndex.from_directory(settings.corpus_dir)
    app.state.client_factory = lambda: default_client_factory(settings)
    log_event(
        "startup",
        model=settings.model,
        corpus_documents=app.state.index.size(),
        corpus_chunks=app.state.index.chunk_count(),
        max_steps=settings.max_steps,
        api_key_present=settings.api_key_present,
    )
    yield
    log_event("shutdown")


app = FastAPI(title="Job Hunt Agent", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Mint a request id and put it in the context for the whole request.

    Everything downstream — every tool call, every log line, the response
    header — carries this id, which is what makes a single run reconstructable
    from logs after the fact.
    """
    request_id = request.headers.get("x-request-id") or new_request_id()
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log_event(
            "http_error",
            method=request.method,
            path=request.url.path,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        request_id_var.reset(token)
        raise
    duration_ms = int((time.perf_counter() - started) * 1000)
    response.headers["x-request-id"] = request_id
    log_event(
        "http_request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=duration_ms,
    )
    request_id_var.reset(token)
    return response


# ---------------------------------------------------------------------------
# Agent invocation
# ---------------------------------------------------------------------------


async def execute_run(request: Request, jd_text: str) -> tuple[AgentResult, dict[str, Any]]:
    """Run the agent once, then log, count and persist it.

    Returns the result plus the run metadata the UI needs but the result
    model does not carry (the role title, the persisted run id)."""
    settings: Settings = request.app.state.settings
    store: RunStore = request.app.state.store
    index = request.app.state.index
    request_id = get_request_id()
    captured: dict[str, Any] = {}

    def on_event(event: str, fields: dict[str, Any]) -> None:
        if event == "requirements_parsed":
            captured["role_title"] = fields.get("role_title")
        if event == "guardrail_rejected":
            METRICS.record_guardrail_rejections(int(fields.get("count", 0)))
        log_event(event, **fields)

    client = request.app.state.client_factory()
    result: AgentResult = await run_in_threadpool(
        agent_loop.run,
        jd_text,
        client=client,
        index=index,
        settings=settings,
        request_id=request_id,
        on_event=on_event,
    )

    METRICS.observe_run(result)
    run_id = await run_in_threadpool(
        store.save_run, request_id, jd_text, result, captured.get("role_title")
    )
    log_event(
        "run_complete",
        run_id=run_id,
        terminated_reason=result.terminated_reason,
        steps=len(result.trace),
        hard_met=result.report.hard_met,
        hard_total=result.report.hard_total,
        suggestions=len(result.suggestions),
        refusals=sum(1 for s in result.suggestions if s.refused),
        total_latency_ms=result.total_latency_ms,
        total_tokens=result.total_tokens,
    )
    return result, {"run_id": run_id, "role_title": captured.get("role_title"), "request_id": request_id}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.post("/analyse", response_class=HTMLResponse)
async def analyse(request: Request, jd_text: str = Form(...)):
    result, meta = await execute_run(request, jd_text)
    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "result": result,
            "report": result.report,
            "jd_text": jd_text,
            "meta": meta,
            "request_id": get_request_id(),
        },
    )


@app.post("/api/analyse")
async def api_analyse(request: Request):
    body = await request.json()
    jd_text = (body or {}).get("jd_text", "")
    if not jd_text.strip():
        return JSONResponse({"error": "jd_text is required"}, status_code=400)
    result, meta = await execute_run(request, jd_text)
    return JSONResponse({**result.model_dump(), "meta": meta})


@app.get("/health")
async def healthcheck(request: Request):
    payload = health.check(
        request.app.state.settings, request.app.state.store, request.app.state.index
    )
    return JSONResponse(payload, status_code=200 if payload["status"] == "ok" else 503)


@app.get("/metrics")
async def metrics():
    return JSONResponse(METRICS.snapshot())
