"""One real end-to-end run against the live Anthropic API.

The test suite never calls the network. This script does, deliberately, and is
the thing to run after changing a prompt.

    export ANTHROPIC_API_KEY=sk-...
    python scripts/smoke_live.py [path/to/jd.txt]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.client import AnthropicClient  # noqa: E402
from app.agent.loop import run  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.corpus.index import CorpusIndex  # noqa: E402
from app.obs.logging import configure_logging  # noqa: E402


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    jd_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests" / "fixtures" / "sample_jd.txt"
    jd_text = jd_path.read_text()

    index = CorpusIndex.from_directory(settings.corpus_dir)
    if index.is_empty():
        print("corpus is empty — fill in app/corpus/documents/ first", file=sys.stderr)
        return 2

    result = run(
        jd_text,
        client=AnthropicClient(settings.require_api_key(), settings.model),
        index=index,
        settings=settings,
        request_id="smoke",
    )

    print(f"\n{result.report.hard_met}/{result.report.hard_total} hard requirements met")
    print(f"{result.report.summary}\n")
    print(f"{'#':>2}  {'tool':<20} {'ms':>7} {'tok':>7}  why")
    for step in result.trace:
        tokens = step.input_tokens + step.output_tokens
        why = (step.error and f"ERROR {step.error}") or (step.rationale or "")
        print(f"{step.index:>2}  {step.tool:<20} {step.latency_ms:>7} {tokens:>7}  {why[:70]}")

    refused = [s for s in result.suggestions if s.refused]
    print(f"\n{len(result.suggestions)} suggestions, {len(refused)} refused")
    for s in refused:
        print(f"  refused [{s.requirement_text}]: {s.refusal_reason}")

    print(f"\nterminated: {result.terminated_reason}  "
          f"latency: {result.total_latency_ms} ms  tokens: {result.total_tokens}")

    out = ROOT / "smoke_result.json"
    out.write_text(json.dumps(result.model_dump(), indent=2, default=str))
    print(f"full result written to {out}")
    return 0 if result.terminated_reason == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
