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
    BulletSuggestion,
    DraftingOutput,
    ErrorKind,
    Evidence,
    MatchReport,
    Requirement,
    RequirementVerdict,
    Requirements,
    ScoringOutput,
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


# ---------------------------------------------------------------------------
# Tool 2 — retrieve_evidence
# ---------------------------------------------------------------------------

RETRIEVE_TOOL_SCHEMA = {
    "name": "retrieve_evidence",
    "description": (
        "Search the candidate's own corpus (resume and project write-ups) for "
        "passages relevant to one requirement. Lexical BM25 search, no model "
        "call. Returns the top-k passages with scores, or an empty list when "
        "nothing in the corpus overlaps. Call this once per requirement you "
        "intend to score."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "requirement": {
                "type": "string",
                "description": "The requirement text to search for, verbatim from parse_requirements.",
            },
            "k": {
                "type": "integer",
                "description": "How many passages to return. Default 5.",
                "minimum": 1,
                "maximum": 20,
            },
        },
        "required": ["requirement"],
    },
}


def retrieve_evidence(requirement: str, k: int = 5, *, index: Any) -> list[Any]:
    """BM25 over the corpus. Top-k with scores. No LLM call.

    An empty return is a real answer — it means nothing in the corpus overlaps
    lexically with this requirement — and it is what makes a refusal downstream
    honest rather than decorative.
    """
    if index is None or index.is_empty():
        raise AgentError(ErrorKind.CORPUS_EMPTY, "corpus index is empty; nothing to retrieve from")
    if not requirement or not requirement.strip():
        raise AgentError(ErrorKind.TOOL_EXECUTION, "requirement text is empty")
    return index.query(requirement, k=k)


# ---------------------------------------------------------------------------
# Planner-facing schemas
#
# These describe the tools as *actions the planner can choose*, which is not the
# same thing as the schema a tool's output is validated against. Where an
# argument is fixed for the whole run (the job description) or is large derived
# state (the evidence map), the planner does not pass it: the loop supplies it
# from run state. Round-tripping a corpus through the planner would burn tokens
# and — worse — give the model an opportunity to paraphrase evidence on the way
# past, which is exactly what the guardrail exists to prevent.
# ---------------------------------------------------------------------------

PARSE_TOOL_SCHEMA: dict[str, Any] = {
    "name": "parse_requirements",
    "description": (
        "Read the job description for this run and extract its requirements as "
        "atomic hard/preferred items with normalised years. Call once, first. "
        "Takes no arguments — the job description is fixed for the run."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}


# ---------------------------------------------------------------------------
# Evidence map keys
# ---------------------------------------------------------------------------


def ev_key(text: str) -> str:
    """Normalise a requirement string into an evidence-map key.

    The planner echoes requirement text back to us and will not always echo it
    byte-for-byte. Keying on normalised whitespace and case means a stray
    capital does not silently detach a requirement from its evidence — which
    would make the guardrail refuse everything for the wrong reason.
    """
    return " ".join((text or "").lower().split())


def _render_evidence_map(
    requirements: list[Any], evidence_map: dict[str, list[Any]]
) -> str:
    """The exact evidence the model is allowed to reason from, and nothing else."""
    blocks: list[str] = []
    for req in requirements:
        hits = evidence_map.get(ev_key(req.text))
        header = f"### REQUIREMENT: {req.text}\n(kind: {req.kind}, category: {req.category}"
        header += f", years: {req.years})" if req.years is not None else ")"
        if hits is None:
            body = "  (no retrieval was performed for this requirement)"
        elif not hits:
            body = "  (retrieval ran and found nothing in the corpus)"
        else:
            body = "\n".join(
                f"  - doc_id: {e.doc_id} (score {e.score})\n    {e.snippet}" for e in hits
            )
        blocks.append(f"{header}\n{body}")
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Tool 3 — score_match
# ---------------------------------------------------------------------------

SCORE_SYSTEM = """You score a candidate against a job's requirements using only
the retrieved evidence you are given.

For every requirement, return exactly one verdict:

- met      — the evidence directly supports this requirement.
- partial  — the evidence supports something adjacent, or supports it at a
             smaller scope or fewer years than asked for.
- gap      — the evidence does not support it. This includes the case where no
             retrieval was performed or retrieval found nothing.

Hard rules:

- cited_doc_ids may only contain doc_ids that appear verbatim in the evidence
  block for THAT requirement. Never cite a doc_id listed under a different
  requirement, and never invent one.
- A verdict of met or partial must cite at least one doc_id. If you cannot
  cite anything, the verdict is gap.
- rationale is one line, and it must refer to what the cited passage actually
  says. "Strong candidate" is not a rationale. "Resume states EXPLAIN ANALYZE
  work on hot paths, which matches the query-plan requirement" is.
- Absence of evidence is a gap, not a met. You are not being asked to be
  generous; you are being asked to be checkable.
- Do not report any totals or counts. Those are computed from your verdicts,
  not taken from you.

summary is two or three sentences a human can read before the table: what this
candidate clearly has, and where the real gaps are."""

EMIT_MATCH = _emit_tool(
    "emit_match_report",
    "Return one verdict per requirement, citing only the doc_ids provided.",
    ScoringOutput,
)

SCORE_TOOL_SCHEMA: dict[str, Any] = {
    "name": "score_match",
    "description": (
        "Score every parsed requirement as met / partial / gap against the "
        "evidence retrieved so far, with a cited passage and a one-line "
        "rationale each. Call once, after retrieval. Takes no arguments — it "
        "reads the requirements and evidence already gathered in this run."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}


def score_match(
    reqs: Requirements,
    evidence_map: dict[str, list[Evidence]],
    *,
    client: ClaudeClient,
    settings: Settings,
    repair_note: str | None = None,
) -> tuple[MatchReport, int, int]:
    """Per requirement: met / partial / gap, with cited evidence and a rationale.

    The headline numbers are computed here, from the verdicts. The model is told
    not to produce them and could not be trusted with them if it did: an
    unexplained number is exactly the thing this project exists to avoid.
    """
    if reqs is None or not reqs.requirements:
        raise AgentError(ErrorKind.TOOL_EXECUTION, "no requirements to score; run parse_requirements first")

    user = (
        f"ROLE: {reqs.role_title}" + (f" at {reqs.company}" if reqs.company else "") + "\n\n"
        "<evidence>\n"
        f"{_render_evidence_map(reqs.requirements, evidence_map)}\n"
        "</evidence>\n\n"
        "Return one verdict for every requirement above, in the same order."
    )

    raw, in_tok, out_tok = structured_call(
        client,
        settings,
        system=SCORE_SYSTEM,
        user=user,
        emit=EMIT_MATCH,
        model_cls=ScoringOutput,
        repair_note=repair_note,
    )

    report = _assemble_report(reqs, evidence_map, raw)
    return report, in_tok, out_tok


def _assemble_report(
    reqs: Requirements, evidence_map: dict[str, list[Evidence]], raw: ScoringOutput
) -> MatchReport:
    """Rehydrate cited ids into real Evidence, then compute the totals ourselves."""
    by_key = {ev_key(v.requirement_text): v for v in raw.verdicts}
    verdicts: list[RequirementVerdict] = []

    for req in reqs.requirements:
        key = ev_key(req.text)
        available = {e.doc_id: e for e in evidence_map.get(key, [])}
        scored = by_key.get(key)

        if scored is None:
            verdicts.append(
                RequirementVerdict(
                    requirement=req,
                    status="gap",
                    evidence=[],
                    rationale="No verdict was returned for this requirement.",
                )
            )
            continue

        cited = [available[d] for d in scored.cited_doc_ids if d in available]
        status = scored.status
        if status in ("met", "partial") and not cited:
            # Cited nothing that retrieval actually returned. That is a gap,
            # whatever the model called it.
            status = "gap"

        verdicts.append(
            RequirementVerdict(
                requirement=req, status=status, evidence=cited, rationale=scored.rationale.strip()
            )
        )

    hard = [v for v in verdicts if v.requirement.kind == "hard"]
    return MatchReport(
        verdicts=verdicts,
        hard_met=sum(1 for v in hard if v.status == "met"),
        hard_total=len(hard),
        summary=raw.summary.strip(),
    )


# ---------------------------------------------------------------------------
# Tool 4 — draft_bullets
# ---------------------------------------------------------------------------

DRAFT_SYSTEM = """You draft resume bullets for requirements the candidate has
not clearly met, using ONLY the retrieved evidence supplied for that
requirement.

For each requirement you are given, return either a bullet or a refusal.

Draft a bullet when the evidence contains real, specific work that speaks to
the requirement. The bullet must:
- describe only what the evidence states, in the candidate's own factual terms;
- list in grounded_in every doc_id you drew on, and nothing else;
- be one line, active voice, concrete. Prefer the specific noun from the
  evidence over a generic one.

Refuse when the evidence is absent, thin, or about something else. Set
refused=true, leave bullet null, and write a one-line refusal_reason naming what
is missing — for example "no Kubernetes work appears anywhere in the corpus".

A refusal is a correct answer, not a failure. Inventing experience the candidate
does not have would get them caught in an interview, which is a far worse
outcome than an honest gap. Never stretch adjacent evidence to cover a
requirement it does not actually cover. Never write a bullet with an empty
grounded_in."""

EMIT_BULLETS = _emit_tool(
    "emit_bullets",
    "Return one bullet suggestion or refusal per requirement.",
    DraftingOutput,
)

DRAFT_TOOL_SCHEMA: dict[str, Any] = {
    "name": "draft_bullets",
    "description": (
        "Draft tailored resume bullets for the requirements scored partial or "
        "gap, strictly from retrieved evidence, refusing where there is none. "
        "Call once, after score_match. Takes no arguments — it reads the report "
        "and evidence already gathered in this run."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}


def draft_bullets(
    gaps: list[Requirement],
    evidence_map: dict[str, list[Evidence]],
    *,
    client: ClaudeClient,
    settings: Settings,
    repair_note: str | None = None,
) -> tuple[list[BulletSuggestion], int, int]:
    """Draft ONLY from retrieved evidence; refuse where evidence is insufficient."""
    if not gaps:
        return [], 0, 0

    user = (
        "<evidence>\n"
        f"{_render_evidence_map(gaps, evidence_map)}\n"
        "</evidence>\n\n"
        "Return one entry per requirement above — a bullet, or a refusal."
    )

    raw, in_tok, out_tok = structured_call(
        client,
        settings,
        system=DRAFT_SYSTEM,
        user=user,
        emit=EMIT_BULLETS,
        model_cls=DraftingOutput,
        repair_note=repair_note,
    )
    return list(raw.suggestions), in_tok, out_tok
