"""Record samples/demo.gif — a real browser session, for the README.

Starts the app in scripted mode, drives a real Chromium through the form, and
composes the frames into an animated GIF. Only the model's replies are canned:
retrieval, the grounding validator, the trace and the page are the real thing.

    pip install playwright pillow
    playwright install chromium
    python scripts/record_demo.py

Re-run this after changing the corpus — the GIF is only as interesting as the
documents behind it.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples" / "demo.gif"
FRAMES = Path(os.environ.get("DEMO_FRAME_DIR", "/tmp/jha-frames"))

WIDTH, HEIGHT = 1000, 660
SCALE = 0.78          # GIF pixels per browser pixel
COLOURS = 96          # palette size; the page is flat colour, so this is plenty
SCROLL_STEP = 165


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_server(port: int) -> subprocess.Popen:
    env = {**os.environ, "LLM_CLIENT": "scripted", "DB_PATH": ":memory:", "LOG_LEVEL": "WARNING"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
            return proc
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("the server never became reachable")


def main() -> None:
    port = _free_port()
    server = _start_server(port)

    FRAMES.mkdir(parents=True, exist_ok=True)
    for stale in FRAMES.glob("*.png"):
        stale.unlink()

    jd = (ROOT / "tests" / "fixtures" / "sample_jd.txt").read_text()
    shots: list[Path] = []

    def shot(page, count: int = 1) -> None:
        """Capture a frame. Repeat it to hold the moment in the finished GIF."""
        for _ in range(count):
            path = FRAMES / f"{len(shots):04d}.png"
            page.screenshot(path=str(path))
            shots.append(path)

    try:
        with sync_playwright() as pw:
            launch_kwargs = {}
            if os.environ.get("CHROMIUM_PATH"):
                launch_kwargs["executable_path"] = os.environ["CHROMIUM_PATH"]
            browser = pw.chromium.launch(**launch_kwargs)
            page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})

            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_load_state("networkidle")
            shot(page, 4)

            box = page.locator("#jd_text")
            box.click()
            box.type(jd[:110], delay=12)   # type the opening for real
            shot(page, 2)
            box.fill(jd)                    # then drop the rest in
            shot(page, 4)

            page.locator("button[type=submit]").click()
            page.wait_for_load_state("networkidle")
            shot(page, 6)

            height = page.evaluate("document.body.scrollHeight")
            offset = 0
            while offset < height - HEIGHT:
                offset += SCROLL_STEP
                page.evaluate(f"window.scrollTo(0, {offset})")
                page.wait_for_timeout(60)
                shot(page)
            shot(page, 6)

            browser.close()
    finally:
        server.terminate()

    size = (int(WIDTH * SCALE), int(HEIGHT * SCALE))
    images = [
        Image.open(p).convert("RGB").resize(size, Image.LANCZOS).quantize(colors=COLOURS)
        for p in shots
    ]
    durations = [1100] + [90] * (len(images) - 2) + [2600]

    OUT.parent.mkdir(exist_ok=True)
    images[0].save(
        OUT, save_all=True, append_images=images[1:], duration=durations, loop=0, optimize=True
    )
    print(f"wrote {OUT}  frames={len(images)}  size={OUT.stat().st_size / 1_048_576:.2f} MB")


if __name__ == "__main__":
    main()
