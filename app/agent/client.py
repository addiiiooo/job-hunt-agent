"""The injection seam for Claude.

Everything that talks to the model goes through ``ClaudeClient.complete``.
The real implementation wraps the ``anthropic`` SDK; tests inject a scripted
fake. Nothing else in the agent imports ``anthropic``, so the whole test suite
runs offline and deterministically.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.models import ErrorKind


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str | None = None

    @property
    def first_tool_call(self) -> ToolCall | None:
        return self.tool_calls[0] if self.tool_calls else None


class ClaudeClient(Protocol):
    """The one method the agent needs. Keeping it to one method is what makes
    the fake three lines long instead of a mock framework."""

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        max_tokens: int = 2048,
        timeout: float = 30.0,
    ) -> LLMReply: ...


class AnthropicClient:
    """Single provider, called directly. No abstraction layer over providers —
    there is one provider, and pretending otherwise costs more than it buys."""

    def __init__(self, api_key: str, model: str):
        import anthropic  # imported here so the module is importable without the SDK

        self._client = anthropic.Anthropic(api_key=api_key, max_retries=0)
        self._model = model

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        max_tokens: int = 2048,
        timeout: float = 30.0,
    ) -> LLMReply:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": list(messages),
            "timeout": timeout,
        }
        if tools:
            kwargs["tools"] = list(tools)
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        msg = self._client.messages.create(**kwargs)

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in msg.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input or {})))

        usage = getattr(msg, "usage", None)
        return LLMReply(
            text="\n".join(text_parts).strip(),
            tool_calls=calls,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            stop_reason=getattr(msg, "stop_reason", None),
        )


# ---------------------------------------------------------------------------
# Error classification — every upstream failure lands in the taxonomy.
# ---------------------------------------------------------------------------

# Retry scope: HTTP 429 and 5xx, plus the transport-level failures that are
# indistinguishable from a 5xx from the caller's side (a socket that never
# answered, a request that ran past TOOL_TIMEOUT_S). A 4xx other than 429 is a
# bug in our request and retrying it just reproduces it.
_RETRYABLE_NAMES = {
    "APITimeoutError",
    "APIConnectionError",
    "DeadlineExceededError",
    "TimeoutError",
    "RateLimitError",
    "InternalServerError",
    "OverloadedError",
    "ServiceUnavailableError",
    "RetryableError",
}


def classify_error(exc: BaseException) -> ErrorKind:
    name = type(exc).__name__
    if name in ("APITimeoutError", "DeadlineExceededError", "TimeoutError", "FuturesTimeoutError"):
        return ErrorKind.UPSTREAM_TIMEOUT
    if name == "RateLimitError":
        return ErrorKind.UPSTREAM_RATE_LIMIT
    if name == "ValidationError":
        return ErrorKind.SCHEMA_VALIDATION
    from app.models import AgentError

    if isinstance(exc, AgentError):
        return exc.kind
    return ErrorKind.TOOL_EXECUTION


def is_transient(exc: BaseException) -> bool:
    """Worth retrying: 429 and 5xx only.

    The status code is authoritative when the SDK gives us one, so a 400 or a
    401 is never retried however it is named. A schema-validation failure is not
    transient either — retrying an identical prompt reproduces it, and the loop
    repairs that one differently.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or 500 <= status < 600
    if type(exc).__name__ in _RETRYABLE_NAMES:
        return True
    return classify_error(exc) in (ErrorKind.UPSTREAM_TIMEOUT, ErrorKind.UPSTREAM_RATE_LIMIT)


def scripted_tool_call(
    name: str, payload: dict[str, Any], *, input_tokens: int = 0, output_tokens: int = 0
) -> LLMReply:
    """Build the reply the API would send if the model had called ``name``."""
    scripted_tool_call._n = getattr(scripted_tool_call, "_n", 0) + 1  # type: ignore[attr-defined]
    return LLMReply(
        tool_calls=[ToolCall(id=f"toolu_{scripted_tool_call._n:04d}", name=name, input=payload)],  # type: ignore[attr-defined]
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        stop_reason="tool_use",
    )


class ScriptedClient:
    """A client that replays a fixed list of replies instead of calling the API.

    It is what makes the test suite deterministic and offline, and it is also a
    real runtime mode: ``LLM_CLIENT=scripted`` serves the app with canned model
    responses over live retrieval, so the deployment is demonstrable without a
    key and without spending anything.

    Give it a list of ``LLMReply`` objects (or callables taking the request
    kwargs and returning one). Each ``complete`` pops the next.
    """

    def __init__(self, replies: Sequence[Any]):
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> LLMReply:
        self.calls.append(kwargs)
        if not self.replies:
            raise AssertionError("ScriptedClient exhausted: the agent made more calls than scripted")
        nxt = self.replies.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        if callable(nxt):
            return nxt(kwargs)
        return nxt


# Kept so older call sites and notebooks do not break on the rename.
FakeClient = ScriptedClient
