"""Step capture.

The trace is a first-class output of a run, not a debug log. It is rendered on
the result page, persisted with the run, and asserted on in tests. Failed steps
are recorded exactly like successful ones — a trace that quietly omits what went
wrong is worse than no trace, because it looks complete.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.models import Step


class Timer:
    __slots__ = ("_start", "elapsed_ms")

    def __init__(self) -> None:
        self._start = time.perf_counter()
        self.elapsed_ms = 0

    def stop(self) -> int:
        self.elapsed_ms = int((time.perf_counter() - self._start) * 1000)
        return self.elapsed_ms


@contextmanager
def timed() -> Iterator[Timer]:
    t = Timer()
    try:
        yield t
    finally:
        t.stop()


class Trace:
    def __init__(self) -> None:
        self.steps: list[Step] = []

    def add(
        self,
        *,
        tool: str,
        args: dict[str, Any] | None = None,
        result_summary: str = "",
        latency_ms: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        error: str | None = None,
        rationale: str | None = None,
    ) -> Step:
        step = Step(
            index=len(self.steps) + 1,
            tool=tool,
            args=args or {},
            result_summary=result_summary,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            error=error,
            rationale=rationale,
        )
        self.steps.append(step)
        return step

    def __len__(self) -> int:
        return len(self.steps)

    @property
    def total_tokens(self) -> int:
        return sum(s.input_tokens + s.output_tokens for s in self.steps)

    @property
    def failures(self) -> list[Step]:
        return [s for s in self.steps if s.error]

    def summary_for_planner(self, limit: int = 20) -> str:
        """A compact rendering of what has happened, fed back to the planner.

        Kept terse on purpose: the planner needs to know what was tried and how
        it went, not to re-read every retrieved passage."""
        if not self.steps:
            return "(nothing yet — this is the first step)"
        lines = []
        for s in self.steps[-limit:]:
            status = f"ERROR {s.error}" if s.error else s.result_summary
            lines.append(f"{s.index}. {s.tool}({_brief_args(s.args)}) -> {status}")
        return "\n".join(lines)


def _brief_args(args: dict[str, Any], limit: int = 70) -> str:
    parts = []
    for k, v in args.items():
        text = str(v)
        if len(text) > limit:
            text = text[:limit] + "…"
        parts.append(f"{k}={text!r}")
    return ", ".join(parts)
