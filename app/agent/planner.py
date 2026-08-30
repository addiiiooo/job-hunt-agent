"""The planner: one model call that decides the next action.

Nothing here hard-codes an order. The planner is given the goal, the real tool
schemas, and everything that has happened so far, and it picks — including
picking ``finish``. That is the whole point: the interesting failure modes of an
agent (looping, giving up early, retrieving for the wrong requirement) only
exist if the model is actually choosing, and only show up in a trace if the
trace records the choice.
"""

from __future__ import annotations

import copy
from typing import Any, Sequence

from app.agent.client import ClaudeClient
from app.config import Settings
from app.models import AgentError, ErrorKind, PlannedAction, Step

PLANNER_SYSTEM = """You are the planner for a job-application match agent.

Your job is to decide the single next action, then stop. You will be called
again with the result.

The intended shape of a run, which you may deviate from when the trace warrants
it:

1. parse_requirements once, to turn the job description into atomic requirements.
2. retrieve_evidence for each requirement worth scoring. Retrieval is cheap and
   makes no model call — prefer retrieving for every requirement over guessing.
3. score_match once, after retrieval, to produce the verdicts.
4. draft_bullets once, for the requirements that came back partial or gap.
5. finish.

Rules:
- Never call score_match before parse_requirements has succeeded.
- Never repeat an identical call that already succeeded; read the trace first.
- If a step failed, decide deliberately: retry it only if a retry could
  plausibly succeed, otherwise move on or finish. Two identical failures mean
  stop trying that call.
- Retrieval returning nothing is information, not a failure. It means the
  corpus has no evidence for that requirement, which is a legitimate result.
- finish when the report and the bullets exist, or when no remaining call can
  improve the answer.

Every call must include a short `why`: one sentence, concrete, naming what you
expect to learn. It is shown to a human in the trace."""

FINISH_TOOL: dict[str, Any] = {
    "name": "finish",
    "description": (
        "End the run. Use when the match report and the bullet suggestions are "
        "both done, or when no further call can improve the result."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "why": {
                "type": "string",
                "description": "One sentence: why the run is complete.",
            }
        },
        "required": ["why"],
    },
}

_WHY_PROPERTY = {
    "type": "string",
    "description": (
        "One sentence explaining why this call is the right next step, shown to "
        "a human in the trace."
    ),
}


def with_why(schema: dict[str, Any]) -> dict[str, Any]:
    """Add a mandatory ``why`` to a tool schema, for the planner's copy only.

    The tools themselves have no business taking a rationale argument, so the
    planner gets a decorated copy and the dispatcher strips ``why`` back off
    before the tool is called.
    """
    decorated = copy.deepcopy(schema)
    props = decorated.setdefault("input_schema", {}).setdefault("properties", {})
    props["why"] = _WHY_PROPERTY
    required = decorated["input_schema"].setdefault("required", [])
    if "why" not in required:
        required.append("why")
    return decorated


def next_action(
    goal: str,
    tool_schemas: Sequence[dict[str, Any]],
    trace_so_far: Sequence[Step],
    *,
    client: ClaudeClient,
    settings: Settings,
) -> PlannedAction:
    """Decide the next tool call, or finish."""
    from app.agent.trace import Trace

    scratch = Trace()
    scratch.steps = list(trace_so_far)

    tools = [with_why(s) for s in tool_schemas] + [FINISH_TOOL]

    user = (
        f"{goal}\n\n"
        f"<trace steps=\"{len(trace_so_far)}\">\n"
        f"{scratch.summary_for_planner()}\n"
        f"</trace>\n\n"
        "Choose exactly one next action."
    )

    reply = client.complete(
        system=PLANNER_SYSTEM,
        messages=[{"role": "user", "content": user}],
        tools=tools,
        tool_choice={"type": "any"},
        max_tokens=1024,
        timeout=settings.tool_timeout_s,
    )

    call = reply.first_tool_call
    if call is None:
        raise AgentError(
            ErrorKind.SCHEMA_VALIDATION,
            f"planner returned no tool call (stop_reason={reply.stop_reason})",
        )

    args = dict(call.input or {})
    why = str(args.pop("why", "") or "").strip()

    if call.name == "finish":
        return PlannedAction(
            tool=None,
            args={},
            rationale=why or "planner chose to finish",
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
        )

    return PlannedAction(
        tool=call.name,
        args=args,
        rationale=why,
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
    )
