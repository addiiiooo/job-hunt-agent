---
id: project-job-hunt-agent
title: Project — Job Hunt Agent
---

<!-- Written from this repository (README, DECISIONS, app/, extension/,
     tests/). It describes the system that reads this corpus. -->

## Overview

An LLM agent that reads a job description, scores the candidate against it
using evidence retrieved from their own resume and project write-ups, and
drafts tailored resume bullets — refusing to draft any bullet it cannot ground
in a document the candidate actually wrote. Retrieval-grounded generation
(RAG-style) over a small personal corpus, built in Python with FastAPI,
Pydantic and the Anthropic Claude API. It is decision support, not an
auto-applier: it never submits anything, and a human always presses send.

## Agent loop and tool calling

A planner-driven, bounded agent loop. Every step is one Claude call that is
given the goal, the current run state, the four tools as real tool definitions
and a compact trace of everything so far, and returns one tool call or finish.
The tools are parse_requirements, retrieve_evidence, score_match and
draft_bullets; their order is chosen by the planner, not hard-coded, and a test
asserts that. Each planner-facing tool schema carries a required rationale,
which the dispatcher strips and records in the trace. The loop terminates on
finish or at a step limit (12 by default) and records which.

## Constraining the model

Every model call is forced into a typed schema: each tool is an Anthropic tool
definition generated from the Pydantic model it returns, with tool_choice
pinned to it, and Pydantic validates at the boundary. A schema validation
failure is fed back to the model once as a repair attempt. The model may only
cite evidence ids it was given — the server rehydrates snippet, score and
relevance from what retrieval actually returned, so the model cannot author an
evidence object. Totals such as hard requirements met are computed in code
from the verdicts; the scoring schema has no field for a number. Refusals are
stamped with their source, model or validator, by the server rather than by
the model.

## Grounding guardrail

A validator in code runs after drafting and before anything reaches the user.
Every non-refused bullet must cite at least one document id, and every id it
cites must appear in the evidence retrieved for that same requirement — not
merely somewhere in the corpus. Anything failing becomes a visible refusal and
the discarded draft is logged, never shown. This is the system's defence
against hallucination: a claim with no retrieved source does not reach the
page. The prompt asks the model to
behave; the validator is the guarantee, and a test proves it catches a
fabricated citation. It checks grounding, not truthfulness: a bullet that cites
a real passage and overstates it would pass.

## Retrieval

BM25 over markdown documents chunked at the section level, so a citation points
at a paragraph a reviewer can check. In-process, rebuilt at startup in
milliseconds, with exact and explainable scores — chosen over a vector database
because the corpus is a few dozen chunks. Relevance is normalised per query
and weak passages are flagged rather than dropped, so the report can tell
"nothing retrieved" apart from "retrieved but below threshold". The known
weakness is paraphrase; the planned fix is hybrid retrieval (BM25 plus
embeddings, merged by reciprocal rank fusion) followed by a cross-encoder
rerank.

## Reliability and error handling

A wall-clock timeout per call, up to two retries with exponential backoff on
HTTP 429 and 5xx, no retry on any other 4xx because that is a bug in the
request, and one schema-repair attempt. SDK-level retries are disabled so the
retry policy lives in one place. Every failure maps to exactly one error kind:
upstream timeout, upstream rate limit, schema validation, tool execution, max
steps exceeded, corpus empty.

## Observability

The trace is an output, not a log: every step, including failures, records its
tool, arguments, rationale, latency, token counts and error kind, and is
rendered on the result page, stored in SQLite with the run, and asserted in
tests. Structured JSON logs with a request id threaded through worker threads
via context variables. A per-dependency health endpoint that fails on an empty
or placeholder corpus. A metrics endpoint with run counts, per-tool failures,
p50 and p95 latency over the last 500 runs, token totals, guardrail rejections
and error counts by kind. Alert conditions: p95 run latency above 30 seconds,
or more than 10% of the last 20 runs ending at the step limit.

## Chrome extension

A Manifest V3 Chrome extension reads the job posting from the tab the user has
open and sends it to the app. Permissions are activeTab, scripting and storage,
with no host permissions at install time — the single server origin is
requested at runtime — and no background service worker, so it runs no code
while the popup is closed. No scraping, auto-fill or auto-submit; tests assert
their absence. Extraction is client-side because server-side scraping of job
boards is fragile, rate-limited and often against their terms.

## Testing, CI and deployment

Over 130 tests run offline in about two seconds against a scripted client; one
live-API test skips without a key. GitHub Actions runs ruff, the test suite,
and a Docker image build that asserts the container answers its health check.
Deployment targets Azure Container Apps through a one-command script: the image
is built in Azure Container Registry, the API key is a Container Apps secret,
and the recommended public deployment runs in scripted mode so an
unauthenticated URL cannot run up model costs.

## Status

Built and tested end to end in scripted mode, where retrieval, the validator,
the trace and metrics are real and only model replies are canned. Not deployed
yet, and no live-model sample has been recorded. Known limitations are
documented: paraphrase misses in BM25, no rerank, no labelled evaluation set,
cost growing with requirement count, and a single-process design.
