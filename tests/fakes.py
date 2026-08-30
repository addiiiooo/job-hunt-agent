"""Helpers for scripting the injected Claude client in tests."""

from __future__ import annotations

from typing import Any

from app.agent.client import LLMReply, ToolCall

_counter = {"n": 0}


def tool_reply(name: str, payload: dict[str, Any], *, in_tok: int = 120, out_tok: int = 60) -> LLMReply:
    """An LLMReply that looks like the model filled in one tool call."""
    _counter["n"] += 1
    return LLMReply(
        text="",
        tool_calls=[ToolCall(id=f"toolu_{_counter['n']:04d}", name=name, input=payload)],
        input_tokens=in_tok,
        output_tokens=out_tok,
        stop_reason="tool_use",
    )


SAMPLE_REQUIREMENTS = {
    "role_title": "Backend Engineer, Payments Platform",
    "company": "Northwind Technologies",
    "requirements": [
        {"text": "3+ years professional backend engineering experience", "kind": "hard",
         "category": "experience", "years": 3},
        {"text": "Strong Python", "kind": "hard", "category": "language", "years": None},
        {"text": "Production PostgreSQL including query plans and indexing", "kind": "hard",
         "category": "platform", "years": None},
        {"text": "Building and consuming REST APIs at scale", "kind": "hard",
         "category": "experience", "years": None},
        {"text": "Docker", "kind": "hard", "category": "platform", "years": None},
        {"text": "CI/CD pipeline experience", "kind": "hard", "category": "platform", "years": None},
        {"text": "Bachelor's degree in Computer Science or equivalent", "kind": "hard",
         "category": "education", "years": None},
        {"text": "Kubernetes in production", "kind": "preferred", "category": "platform",
         "years": None},
        {"text": "Payments, ledgers or double-entry accounting knowledge", "kind": "preferred",
         "category": "domain", "years": None},
        {"text": "Terraform or another infrastructure-as-code tool", "kind": "preferred",
         "category": "platform", "years": None},
        {"text": "On-call ownership of a Tier-1 service", "kind": "preferred",
         "category": "experience", "years": None},
    ],
}


def planner_reply(tool: str, why: str = "because the trace says so", **args: Any) -> LLMReply:
    """A planner decision. ``tool='finish'`` ends the run."""
    payload: dict[str, Any] = {"why": why}
    payload.update(args)
    return tool_reply(tool, payload, in_tok=200, out_tok=40)


class Boom(Exception):
    """A non-transient failure."""


class RateLimitError(Exception):
    """Named to match the SDK class, which is what classify_error keys on."""


class APITimeoutError(Exception):
    pass


class SlowClient:
    """A client that always takes longer than the timeout allows."""

    def __init__(self, delay: float = 5.0):
        self.delay = delay
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> LLMReply:
        import time as _time

        self.calls.append(kwargs)
        _time.sleep(self.delay)
        return LLMReply(text="too late")


# A deliberately small requirement set, used to script a full four-tool run.
R_PYTHON = "Strong Python"
R_POSTGRES = "Production PostgreSQL including query plans and indexing"
R_K8S = "Kubernetes in production"

SMALL_REQUIREMENTS = {
    "role_title": "Backend Engineer, Payments Platform",
    "company": "Northwind Technologies",
    "requirements": [
        {"text": R_PYTHON, "kind": "hard", "category": "language", "years": None},
        {"text": R_POSTGRES, "kind": "hard", "category": "platform", "years": None},
        {"text": R_K8S, "kind": "preferred", "category": "platform", "years": None},
    ],
}
