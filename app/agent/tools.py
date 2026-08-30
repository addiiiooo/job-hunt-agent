"""The four tools.

Each one has a typed JSON schema derived from the Pydantic model it returns,
and each LLM-backed tool forces the model into that schema with a real tool
definition and ``tool_choice``. The model does not "return JSON" in prose that
we then hope to parse; it fills in a tool call, and we validate the result.
"""

from __future__ import annotations

import copy
from typing import Any

from app.agent.client import ClaudeClient
from app.config import Settings
from app.models import (
    AgentError,
    ErrorKind,
    Requirements,
)


# ---------------------------------------------------------------------------
# JSON schema helpers
# ---------------------------------------------------------------------------


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Flatten Pydantic's ``$defs``/``$ref`` into a self-contained schema.

    The API accepts refs, but an inlined schema is what you want in a log line
    and in the tool listing on the trace page — you can read it without
    resolving pointers.
    """
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                key = node["$ref"].rsplit("/", 1)[-1]
                target = walk(copy.deepcopy(defs.get(key, {})))
                for k, v in node.items():
                    if k != "$ref":
                        target[k] = v
                return target
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def schema_of(model: type) -> dict[str, Any]:
    return _inline_refs(model.model_json_schema())


def _emit_tool(name: str, description: str, model: type) -> dict[str, Any]:
    return {"name": name, "description": description, "input_schema": schema_of(model)}


# ---------------------------------------------------------------------------
# Shared call helper
# ---------------------------------------------------------------------------


def structured_call(
    client: ClaudeClient,
    settings: Settings,
    *,
    system: str,
    user: str,
    emit: dict[str, Any],
    model_cls: type,
    max_tokens: int = 4096,
    repair_note: str | None = None,
):
    """One model call constrained to exactly one tool, validated on the way out.

    Returns ``(instance, input_tokens, output_tokens)``. A validation failure
    raises so the loop can decide whether to repair or record a failed step.
    """
    content = user if not repair_note else f"{user}\n\n{repair_note}"
    reply = client.complete(
        system=system,
        messages=[{"role": "user", "content": content}],
        tools=[emit],
        tool_choice={"type": "tool", "name": emit["name"]},
        max_tokens=max_tokens,
        timeout=settings.tool_timeout_s,
    )
    call = reply.first_tool_call
    if call is None:
        raise AgentError(
            ErrorKind.SCHEMA_VALIDATION,
            f"model returned no {emit['name']} tool call (stop_reason={reply.stop_reason})",
        )
    instance = model_cls.model_validate(call.input)
    return instance, reply.input_tokens, reply.output_tokens


# ---------------------------------------------------------------------------
# Tool 1 — parse_requirements
# ---------------------------------------------------------------------------

PARSE_SYSTEM = """You extract structured hiring requirements from job descriptions.

Rules, in priority order:

1. ATOMIC. Split every compound requirement into separate requirements. "3+
   years of Python and Go with Kubernetes in production" is three
   requirements, not one. A reader must be able to answer "do I have this?"
   with a yes or no for each.
2. HARD vs PREFERRED. "required", "must have", "you have", and bare list items
   under a Requirements heading are hard. "nice to have", "bonus", "preferred",
   "a plus", "ideally" are preferred. When a section header marks the whole
   block as preferred, every item inside it inherits that.
3. YEARS. Normalise any stated duration to a number of years: "3+ years" -> 3,
   "18 months" -> 1.5, "5-7 years" -> 5 (take the floor of a range). Leave
   years null when the text states none. Never invent a number.
4. CATEGORY. language = a programming language. platform = a tool, framework,
   cloud, or database. experience = a level, a scope, or years of general
   practice. domain = business or industry knowledge. education = a degree or
   certification. soft = communication, collaboration, ownership.
5. VERBATIM-ISH. Keep requirement text short and close to the posting's own
   words. Do not editorialise, do not add requirements the posting does not
   state, and do not merge duplicates that are genuinely separate.

Extract the role title and the company when the posting names them."""

EMIT_REQUIREMENTS = _emit_tool(
    "emit_requirements",
    "Return the structured requirements extracted from the job description.",
    Requirements,
)


def parse_requirements(
    jd_text: str,
    *,
    client: ClaudeClient,
    settings: Settings,
    repair_note: str | None = None,
) -> tuple[Requirements, int, int]:
    """Extract hard vs preferred requirements, atomised, with years normalised."""
    if not jd_text or not jd_text.strip():
        raise AgentError(ErrorKind.TOOL_EXECUTION, "job description is empty")
    return structured_call(
        client,
        settings,
        system=PARSE_SYSTEM,
        user=f"<job_description>\n{jd_text.strip()}\n</job_description>",
        emit=EMIT_REQUIREMENTS,
        model_cls=Requirements,
        repair_note=repair_note,
    )
