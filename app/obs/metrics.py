"""In-process metrics.

No Prometheus client, no push gateway, no external infrastructure — the brief
rules those out and a single-process app does not need them. Counters live in
memory behind a lock and are served as JSON from /metrics.

The latency window is bounded, which means /metrics answers "how is it behaving
now", not "how has it behaved forever". That is the question you actually ask
during an incident.
"""

from __future__ import annotations

import threading
from collections import Counter, deque
from typing import Any

from app.models import AgentResult, ErrorKind

LATENCY_WINDOW = 500
RECENT_RUNS_WINDOW = 20  # the window the documented max_steps alert is defined over


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile. Exact, no interpolation, no numpy."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-pct * len(ordered) // 100))))
    return round(ordered[rank - 1], 2)


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._runs = 0
            self._run_reasons: Counter[str] = Counter()
            self._recent_reasons: deque[str] = deque(maxlen=RECENT_RUNS_WINDOW)
            self._tool_calls: Counter[str] = Counter()
            self._tool_failures: Counter[str] = Counter()
            self._errors: Counter[str] = Counter()
            self._latencies: deque[float] = deque(maxlen=LATENCY_WINDOW)
            self._input_tokens = 0
            self._output_tokens = 0
            self._guardrail_rejections = 0

    # -- recording -----------------------------------------------------------

    def record_tool(self, tool: str, error: str | None = None) -> None:
        with self._lock:
            self._tool_calls[tool] += 1
            if error:
                self._tool_failures[tool] += 1
                self._errors[error] += 1

    def record_error(self, kind: ErrorKind | str) -> None:
        with self._lock:
            self._errors[kind.value if isinstance(kind, ErrorKind) else str(kind)] += 1

    def record_guardrail_rejections(self, n: int) -> None:
        if n <= 0:
            return
        with self._lock:
            self._guardrail_rejections += n

    def observe_run(self, result: AgentResult) -> None:
        """One call per completed run. Tool counters come from the trace, so
        they cannot drift away from what the trace shows a reviewer."""
        with self._lock:
            self._runs += 1
            self._run_reasons[result.terminated_reason] += 1
            self._recent_reasons.append(result.terminated_reason)
            self._latencies.append(float(result.total_latency_ms))
            for step in result.trace:
                self._tool_calls[step.tool] += 1
                self._input_tokens += step.input_tokens
                self._output_tokens += step.output_tokens
                if step.error:
                    self._tool_failures[step.tool] += 1
                    self._errors[step.error] += 1
            if result.terminated_reason == "max_steps":
                self._errors[ErrorKind.MAX_STEPS_EXCEEDED.value] += 1

    # -- reporting -----------------------------------------------------------

    def max_steps_rate_recent(self) -> float:
        with self._lock:
            if not self._recent_reasons:
                return 0.0
            hits = sum(1 for r in self._recent_reasons if r == "max_steps")
            return round(hits / len(self._recent_reasons), 4)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            latencies = list(self._latencies)
            recent = list(self._recent_reasons)
            payload = {
                "runs": {
                    "total": self._runs,
                    "by_terminated_reason": dict(self._run_reasons),
                },
                "latency_ms": {
                    "p50": _percentile(latencies, 50),
                    "p95": _percentile(latencies, 95),
                    "max": round(max(latencies), 2) if latencies else 0.0,
                    "window": len(latencies),
                },
                "tools": {
                    "calls": dict(self._tool_calls),
                    "failures": dict(self._tool_failures),
                },
                "tokens": {
                    "input": self._input_tokens,
                    "output": self._output_tokens,
                    "total": self._input_tokens + self._output_tokens,
                },
                "errors": {kind.value: self._errors.get(kind.value, 0) for kind in ErrorKind},
                "guardrail": {"rejected_suggestions": self._guardrail_rejections},
            }
        payload["alerts"] = {
            "p95_latency_ms": payload["latency_ms"]["p95"],
            "p95_latency_threshold_ms": 30000,
            "max_steps_rate_last_20": (
                round(sum(1 for r in recent if r == "max_steps") / len(recent), 4) if recent else 0.0
            ),
            "max_steps_rate_threshold": 0.10,
            "window_runs": len(recent),
        }
        return payload


METRICS = Metrics()
