// Injected into the active tab on demand, only after the user clicks Analyse.
// Reads text that is already on screen. It does not navigate, does not follow
// links, does not touch other tabs, and runs nowhere in the background.
(() => {
  const MAX_CHARS = 24000;
  const DROP = new Set([
    "SCRIPT", "STYLE", "NOSCRIPT", "SVG", "CANVAS", "IFRAME",
    "NAV", "HEADER", "FOOTER", "FORM", "BUTTON", "SELECT", "TEXTAREA",
  ]);

  // If the user highlighted the posting, that is a better signal than any
  // heuristic we could write.
  const selected = String(window.getSelection() || "").trim();
  if (selected.length > 200) {
    return { text: selected.slice(0, MAX_CHARS), source: "selection", title: document.title };
  }

  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
      return false;
    }
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  const textOf = (root) => {
    if (!root) return "";
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || DROP.has(parent.tagName)) return NodeFilter.FILTER_REJECT;
        if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        if (!isVisible(parent)) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    const parts = [];
    let node;
    while ((node = walker.nextNode())) {
      const block = node.parentElement.closest("p,li,h1,h2,h3,h4,h5,h6,td,div");
      parts.push({ text: node.nodeValue.trim(), block });
    }
    let out = "";
    let lastBlock = null;
    for (const part of parts) {
      out += (part.block !== lastBlock && out ? "\n" : " ") + part.text;
      lastBlock = part.block;
    }
    return out.replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n").trim();
  };

  // Prefer the page's own idea of its main content; fall back to the body.
  const candidates = [
    document.querySelector("[role=main]"),
    document.querySelector("main"),
    document.querySelector("article"),
    document.querySelector("#job-details, .job-description, [class*=job-description]"),
    document.body,
  ].filter(Boolean);

  let best = { text: "", source: "body" };
  const names = ["role=main", "main", "article", "job-description", "body"];
  candidates.forEach((el, i) => {
    const text = textOf(el);
    if (text.length > best.text.length) best = { text, source: names[i] };
  });

  return { text: best.text.slice(0, MAX_CHARS), source: best.source, title: document.title };
})();
