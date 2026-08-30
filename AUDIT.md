# Audit

State of the repo before the "finish and ship" pass. Baseline: commit `de4ff5f`,
`pytest` → **81 passed, 1 skipped** (the skip is the live-API test, which skips
itself without a key). `ruff check app tests scripts` → clean.

Legend: **present** = built and tested · **partial** = exists but does not meet
the brief · **missing** = not there.

## §2 Defects — all four confirmed real

| # | Item | State | Where |
|---|---|---|---|
| 2.1 | Trace/report contradiction | **partial — confirmed** | `app/agent/tools.py:384` |
| 2.2 | Raw BM25 score displayed | **partial — confirmed** | `app/templates/result.html:35` |
| 2.3 | Refusal provenance | **missing — confirmed** | `refusal_source` absent from `app/models.py`, `app/agent/guardrail.py` |
| 2.4 | Latency accounting | **partial — confirmed** | `app/agent/loop.py:324,435` |

**2.1 — the diagnosis in the brief is close but not the cause.** There is no
relevance threshold in `score_match`, and the `evidence_map` key is already
normalised through `ev_key()`, so neither candidate cause applies. The actual
cause is narrower and worse: `_assemble_report` builds
`cited = [available[d] for d in scored.cited_doc_ids if d in available]` and
puts **only that** on the verdict. When the model returns a `gap` with an empty
`cited_doc_ids`, the verdict carries zero evidence even though retrieval
returned three passages a step earlier. The retrieved-but-uncited passages are
discarded at line 384 and never reach the template, so the page cannot
distinguish "nothing retrieved" from "retrieved and not cited". The fix in the
brief — carry everything retrieved and mark relevance on the `Evidence` — is the
right one and addresses this cause too.

**2.4** — `total_latency_ms` is wall-clock across the whole run
(`loop.py:324` → `435`), so it exceeds the sum of step latencies by the
per-iteration overhead. Step latencies are already measured with
`time.perf_counter()` via `trace.timed()`; only the total needs to change.

## §3 Real Claude client

| Item | State | Where |
|---|---|---|
| Protocol with two implementations | **present** | `app/agent/client.py:38,54,146` |
| `AnthropicClient` real, SDK retries disabled | **present** | `client.py:61` (`max_retries=0`) |
| Scripted client for tests | **partial** — named `FakeClient`, brief asks for `ScriptedClient` | `client.py:146` |
| `LLM_CLIENT=anthropic\|scripted` selector | **missing** | not in `app/config.py` |
| Real token counts populate each `Step` | **present** | `client.py:96`, `loop.py` step assembly |
| 2 retries, exponential backoff | **present** | `loop.py:43,247` |
| Retry scope limited to 429 + 5xx | **partial** — also retries timeouts and connection errors | `client.py:111` |
| Tests never touch the network | **present** | whole suite runs offline |
| Live end-to-end run kept in `samples/` | **missing** — `samples/` does not exist; `docs/sample-report.html` is a scripted render, not a live one |

## §4 Corpus

| Item | State | Where |
|---|---|---|
| Heading-chunked docs, `doc_id = file#heading-slug` | **present** | `app/corpus/index.py` |
| Real content | **missing** | 17 `TODO: replace` markers across 3 files |
| `/health` fails on placeholder text | **missing** | no check in `app/health.py` |

## §5 Observability

| Item | State | Where |
|---|---|---|
| JSON logs, one line per event, `request_id` threaded | **present** | `app/obs/logging.py` |
| `/health` per-dependency (sqlite, corpus, api key) | **present** | `app/health.py:16` |
| `/health` corpus-free-of-placeholders check | **missing** | — |
| `/metrics` counts, p50/p95, tokens, errors by kind | **present** | `app/obs/metrics.py:97`, `app/main.py:201` |
| `ErrorKind` with all six members | **present** | `app/models.py:22` |
| Alert condition documented | **present** | `README.md` → Observability |

## §6 Deploy — Azure Container Apps

| Item | State | Where |
|---|---|---|
| Single-stage Dockerfile, 3.11-slim, non-root, uvicorn | **present** | `Dockerfile` |
| `docker build` verified | **missing** | see Environment below |
| ACR push / Container Apps deploy | **missing** | — |
| `DEPLOY.md` | **missing** | — |
| Live URL in README | **missing** | placeholder at `README.md:8` |

## §7 Docs

| Item | State | Where |
|---|---|---|
| README section order (1–7) | **present** | verified programmatically |
| Live URL | **missing** | placeholder |
| GIF of a real run | **missing** | — |
| Decisions: grounding validator, SQLite over Postgres, one provider | **present** | README → Design decisions |
| Decision: client-side extraction | **missing** — no extension yet | — |
| Limitations: long JDs, cost per run, BM25 paraphrase | **present** | README → Known limitations |
| Limitation: no cross-encoder rerank | **missing** | — |
| `DECISIONS.md` | **present** | 8 phases of entries |

## §8 Chrome extension

**missing** — no `extension/` directory.

## Environment constraints found while auditing

These bound what this session can finish, and they are not repo defects.

- **No `docker` and no `az` on the machine this session reaches.** Both report
  `MISSING`. `node`/`npm`/`python3`/`curl` are present, and the network reaches
  PyPI.
- **The sandbox blocks every container registry** (Docker Hub, ECR Public and
  GCR all return HTTP 403), so a `docker build` cannot pull a base image here
  even where a daemon exists.
- **No `ANTHROPIC_API_KEY` is available to this session**, by the owner's
  earlier choice to keep it out of the transcript.

Consequence: §6 in full, the live run in §3, and the acceptance items depending
on them cannot be executed from here. Everything else can.
