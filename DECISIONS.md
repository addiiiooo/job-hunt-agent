# Decisions

Choices the brief left open, and why each one went the way it did. Roughly in
build order.

## Phase 1 — skeleton

**`Settings` is a Pydantic model with a `from_env` classmethod, not
`pydantic-settings`.** The brief fixes the dependency list and
`pydantic-settings` is not on it. A twenty-line classmethod gets the same type
coercion and validation from the Pydantic already in the stack.

**A missing `ANTHROPIC_API_KEY` does not stop the process from starting.** The
brief calls the key required, and it is — the agent cannot run without one. But
`/health` is specified to report "API key present" as a per-dependency status,
which is only possible if the process is alive to answer. So the key is
validated lazily at first use (`require_api_key`), and its absence shows up as
`anthropic_api_key: fail` with the overall status `degraded` and HTTP 503. A
container that boots and says why it is useless beats one that crash-loops.

**`os.environ` is read in exactly one function.** `Settings.from_env` is the
only place. That makes the configuration surface of the service equal to the
field list on one model, and it makes tests able to construct a `Settings`
directly instead of mutating global state.

**`ErrorKind` lives in `models.py`.** The brief fixes the file layout and does
not include an `errors.py`. The taxonomy is a shared enum crossing every
boundary, so it sits with the other shared types rather than earning a file the
layout does not allow.

**`RequirementVerdict.requirement` holds a `Requirement`, not a string.** The
brief lists the field without a type. The requirement table in the UI needs
`kind` and `category` per row, and keying a lookup on prose is fragile, so the
verdict carries the whole object.

**`Step` gains one field beyond the brief's list: `rationale`.** Section 10
specifies a "why chosen" column in the trace table. That value is the planner's
own stated reason for the step, and it has nowhere else to live. Every other
field on every other model is exactly as specified.

**SQLite connections are opened per call, not shared.** One write per run is
not a workload that needs pooling, and per-call connections avoid SQLite's
thread-affinity rules under uvicorn's threadpool. `:memory:` is the exception —
an in-memory database dies with its connection — so that one connection is held
for the life of the store, which is what tests want anyway.

**Python 3.10-compatible syntax, despite targeting 3.11+.** The container and
CI both run 3.11. Nothing in the codebase uses a 3.11-only construct
(`enum.StrEnum`, `typing.Self`, exception groups), so the test suite also runs
on 3.10 without a second thought. `from __future__ import annotations` is on
every module.

## Phase 2 — the model client

**`app/agent/client.py` is one module beyond the brief's layout.** The brief
requires that "the Claude client is injected, so tests run against a fake", and
both `planner.py` and `tools.py` need that seam. Putting it in either one makes
the other import it sideways; putting it in `loop.py` creates a cycle. It gets
its own module, and it is the only file in the package that imports `anthropic`.

**`ClaudeClient` is a `Protocol` with exactly one method.** Keeping the surface
to a single `complete()` is what makes `FakeClient` twelve lines instead of a
mock framework, and it is why the whole suite runs offline.

**The SDK's own retry is disabled (`max_retries=0`).** Retry policy lives in
the loop, where it is visible, bounded and testable. Two retry layers stacked on
each other turn a 30-second timeout into a two-minute one.

**Structured output uses forced tool calls, not JSON-in-prose.** Each LLM-backed
tool passes a real tool definition generated from its Pydantic model and pins
`tool_choice` to it. Parsing prose for JSON is the single most common source of
schema drift in agent code.

**Pydantic's `$defs`/`$ref` are inlined before the schema is sent.** The API
accepts refs, but an inlined schema is readable in a log line and in a tool
listing without resolving pointers.

**Errors are classified by exception class name, not by `isinstance`.** That
keeps `classify_error` importable without the SDK and lets tests raise a
locally-defined `RateLimitError` to exercise the transient path.

## Phase 3 — corpus and retrieval

**Chunks are markdown sections, and the citation id is `doc_id#section-slug`.**
The brief specifies a front-matter `id` per document; this keeps it and appends
the section. A citation that points at a paragraph can be checked by a reader; a
citation that points at a whole resume cannot.

**A BM25 score of zero is dropped, not returned as weak evidence.** Zero means
no lexical overlap at all. Returning it would let a downstream `met` verdict
"cite" a passage with nothing to do with the requirement.

**The stoplist is tiny and the tokenizer keeps `+`, `#`, `.` and `-`.** On a
corpus this small an aggressive stoplist removes signal, and `c++`, `ci/cd` and
`node.js` are exactly the tokens that matter.

**The evidence map is keyed on normalised requirement text.** The planner echoes
requirement strings back and will not always echo them byte-for-byte. Keying on
lowercased, whitespace-collapsed text stops a stray capital from silently
detaching a requirement from its evidence — which would make the guardrail
refuse everything, for the wrong reason.

## Phase 4 — the loop

**The planner's tool schemas are decorated copies carrying a required `why`.**
The trace table needs a "why chosen" column and the rationale has to come from
the model that made the choice. Decorating the planner's copy keeps the actual
tool signatures clean; the dispatcher strips `why` before the handler is called.

**Tools whose arguments are fixed for the run take no arguments from the
planner.** `parse_requirements`, `score_match` and `draft_bullets` read run
state; only `retrieve_evidence` takes model-chosen arguments. Round-tripping the
job description and the whole evidence map through the planner would cost tokens
on every step and hand the model an opportunity to paraphrase evidence in
transit. The planner still chooses which tool, which requirement to retrieve
for, how many passages, and when to stop.

**`finish` is a tool, and it gets a trace step.** Making termination an explicit
decision — with a stated reason — is more informative than a loop that simply
stops, and it shows up in the trace where a reviewer can see it.

**Two consecutive planner failures end the run as `error`.** One failure is
worth retrying through the normal path; two in a row means the planner call
itself is broken, and continuing just burns the step budget.

**Timeouts use a per-call single-worker `ThreadPoolExecutor` submitted through
`copy_context()`.** Python cannot interrupt an arbitrary blocking call, so the
bound is enforced by abandoning the future. `copy_context()` carries the
`request_id` `ContextVar` into the worker; without it every log line emitted
inside a tool call would be orphaned. The abandoned thread is a known cost and
is named in the README.

**Retry and repair are different policies.** Transient upstream errors get two
retries with exponential backoff. A schema-validation failure gets exactly one
repair round-trip carrying the validator's own message, then the step is
recorded as failed — retrying an identical prompt after a validation failure
just reproduces it.

## Phase 5 — scoring, drafting, guardrail

**The scoring model emits `cited_doc_ids`, never `Evidence` objects.** The
server rehydrates snippet and score from what retrieval actually returned. This
is the strongest single constraint in the system: a model with no way to author
an evidence object has no way to author a convincing fake one.

**Totals are computed, never taken from the model.** `hard_met`/`hard_total`
come from the verdicts in `_assemble_report`, and the scoring schema has no
field for a total. "Never emit an unexplained number" is enforced by the schema
having nowhere to put one.

**`met` or `partial` with no surviving citation is silently downgraded to
`gap`.** After filtering fabricated ids, a verdict with no evidence left is not
a met requirement regardless of what the model called it.

**Verdict order follows the requirements, not the model's response.** Missing
verdicts become explicit gaps rather than vanishing from the table.

**The guardrail sets `bullet=None` on rejection.** The brief specifies
`refused=True` and the exact refusal reason; clearing the text is the safe
reading, since a rejected draft displayed on the page is a draft someone will
copy. The discarded text goes into the structured log so the failure is
diagnosable.

**A single fabricated citation rejects the whole bullet, even alongside real
ones.** Partial grounding is not grounding: one real citation must not launder a
fake one sitting next to it.

## Phase 6 — observability

**`request_id` is a `ContextVar` injected by the log formatter.** Passing it as
an argument would make "every line carries a request_id" a rule that call sites
can forget. This way there is no call site that can.

**Metrics derive tool counters from the trace, in `observe_run`.** Counting at
call sites lets metrics drift away from what the trace shows a reviewer. One
source of truth.

**`/metrics` serves the alert thresholds alongside the values.** The documented
alert becomes a comparison rather than a computation someone has to reimplement
in a dashboard.

**The latency window is bounded at 500 runs and the max-steps window at 20.**
Twenty is the window the brief's alert is defined over, so the endpoint answers
that question directly.

**`/api/analyse` exists alongside the HTML form.** The same run, as JSON.
It is what `curl` and the metrics tests use, and it costs one route.

## Phase 7 — UI

**Server-rendered Jinja2, one stylesheet, zero JavaScript.** The brief bans
frameworks and build steps; the pages need no interactivity to do their job, so
they ship none. The result page works with JS disabled.

**The trace table is the widest, densest thing on the page,** with per-step
arguments and result summaries under each tool name and failed steps tinted.
The brief calls the trace the demo; the layout treats it that way.

**`docs/sample-report.html` is generated, not hand-written.**
`scripts/render_sample.py` runs the real agent against the scripted offline
client and inlines the stylesheet, so the README can link to a genuine render of
a genuine trace that costs nothing to regenerate and cannot drift from the code.

## Phase 8 — ship

**`requirements.txt` is runtime-only; `requirements-dev.txt` adds the test
tooling.** The brief's layout names `requirements.txt`, and this keeps it — but
shipping `pytest` and `ruff` inside a production image is the kind of detail a
reviewer notices.

**Single-stage Dockerfile.** Pure Python, no compiled dependencies, no build
step: a multi-stage build would add ceremony without removing a byte. The image
runs as an unprivileged user and keeps SQLite at `/data/runs.db` so it can be a
mounted volume without dragging the source tree along.

**The container `HEALTHCHECK` fails without an API key.** `/health` returns 503
when any dependency is unhealthy, and a key-less container genuinely cannot
serve a run. An orchestrator refusing to route traffic to it is correct
behaviour, and the probe body names the reason.

**CI has a second job that builds the image and curls `/health`.** A Dockerfile
that is never built in CI is a Dockerfile that is broken and nobody knows yet.

**Code stays 3.10-compatible (`timezone.utc`, no `StrEnum`), and `UP017` is
disabled in `ruff.toml` to keep it that way.** The container and CI both run
3.11; nothing is given up by also being runnable on 3.10.
