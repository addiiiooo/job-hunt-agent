"""Post-hoc grounding validation.

The drafting prompt asks the model not to invent experience. This module makes
that true regardless of whether the model complied.

Both layers exist on purpose, and they are not redundant. The prompt is how you
get good behaviour most of the time; the validator is how you get a guarantee.
A prompt cannot be tested — you can only sample it. This can be tested, and
`tests/test_guardrail.py` proves it turns a fabricated citation into a refusal.

Two checks, run after `draft_bullets` returns and before any result is shown:

1. Every non-refused suggestion must cite something (`grounded_in` non-empty).
2. Every doc_id it cites must appear in the evidence actually retrieved for
   that requirement — not merely somewhere in the corpus, and not under a
   different requirement.

Anything failing either check is rewritten into a refusal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agent.tools import ev_key
from app.models import BulletSuggestion, Evidence

REFUSAL_REASON = "failed grounding validation"


@dataclass
class GuardrailOutcome:
    suggestions: list[BulletSuggestion]
    rejected: list[dict[str, str]] = field(default_factory=list)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)


def enforce(
    suggestions: list[BulletSuggestion],
    evidence_map: dict[str, list[Evidence]],
) -> GuardrailOutcome:
    """Return validated suggestions, with ungrounded ones rewritten to refusals."""
    kept: list[BulletSuggestion] = []
    rejected: list[dict[str, str]] = []

    for suggestion in suggestions:
        if suggestion.refused:
            # An honest refusal from the model passes through untouched, but its
            # provenance is pinned so the page can say who refused.
            kept.append(
                suggestion
                if suggestion.refusal_source
                else suggestion.model_copy(update={"refusal_source": "model"})
            )
            continue

        allowed = {e.doc_id for e in evidence_map.get(ev_key(suggestion.requirement_text), [])}

        if not suggestion.grounded_in:
            reason = "cited nothing"
        else:
            fabricated = [d for d in suggestion.grounded_in if d not in allowed]
            reason = (
                "cited doc_id(s) not retrieved for this requirement: " + ", ".join(sorted(fabricated))
                if fabricated
                else ""
            )

        if not reason:
            kept.append(suggestion)
            continue

        rejected.append(
            {
                "requirement_text": suggestion.requirement_text,
                "detail": reason,
                "discarded_bullet": suggestion.bullet or "",
            }
        )
        kept.append(
            BulletSuggestion(
                requirement_text=suggestion.requirement_text,
                bullet=None,
                refused=True,
                refusal_reason=REFUSAL_REASON,
                refusal_source="validator",
                grounded_in=[],
            )
        )

    return GuardrailOutcome(suggestions=kept, rejected=rejected)
