"""Every boundary in this service is a Pydantic model.

That includes the boundaries you cannot see in a type signature: each of the
four tools returns one of these, parsed and validated out of raw model output.
If Claude emits something that does not fit, we get a ``ValidationError`` at the
edge rather than a plausible-looking dict three layers deep.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

Kind = Literal["hard", "preferred"]
Category = Literal["experience", "language", "platform", "domain", "soft", "education"]
Status = Literal["met", "partial", "gap"]
TerminatedReason = Literal["complete", "max_steps", "error"]


class ErrorKind(str, Enum):
    """The error taxonomy.

    Every failure in the system maps to exactly one of these. Metrics are
    counted by this enum, so "what is breaking" is answerable without grepping
    log text.
    """

    UPSTREAM_TIMEOUT = "upstream_timeout"
    UPSTREAM_RATE_LIMIT = "upstream_rate_limit"
    SCHEMA_VALIDATION = "schema_validation"
    TOOL_EXECUTION = "tool_execution"
    MAX_STEPS_EXCEEDED = "max_steps_exceeded"
    CORPUS_EMPTY = "corpus_empty"


class AgentError(Exception):
    """An error that has already been classified into the taxonomy."""

    def __init__(self, kind: ErrorKind, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.kind.value}: {self.message}"


# --------------------------------------------------------------------------
# Requirement extraction
# --------------------------------------------------------------------------


class Requirement(BaseModel):
    text: str
    kind: Kind
    category: Category
    years: float | None = None


class Requirements(BaseModel):
    role_title: str
    company: str | None = None
    requirements: list[Requirement] = Field(default_factory=list)

    def hard(self) -> list[Requirement]:
        return [r for r in self.requirements if r.kind == "hard"]


# --------------------------------------------------------------------------
# Retrieval and scoring
# --------------------------------------------------------------------------


class Evidence(BaseModel):
    doc_id: str
    snippet: str
    score: float


class RequirementVerdict(BaseModel):
    # The full Requirement, not just its text: the UI table needs kind and
    # category, and carrying the object avoids a lookup keyed on prose.
    requirement: Requirement
    status: Status
    evidence: list[Evidence] = Field(default_factory=list)
    rationale: str


class MatchReport(BaseModel):
    verdicts: list[RequirementVerdict] = Field(default_factory=list)
    hard_met: int = 0
    hard_total: int = 0
    summary: str = ""

    def gaps(self) -> list[Requirement]:
        return [v.requirement for v in self.verdicts if v.status in ("gap", "partial")]


# --------------------------------------------------------------------------
# Drafting
# --------------------------------------------------------------------------


class BulletSuggestion(BaseModel):
    requirement_text: str
    bullet: str | None = None
    refused: bool = False
    refusal_reason: str | None = None
    grounded_in: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Trace
# --------------------------------------------------------------------------


class Step(BaseModel):
    index: int
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    result_summary: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    # Additive: the brief's trace table (section 10) has a "why chosen" column,
    # which is the planner's own stated reason for this step.
    rationale: str | None = None


class AgentResult(BaseModel):
    report: MatchReport
    suggestions: list[BulletSuggestion] = Field(default_factory=list)
    trace: list[Step] = Field(default_factory=list)
    total_latency_ms: int = 0
    total_tokens: int = 0
    terminated_reason: TerminatedReason = "complete"


# --------------------------------------------------------------------------
# Planner output
# --------------------------------------------------------------------------


class PlannedAction(BaseModel):
    """What the planner decided to do next.

    ``tool`` is ``None`` when the planner chose to finish.
    """

    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def is_finish(self) -> bool:
        return self.tool is None
