"""Canned model responses for ``LLM_CLIENT=scripted``.

This is what makes scripted mode a real runtime mode rather than a hole in the
config: the deployed app answers with no API key and at zero cost, which is what
a portfolio URL needs. Only the *model* is canned — retrieval, scoring
assembly, the guardrail, the trace, metrics and persistence all run for real
against whatever corpus is loaded, so the demo improves the moment the corpus
does.

The result page shows a banner in this mode. Presenting canned output as a live
model run would be the same class of dishonesty the guardrail exists to prevent.
"""

from __future__ import annotations

from typing import Any

from app.agent.client import LLMReply, scripted_tool_call

# Requirements from a generic backend posting. Text is matched against the
# corpus at runtime, so these stay useful once real documents are in place.
DEMO_REQUIREMENTS: list[dict[str, Any]] = [
    {"text": "Strong Python", "kind": "hard", "category": "language", "years": None},
    {"text": "Production PostgreSQL including query plans and indexing", "kind": "hard",
     "category": "platform", "years": None},
    {"text": "Building and consuming REST APIs at scale", "kind": "hard",
     "category": "experience", "years": None},
    {"text": "Docker and a CI/CD pipeline", "kind": "hard", "category": "platform", "years": None},
    {"text": "Kubernetes in production", "kind": "preferred", "category": "platform", "years": None},
    {"text": "Terraform or another infrastructure-as-code tool", "kind": "preferred",
     "category": "platform", "years": None},
]

# Raw-score cut-offs used ONLY to pick a canned verdict. The real path takes its
# verdicts from the model; nothing outside this module scores by threshold.
_MET_AT = 4.0
_PARTIAL_AT = 1.5

_PLANNER_TOKENS = (180, 35)
_TOOL_TOKENS = (900, 240)


def _plan(tool: str, why: str, **args: Any) -> LLMReply:
    return scripted_tool_call(
        tool, {"why": why, **args}, input_tokens=_PLANNER_TOKENS[0], output_tokens=_PLANNER_TOKENS[1]
    )


def demo_replies(index: Any) -> list[LLMReply]:
    """A full six-requirement run, scored against real retrieval."""
    replies: list[LLMReply] = [
        _plan("parse_requirements", "the posting has not been parsed yet"),
        scripted_tool_call(
            "emit_requirements",
            {
                "role_title": "Backend Engineer",
                "company": None,
                "requirements": DEMO_REQUIREMENTS,
            },
            input_tokens=_TOOL_TOKENS[0],
            output_tokens=_TOOL_TOKENS[1],
        ),
    ]

    hits_by_req: dict[str, list[Any]] = {}
    for req in DEMO_REQUIREMENTS:
        replies.append(
            _plan(
                "retrieve_evidence",
                f"look for corpus evidence of: {req['text']}",
                requirement=req["text"],
                k=3,
            )
        )
        hits_by_req[req["text"]] = index.query(req["text"], k=3) if index else []

    verdicts = []
    for req in DEMO_REQUIREMENTS:
        hits = hits_by_req[req["text"]]
        relevant = [h for h in hits if h.relevant]
        if relevant and relevant[0].score >= _MET_AT:
            verdicts.append({
                "requirement_text": req["text"], "status": "met",
                "cited_doc_ids": [relevant[0].doc_id],
                "rationale": f"{relevant[0].doc_id} speaks to this requirement directly",
            })
        elif relevant and relevant[0].score >= _PARTIAL_AT:
            verdicts.append({
                "requirement_text": req["text"], "status": "partial",
                "cited_doc_ids": [relevant[0].doc_id],
                "rationale": f"{relevant[0].doc_id} is adjacent but does not state the full scope",
            })
        else:
            verdicts.append({
                "requirement_text": req["text"], "status": "gap",
                "cited_doc_ids": [],
                "rationale": "nothing in the corpus covers this requirement",
            })

    replies += [
        _plan("score_match", "every requirement now has retrieval behind it"),
        scripted_tool_call(
            "emit_match_report",
            {
                "verdicts": verdicts,
                "summary": (
                    "Canned demo verdicts over live retrieval. Strength tracks whatever "
                    "the loaded corpus actually contains."
                ),
            },
            input_tokens=_TOOL_TOKENS[0],
            output_tokens=_TOOL_TOKENS[1],
        ),
        _plan("draft_bullets", "several requirements came back short of met"),
    ]

    shortfalls = [v for v in verdicts if v["status"] in ("gap", "partial")]
    suggestions = []
    fabricated_used = False
    for verdict in shortfalls:
        hits = [h for h in hits_by_req[verdict["requirement_text"]] if h.relevant]
        if hits:
            suggestions.append({
                "requirement_text": verdict["requirement_text"],
                "bullet": f"Work evidenced in {hits[0].doc_id}, written up against this requirement.",
                "refused": False,
                "grounded_in": [hits[0].doc_id],
            })
        elif not fabricated_used:
            # One deliberately ungrounded draft, so the guardrail is visible in
            # the demo rather than merely described in the README.
            fabricated_used = True
            suggestions.append({
                "requirement_text": verdict["requirement_text"],
                "bullet": "Owned this area end to end in a production environment.",
                "refused": False,
                "grounded_in": ["resume#not-a-real-section"],
            })
        else:
            suggestions.append({
                "requirement_text": verdict["requirement_text"],
                "bullet": None, "refused": True,
                "refusal_reason": "no passage in the corpus supports this requirement",
                "grounded_in": [],
            })

    replies += [
        scripted_tool_call(
            "emit_bullets", {"suggestions": suggestions},
            input_tokens=_TOOL_TOKENS[0], output_tokens=_TOOL_TOKENS[1],
        ),
        _plan("finish", "the report and the bullets are both complete"),
    ]
    return replies
