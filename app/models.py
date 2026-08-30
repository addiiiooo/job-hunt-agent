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
RefusalSource = Literal["model", "validator"]


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
    """One retrieved passage.

    ``score`` is the raw BM25 score. It is unbounded and not comparable across
    queries, so it stays in the model and the trace for debugging and is never
    shown to a reader. ``score_normalized`` is that score divided by the top
    score for the same query, which makes it a 0-1 quantity that means "how
    close to the best hit for this requirement" — the only comparison a reader
    can actually make.

    ``relevant`` records whether the passage cleared the relevance threshold.
    ``cited`` records whether the scoring model leaned on it. A passage can be
    retrieved and irrelevant, or relevant and uncited, and the report needs to
    tell those apart — conflating them is what made the trace and the report
    contradict each other.
    """

    doc_id: str
    snippet: str
    score: float
    score_normalized: float = 0.0
    relevant: bool = True
    cited: bool = False


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
    """A drafted bullet, or a refusal.

    ``refusal_source`` distinguishes the two ways a refusal happens, and the
    difference matters more than anything else on the page: ``model`` means the
    drafting model looked at the evidence and declined, ``validator`` means it
    wrote a claim and the grounding check threw the claim away.
    """

    requirement_text: str
    bullet: str | None = None
    refused: bool = False
    refusal_reason: str | None = None
    refusal_source: RefusalSource | None = None
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


# --------------------------------------------------------------------------
# Constrained model outputs
#
# The model is never allowed to emit an `Evidence` object. It emits doc_ids it
# wants to cite, and the server rehydrates the snippet and score from what
# retrieval actually returned. A model that cannot author an evidence object
# cannot author a plausible-looking fake one.
# --------------------------------------------------------------------------


class ScoringVerdict(BaseModel):
    requirement_text: str
    status: Status
    cited_doc_ids: list[str] = Field(default_factory=list)
    rationale: str


class ScoringOutput(BaseModel):
    verdicts: list[ScoringVerdict] = Field(default_factory=list)
    summary: str = ""


class DraftedBullet(BaseModel):
    """What the drafting model is allowed to emit.

    Deliberately missing ``refusal_source``: provenance is a fact about who
    refused, and the model is not in a position to attest to it.
    """

    requirement_text: str
    bullet: str | None = None
    refused: bool = False
    refusal_reason: str | None = None
    grounded_in: list[str] = Field(default_factory=list)


class DraftingOutput(BaseModel):
    suggestions: list[DraftedBullet] = Field(default_factory=list)
