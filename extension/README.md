# Job Hunt Agent — Chrome extension

Reads the job description on the tab you already have open and sends it to
**your own** deployment for analysis. It renders the match report and the
suggested bullets in the popup, including the refusals.

## Install (unpacked)

1. `chrome://extensions` → enable **Developer mode**
2. **Load unpacked** → select this `extension/` directory
3. Click the icon, open settings (⚙), and paste your API base URL — for example
   `https://job-hunt-agent.<region>.azurecontainerapps.io`, or
   `http://localhost:8000` while developing
4. Chrome will ask permission for that one origin. Approve it.

## What it does, and what it deliberately does not

Open a job posting, click **Analyse this page**. If you highlight the posting
first, the selection is used; otherwise the extractor prefers `[role=main]`,
`<main>` or `<article>` and falls back to the body, skipping anything not
actually visible.

It does **not** fill forms, submit applications, click anything on the page, or
run in the background. There is no service worker: with the popup closed, the
extension executes no code at all.

## Permissions, and why each one

| Permission | Why |
|---|---|
| `activeTab` | Read the tab you are looking at, only after you click. Grants nothing until then, and nothing for any other tab. |
| `scripting` | The API used to inject `extract.js` on that click. It is not a host permission and grants no access to any site by itself. |
| `storage` | Remember your API base URL between sessions. |

Host access is **not** requested at install time. `optional_host_permissions`
means the extension asks for exactly the origin you type into settings, at the
moment you save it, and holds no other host access ever.

## Why extraction is client-side

Reading the posting in your browser is not a workaround, it is the correct
design. Server-side scraping of LinkedIn, Indeed and company career sites is
fragile (markup changes weekly), rate-limited and IP-blocked, frequently against
the site's terms of service, and it cannot see postings behind a login you are
already authenticated for. Your browser is already authorised to read the page
you are looking at. The server never talks to the job board at all.
