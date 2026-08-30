/*
 * Popup logic.
 *
 * The extension does three things: read the text already visible on the tab the
 * user is looking at, POST it to the user's own deployment, and render what
 * comes back. It does not fill forms, does not submit applications, and has no
 * service worker, so it does nothing at all when the popup is closed.
 */

const $ = (id) => document.getElementById(id);
const state = { apiBase: "" };

// ---------------------------------------------------------------------------
// settings
// ---------------------------------------------------------------------------

const normaliseBase = (raw) => String(raw || "").trim().replace(/\/+$/, "");

async function loadSettings() {
  const stored = await chrome.storage.sync.get({ apiBase: "" });
  state.apiBase = normaliseBase(stored.apiBase);
  $("api-base").value = state.apiBase;
  const configured = Boolean(state.apiBase);
  $("settings").hidden = configured;
  $("main").hidden = !configured;
  if (!configured) $("settings-status").textContent = "Set your API URL to get started.";
}

async function saveSettings() {
  const base = normaliseBase($("api-base").value);
  const status = $("settings-status");
  status.className = "status";

  let origin;
  try {
    origin = new URL(base).origin + "/*";
  } catch {
    status.textContent = "That is not a valid URL.";
    status.className = "status error";
    return;
  }

  // Ask for access to this one origin, at the moment the user names it. The
  // extension ships with no host access at all.
  const granted = await chrome.permissions.request({ origins: [origin] });
  if (!granted) {
    status.textContent = "Permission denied for that origin, so nothing was saved.";
    status.className = "status error";
    return;
  }

  await chrome.storage.sync.set({ apiBase: base });
  state.apiBase = base;
  status.textContent = "Saved.";
  $("settings").hidden = true;
  $("main").hidden = false;
}

// ---------------------------------------------------------------------------
// extraction + analysis
// ---------------------------------------------------------------------------

async function extractFromActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) throw new Error("No active tab.");
  const [injection] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ["extract.js"],
  });
  const result = injection && injection.result;
  if (!result || !result.text || result.text.length < 200) {
    throw new Error("Could not find enough text on this page. Try selecting the posting first.");
  }
  return result;
}

async function analyse() {
  const button = $("analyse");
  const statusArea = $("status-area");
  const status = $("status");
  button.disabled = true;
  $("result").hidden = true;
  statusArea.hidden = false;
  status.className = "status";
  status.textContent = "Reading the page…";

  try {
    const extracted = await extractFromActiveTab();
    status.textContent = `Read ${extracted.text.length.toLocaleString()} characters. Analysing…`;

    const response = await fetch(`${state.apiBase}/api/analyse`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jd_text: extracted.text }),
    });
    if (!response.ok) {
      throw new Error(`API returned ${response.status}. Check the URL and that the service is healthy.`);
    }
    render(await response.json());
    statusArea.hidden = true;
  } catch (err) {
    status.textContent = err.message || String(err);
    status.className = "status error";
  } finally {
    button.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// rendering
// ---------------------------------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function render(result) {
  const report = result.report || {};
  $("headline").textContent = `${report.hard_met ?? 0} / ${report.hard_total ?? 0} hard requirements met`;
  $("summary").textContent = report.summary || "";

  const verdicts = $("verdicts");
  verdicts.replaceChildren();
  for (const verdict of report.verdicts || []) {
    const row = el("div", "verdict");
    const head = el("div", "verdict-head");
    head.append(el("span", `pill ${verdict.status}`, verdict.status));
    head.append(el("span", "req", verdict.requirement?.text || ""));
    row.append(head);
    if (verdict.rationale) row.append(el("p", "rationale", verdict.rationale));

    const cited = (verdict.evidence || []).filter((e) => e.cited);
    const retrieved = (verdict.evidence || []).length;
    if (cited.length) {
      row.append(el("p", "cite", `cites ${cited.map((e) => e.doc_id).join(", ")}`));
    } else if (retrieved) {
      row.append(el("p", "cite", `${retrieved} passage(s) retrieved, none cited`));
    } else {
      row.append(el("p", "cite", "no passages retrieved"));
    }
    verdicts.append(row);
  }

  const bullets = $("bullets");
  bullets.replaceChildren();
  for (const suggestion of result.suggestions || []) {
    const kind = suggestion.refused ? suggestion.refusal_source || "model" : "ok";
    const card = el("div", `bullet ${kind}`);
    card.append(el("p", "for", suggestion.requirement_text));

    if (!suggestion.refused) {
      card.append(el("p", null, suggestion.bullet || ""));
      if (suggestion.grounded_in?.length) {
        card.append(el("p", "cite", `grounded in ${suggestion.grounded_in.join(", ")}`));
      }
      const copy = el("button", "copy", "Copy");
      copy.addEventListener("click", async () => {
        await navigator.clipboard.writeText(suggestion.bullet || "");
        copy.textContent = "Copied";
        setTimeout(() => (copy.textContent = "Copy"), 1200);
      });
      card.append(copy);
    } else if (suggestion.refusal_source === "validator") {
      card.append(el("p", null, "Overridden by the grounding validator."));
      card.append(el("p", "why",
        "The model drafted a bullet here and cited a document retrieval never returned. " +
        "The code discarded it — nothing in your corpus supports the claim."));
    } else {
      card.append(el("p", null, "Refused by the model."));
      card.append(el("p", "why", suggestion.refusal_reason || "no reason given"));
    }
    bullets.append(card);
  }

  const steps = (result.trace || []).length;
  $("trace-note").textContent =
    `${steps} agent steps · ${result.total_tokens ?? 0} tokens · ${result.total_latency_ms ?? 0} ms · ` +
    `terminated: ${result.terminated_reason || "?"}`;
  $("result").hidden = false;
}

// ---------------------------------------------------------------------------
// wiring
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", async () => {
  await loadSettings();
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab?.title) $("page-hint").textContent = tab.title;

  $("analyse").addEventListener("click", analyse);
  $("save-settings").addEventListener("click", saveSettings);
  $("settings-toggle").addEventListener("click", () => {
    $("settings").hidden = !$("settings").hidden;
  });
});
