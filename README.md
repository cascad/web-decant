# decant

Rip clean Markdown from **auth-gated** web pages (Confluence, internal wikis, anything behind
SSO/2FA) by reusing a **real browser session** — no MCP, no API tokens, no re-auth dance.

The key idea: don't fight the auth. A dedicated persistent browser profile logs in **once**
(2FA included); every later run reuses that session headless. The tool becomes a clean bridge —
it outputs Markdown to stdout/file, which an assistant can then read.

## Why this stack

- **Playwright (real Edge/Chromium)** — Confluence/Jira are JS SPAs; plain HTTP gets you an empty
  shell. A real browser renders the page exactly as you see it.
- **trafilatura** — best-in-class main-content extraction across wildly different page formats;
  strips nav/boilerplate, keeps tables, outputs Markdown. `markdownify` is the structural fallback.
- **Persistent profile** — log in once, reuse forever (until the session expires).

## Setup

Cross-platform (Python 3.9+). The one-line installer creates the venv, installs deps, and
sets up the browser engine (Edge on Windows — no download; bundled Chromium on macOS/Linux).

**Windows (PowerShell):**
```pwsh
cd path\to\web-decant
powershell -ExecutionPolicy Bypass -File install.ps1
```

**macOS / Linux (bash):**
```bash
cd path/to/web-decant
bash install.sh          # also chmod +x's the ./decant wrapper for you
```

Then authenticate once (`./decant` on macOS/Linux, `decant.cmd` on Windows):
```
decant login https://your-confluence.example.com
```

<details>
<summary>Manual setup (instead of the installer)</summary>

```bash
python3 -m venv .venv                                 # Windows: python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt   # Windows: .venv\Scripts\python -m pip ...
.venv/bin/python -m playwright install chromium       # macOS/Linux only (Windows drives Edge)
chmod +x decant                                       # macOS/Linux wrapper
```
</details>

The session profile (`.profile/`) and daemon token (`.token`) are **per-machine** — log in
again on each box; they are not (and must not be) copied or committed.

## Use

```pwsh
# 1) one-time: log into your wiki in the window that opens, then press Enter
python decant.py login https://your-confluence.example.com

# 2) rip any page behind that session
python decant.py get https://your-confluence.example.com/display/SPACE/Some+Page
python decant.py get <URL> --out .\captures              # also save a .md file
python decant.py get <URL> --no-headless                  # watch it work / debug
```

If a fetch hits a login wall, `get` says so — just re-run `login`.

## Config

- `--profile <dir>` / `DECANT_PROFILE` — where the browser session is stored (default: `./.profile`).
- `--channel msedge|chrome|""` / `DECANT_BROWSER` — which browser engine to drive.

## Roadmap

- `crawl <url> --depth N --prefix <path>` — follow neighbouring pages within a space.
- `--attach` (CDP) — rip the page in your already-open browser instead of a dedicated profile.
- `search <query>` — drive Confluence search and rip the top hits ("find the answer" mode).
