"""Render a sample result page to samples/scripted-run.html.

Runs the app in scripted mode (``LLM_CLIENT=scripted``), so the output is a real
render of a real trace over real retrieval — only the model responses are
canned. Costs nothing, needs no API key, and cannot drift from the code.

For a live-model sample, run `scripts/smoke_live.py --html` with a real key;
that writes samples/live-run.html.

    python scripts/render_sample.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["LLM_CLIENT"] = "scripted"
os.environ.setdefault("DB_PATH", ":memory:")

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402

JD = (ROOT / "tests" / "fixtures" / "sample_jd.txt").read_text()
OUT = ROOT / "samples" / "scripted-run.html"


def inline_css(html: str) -> str:
    css = (ROOT / "app" / "static" / "style.css").read_text()
    return html.replace(
        '<link rel="stylesheet" href="/static/style.css">', f"<style>\n{css}\n</style>"
    )


def main() -> None:
    config.reset_settings_cache()
    from app.main import app

    with TestClient(app) as client:
        html = client.post("/analyse", data={"jd_text": JD}).text

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(inline_css(html))
    print(f"wrote {OUT} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
