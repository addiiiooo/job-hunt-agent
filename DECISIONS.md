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
