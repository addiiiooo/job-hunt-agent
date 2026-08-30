"""FastAPI application: routes, middleware, wiring.

Two pages and three JSON endpoints. All dependency construction happens once,
at startup, and hangs off ``app.state`` so tests can swap any piece.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import health
from app.config import get_settings
from app.corpus.index import CorpusIndex
from app.db import RunStore

_HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.store = RunStore(settings.db_path)
    app.state.index = CorpusIndex.from_directory(settings.corpus_dir)
    yield


app = FastAPI(title="Job Hunt Agent", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.post("/analyse", response_class=HTMLResponse)
async def analyse(request: Request, jd_text: str = Form(...)):
    # Phase 1: echo. The agent lands in phase 4.
    return templates.TemplateResponse(
        request, "index.html", {"echo": jd_text, "chars": len(jd_text)}
    )


@app.get("/health")
async def healthcheck(request: Request):
    payload = health.check(
        request.app.state.settings, request.app.state.store, request.app.state.index
    )
    return JSONResponse(payload, status_code=200 if payload["status"] == "ok" else 503)
