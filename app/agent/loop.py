"""The orchestrator.

``run`` is a bounded loop around one decision: ask the planner what to do next,
do it, record what happened, repeat. The order of tool calls is not written
here — it is chosen by the model, every step, given the trace so far.

Everything the loop does that is not "call the planner" is error policy:
a wall-clock timeout per call, two retries with exponential backoff on
transient upstream failures, and exactly one schema-repair round-trip when the
model returns something that does not validate. A step that exhausts all of
that is recorded as a failed step and the planner is told about it, because a
planner that can see its own failures can route around them.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextvars import copy_context
from dataclasses import dataclass, field
from typing import Any

from app.agent import guardrail as guardrail_mod
from app.agent import planner as planner_mod
from app.agent import tools as tools_mod
from app.agent.client import ClaudeClient, classify_error, is_transient
from app.agent.tools import ev_key
from app.agent.trace import Trace, timed
from app.config import Settings
from app.models import (
    AgentError,
    AgentResult,
    BulletSuggestion,
    ErrorKind,
    Evidence,
    MatchReport,
    PlannedAction,
    Requirements,
)

MAX_TRANSIENT_RETRIES = 2
BACKOFF_BASE_S = 0.5
JD_PREVIEW_CHARS = 700


# ---------------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------------


@dataclass
class RunState:
    jd_text: str
    requirements: Requirements | None = None
    evidence_map: dict[str, list[Evidence]] = field(default_factory=dict)
    report: MatchReport | None = None
    suggestions: list[BulletSuggestion] | None = None
    guardrail_rejections: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ToolSpec:
    """A tool as the loop sees it: a schema for the planner and a handler.

    ``handler(state, args, ctx, repair_note) -> (summary, in_tok, out_tok)``.
    ``repairable`` is False for tools that make no model call, since there is
    nothing to repair when no model was involved.
    """

    schema: dict[str, Any]
    handler: Callable[..., tuple[str, int, int]]
    repairable: bool = True


@dataclass
class Context:
    client: ClaudeClient
    settings: Settings
    index: Any
    emit: Callable[[str, dict[str, Any]], None] = lambda *_a, **_k: None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _h_parse_requirements(state: RunState, args: dict, ctx: Context, repair_note: str | None):
    reqs, i, o = tools_mod.parse_requirements(
        state.jd_text, client=ctx.client, settings=ctx.settings, repair_note=repair_note
    )
    state.requirements = reqs
    hard = len(reqs.hard())
    ctx.emit(
        "requirements_parsed",
        {
            "role_title": reqs.role_title,
            "company": reqs.company,
            "requirement_count": len(reqs.requirements),
            "hard_count": hard,
        },
    )
    return (
        f"{len(reqs.requirements)} requirements ({hard} hard, "
        f"{len(reqs.requirements) - hard} preferred) for '{reqs.role_title}'",
        i,
        o,
    )


def _h_retrieve_evidence(state: RunState, args: dict, ctx: Context, repair_note: str | None):
    requirement = str(args.get("requirement", "")).strip()
    k = int(args.get("k") or 5)
    hits = tools_mod.retrieve_evidence(requirement, k=k, index=ctx.index)
    state.evidence_map[ev_key(requirement)] = hits
    if not hits:
        return "no matching passages (corpus has no lexical overlap)", 0, 0
    relevant = sum(1 for h in hits if h.relevant)
    top = hits[0]
    return (
        f"{len(hits)} passages, {relevant} above threshold, top {top.doc_id}",
        0,
        0,
    )


def _h_score_match(state: RunState, args: dict, ctx: Context, repair_note: str | None):
    if state.requirements is None:
        raise AgentError(
            ErrorKind.TOOL_EXECUTION,
            "cannot score before parse_requirements has succeeded",
        )
    report, i, o = tools_mod.score_match(
        state.requirements,
        state.evidence_map,
        client=ctx.client,
        settings=ctx.settings,
        repair_note=repair_note,
    )
    state.report = report
    return f"{report.hard_met}/{report.hard_total} hard requirements met", i, o


def _h_draft_bullets(state: RunState, args: dict, ctx: Context, repair_note: str | None):
    if state.report is None:
        raise AgentError(
            ErrorKind.TOOL_EXECUTION,
            "cannot draft bullets before score_match has produced a report",
        )
    gaps = state.report.gaps()
    drafted, i, o = tools_mod.draft_bullets(
        gaps,
        state.evidence_map,
        client=ctx.client,
        settings=ctx.settings,
        repair_note=repair_note,
    )

    # The guardrail runs here: after drafting, before anything is shown.
    outcome = guardrail_mod.enforce(drafted, state.evidence_map)
    state.suggestions = outcome.suggestions
    state.guardrail_rejections = outcome.rejected

    if outcome.rejected_count:
        ctx.emit(
            "guardrail_rejected",
            {"count": outcome.rejected_count, "rejections": outcome.rejected},
        )

    refused = sum(1 for s in outcome.suggestions if s.refused)
    summary = f"{len(outcome.suggestions)} suggestions, {refused} refused"
    if outcome.rejected_count:
        summary += f" ({outcome.rejected_count} rewritten by the grounding validator)"
    return summary, i, o


def build_registry(ctx: Context) -> dict[str, ToolSpec]:
    return {
        "parse_requirements": ToolSpec(tools_mod.PARSE_TOOL_SCHEMA, _h_parse_requirements),
        "retrieve_evidence": ToolSpec(
            tools_mod.RETRIEVE_TOOL_SCHEMA, _h_retrieve_evidence, repairable=False
        ),
        "score_match": ToolSpec(tools_mod.SCORE_TOOL_SCHEMA, _h_score_match),
        "draft_bullets": ToolSpec(tools_mod.DRAFT_TOOL_SCHEMA, _h_draft_bullets),
    }


# ---------------------------------------------------------------------------
# Error policy
# ---------------------------------------------------------------------------


def _with_timeout(fn: Callable[[], Any], seconds: float) -> Any:
    """Wall-clock bound on one call.

    A dedicated single-worker executor per call is not free, but it is the only
    way to bound an arbitrary blocking callable without cooperative
    cancellation. On timeout the worker is abandoned (`shutdown(wait=False)`)
    and the SDK's own timeout eventually reaps it — see Known limitations.
    """
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        # copy_context so the request_id ContextVar survives the hop into the
        # worker thread; without it every log line from inside a tool call
        # would be orphaned.
        future = pool.submit(copy_context().run, fn)
        try:
            return future.result(timeout=seconds)
        except FutureTimeout:
            future.cancel()
            raise AgentError(
                ErrorKind.UPSTREAM_TIMEOUT, f"call exceeded TOOL_TIMEOUT_S ({seconds}s)"
            ) from None
    finally:
        pool.shutdown(wait=False)


def call_with_policy(
    fn: Callable[[str | None], Any],
    settings: Settings,
    *,
    repairable: bool,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Timeout + bounded transient retry + one schema repair.

    ``fn`` takes a repair note (``None`` on the first attempt) so the caller
    decides how to feed a validation error back to the model.
    """
    transient_used = 0
    repair_used = False
    repair_note: str | None = None

    while True:
        try:
            return _with_timeout(lambda note=repair_note: fn(note), settings.tool_timeout_s)
        except Exception as exc:
            kind = classify_error(exc)

            if kind is ErrorKind.SCHEMA_VALIDATION and repairable and not repair_used:
                repair_used = True
                repair_note = (
                    "Your previous response did not satisfy the required schema. "
                    f"The validator said:\n{_short(exc)}\n"
                    "Return the same information again, this time matching the tool "
                    "schema exactly. Do not add fields, and do not omit required ones."
                )
                continue

            if is_transient(exc) and transient_used < MAX_TRANSIENT_RETRIES:
                sleep(BACKOFF_BASE_S * (2**transient_used))
                transient_used += 1
                continue

            raise


def _short(exc: BaseException, limit: int = 400) -> str:
    text = str(exc).strip().replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


def _goal(state: RunState, settings: Settings) -> str:
    jd = state.jd_text.strip()
    preview = jd[:JD_PREVIEW_CHARS] + ("…" if len(jd) > JD_PREVIEW_CHARS else "")

    lines = [
        "GOAL: score this candidate against the job description using only "
        "evidence retrieved from their own corpus, then draft resume bullets "
        "for the requirements that are not fully met — refusing to draft where "
        "the corpus has no evidence.",
        "",
        f"<job_description chars={len(jd)}>\n{preview}\n</job_description>",
        "",
        "<state>",
    ]

    if state.requirements is None:
        lines.append("requirements: NOT PARSED YET")
    else:
        reqs = state.requirements
        lines.append(
            f"requirements: parsed — {len(reqs.requirements)} total, "
            f"{len(reqs.hard())} hard, role '{reqs.role_title}'"
        )
        for r in reqs.requirements:
            got = state.evidence_map.get(ev_key(r.text))
            if got is None:
                mark = "no evidence retrieved yet"
            elif not got:
                mark = "retrieved: nothing in corpus"
            else:
                mark = f"retrieved: {len(got)} passages"
            lines.append(f"  - [{r.kind}] {r.text} — {mark}")

    lines.append(f"report: {'built' if state.report else 'not built'}")
    lines.append(
        "bullets: "
        + ("not drafted" if state.suggestions is None else f"{len(state.suggestions)} drafted")
    )
    lines.append(f"step budget: {settings.max_steps} steps total</state>")
    return "\n".join(lines)


def run(
    jd_text: str,
    *,
    client: ClaudeClient,
    index: Any,
    settings: Settings,
    request_id: str = "-",
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> AgentResult:
    """Run the agent to completion, MAX_STEPS, or unrecoverable error."""
    emit = on_event or (lambda *_args, **_kw: None)
    ctx = Context(client=client, settings=settings, index=index, emit=emit)
    registry = build_registry(ctx)
    schemas = [spec.schema for spec in registry.values()]

    state = RunState(jd_text=jd_text)
    trace = Trace()
    terminated: str = "complete"
    planner_failures = 0

    emit("run_start", {"request_id": request_id, "max_steps": settings.max_steps})

    while len(trace) < settings.max_steps:
        # ---- decide -------------------------------------------------------
        with timed() as t:
            try:
                action: PlannedAction = call_with_policy(
                    lambda _note: planner_mod.next_action(
                        _goal(state, settings),
                        schemas,
                        trace.steps,
                        client=client,
                        settings=settings,
                    ),
                    settings,
                    repairable=False,
                )
            except Exception as exc:
                kind = classify_error(exc)
                trace.add(
                    tool="planner",
                    result_summary="planner could not decide a next action",
                    latency_ms=t.stop(),
                    error=kind.value,
                    rationale=_short(exc),
                )
                emit("step_failed", {"request_id": request_id, "tool": "planner", "error": kind.value})
                planner_failures += 1
                if planner_failures >= 2:
                    terminated = "error"
                    break
                continue
        planner_failures = 0
        plan_ms = t.elapsed_ms

        # ---- finish -------------------------------------------------------
        if action.is_finish:
            trace.add(
                tool="finish",
                result_summary="planner ended the run",
                latency_ms=plan_ms,
                input_tokens=action.input_tokens,
                output_tokens=action.output_tokens,
                rationale=action.rationale,
            )
            terminated = "complete"
            emit("run_finish", {"request_id": request_id, "reason": "complete"})
            break

        # ---- act ----------------------------------------------------------
        spec = registry.get(action.tool or "")
        if spec is None:
            trace.add(
                tool=action.tool or "unknown",
                args=action.args,
                result_summary=f"no such tool: {action.tool!r}",
                latency_ms=plan_ms,
                input_tokens=action.input_tokens,
                output_tokens=action.output_tokens,
                error=ErrorKind.TOOL_EXECUTION.value,
                rationale=action.rationale,
            )
            emit("step_failed", {"request_id": request_id, "tool": action.tool, "error": "tool_execution"})
            continue

        with timed() as t:
            try:
                summary, in_tok, out_tok = call_with_policy(
                    lambda note, _spec=spec, _args=action.args: _spec.handler(
                        state, _args, ctx, note
                    ),
                    settings,
                    repairable=spec.repairable,
                )
                error_value = None
            except Exception as exc:
                kind = classify_error(exc)
                summary, in_tok, out_tok = _short(exc), 0, 0
                error_value = kind.value

        trace.add(
            tool=action.tool or "unknown",
            args=action.args,
            result_summary=summary,
            latency_ms=plan_ms + t.elapsed_ms,
            input_tokens=action.input_tokens + in_tok,
            output_tokens=action.output_tokens + out_tok,
            error=error_value,
            rationale=action.rationale,
        )
        emit(
            "step_failed" if error_value else "step_ok",
            {
                "request_id": request_id,
                "step": len(trace),
                "tool": action.tool,
                "latency_ms": plan_ms + t.elapsed_ms,
                "tokens": action.input_tokens + in_tok + action.output_tokens + out_tok,
                "error": error_value,
                "summary": summary,
            },
        )
    else:
        terminated = "max_steps"
        emit("run_finish", {"request_id": request_id, "reason": "max_steps"})

    report = state.report or _empty_report(state, terminated)
    # The run's latency is the sum of what it spent on steps, each measured
    # with perf_counter around its dispatch. Wall-clock would include loop
    # overhead the trace cannot account for, and a total that does not
    # reconcile with the rows above it is a total nobody can audit.
    total_ms = sum(step.latency_ms for step in trace.steps)

    return AgentResult(
        report=report,
        suggestions=state.suggestions or [],
        trace=trace.steps,
        total_latency_ms=total_ms,
        total_tokens=trace.total_tokens,
        terminated_reason=terminated,  # type: ignore[arg-type]
    )


def _empty_report(state: RunState, terminated: str) -> MatchReport:
    """A run that produced no verdicts still returns a report, and says why."""
    hard_total = len(state.requirements.hard()) if state.requirements else 0
    reason = {
        "max_steps": "the run hit its step budget before scoring finished",
        "error": "the run failed before scoring could complete",
    }.get(terminated, "no verdicts were produced")
    return MatchReport(verdicts=[], hard_met=0, hard_total=hard_total, summary=f"No match report: {reason}.")
