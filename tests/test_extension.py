"""§8 — the extension's constraints, enforced as tests.

"No auto-apply" is the product's central promise. A promise that lives only in a
README is a promise that gets broken by a future commit, so it is checked here."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

EXT = Path(__file__).resolve().parents[1] / "extension"
MANIFEST = json.loads((EXT / "manifest.json").read_text())
POPUP_JS = (EXT / "popup.js").read_text()
EXTRACT_JS = (EXT / "extract.js").read_text()


def test_manifest_is_v3():
    assert MANIFEST["manifest_version"] == 3


def test_permissions_are_minimal():
    assert set(MANIFEST["permissions"]) == {"activeTab", "scripting", "storage"}
    for broad in ("tabs", "webRequest", "cookies", "history", "<all_urls>", "downloads"):
        assert broad not in MANIFEST["permissions"]


def test_no_host_access_is_granted_at_install_time():
    """Host access is requested at runtime for the one origin the user names."""
    assert "host_permissions" not in MANIFEST
    assert "optional_host_permissions" in MANIFEST
    assert "chrome.permissions.request" in POPUP_JS


def test_no_background_service_worker():
    """Popup closed means nothing running. No background scraping is possible."""
    assert "background" not in MANIFEST


def test_no_static_content_scripts():
    """Injection happens on click under activeTab, not on every page load."""
    assert "content_scripts" not in MANIFEST
    assert "chrome.scripting.executeScript" in POPUP_JS


@pytest.mark.parametrize(
    "forbidden",
    [".submit(", "form.submit", "requestSubmit", "autofill", "chrome.webRequest", "setInterval"],
)
def test_no_auto_submit_or_polling(forbidden):
    assert forbidden not in POPUP_JS
    assert forbidden not in EXTRACT_JS


def test_extractor_does_not_navigate_or_reach_other_tabs():
    for forbidden in ["window.location =", "location.href =", "window.open(", "fetch("]:
        assert forbidden not in EXTRACT_JS


def test_extractor_only_reads_visible_text():
    assert "getComputedStyle" in EXTRACT_JS
    assert "getSelection" in EXTRACT_JS


def test_every_referenced_file_exists():
    referenced = {MANIFEST["action"]["default_popup"]}
    referenced |= set(MANIFEST["icons"].values())
    referenced |= set(MANIFEST["action"]["default_icon"].values())
    referenced |= {"popup.js", "popup.css", "extract.js"}
    for name in referenced:
        assert (EXT / name).is_file(), f"{name} referenced but missing"


def test_icons_are_real_pngs():
    for size, name in MANIFEST["icons"].items():
        blob = (EXT / name).read_bytes()
        assert blob.startswith(b"\x89PNG\r\n\x1a\n"), f"{name} is not a PNG"
        width = int.from_bytes(blob[16:20], "big")
        assert width == int(size)


def test_popup_renders_refusal_provenance():
    """The extension must distinguish a validator override, like the web UI."""
    assert "Overridden by the grounding validator" in POPUP_JS
    assert "Refused by the model" in POPUP_JS
