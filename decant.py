#!/usr/bin/env python3
"""decant - rip clean Markdown from auth-gated web pages by reusing a real browser session.

MVP scope: a single page. Two subcommands:

    python decant.py login [URL]   open a real browser window, log in once (2FA included);
                                   the session is saved into a dedicated profile dir.
    python decant.py get URL       fetch the rendered page headless through the saved session
                                   and print clean Markdown (and optionally write a .md file).

Auth model
----------
A dedicated *persistent* browser profile (Playwright user-data-dir). You log in once;
cookies + localStorage live in the profile and are reused on every later run. If the saved
session has expired, `get` detects the login wall and tells you to re-run `login`.

The session layer is intentionally swappable: today it's the persistent profile; a CDP-attach
backend ("use my already-open browser") can be added later without touching extraction/output.
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
import urllib.parse
from pathlib import Path

# --- configuration -----------------------------------------------------------

DEFAULT_PROFILE = Path(
    os.environ.get("DECANT_PROFILE", Path(__file__).resolve().parent / ".profile")
)
# Engine: "chromium" (drive installed Edge/Chrome via --channel, or bundled Chromium) or
# "firefox" (Playwright's own bundled Firefox - a dedicated profile, NOT your system Firefox).
DEFAULT_ENGINE = os.environ.get("DECANT_ENGINE", "chromium")
# For the chromium engine: which binary to drive. "" = bundled Chromium (needs once:
# python -m playwright install chromium); "msedge"/"chrome" drive an installed browser (no download).
# OS-aware default: Edge ships with Windows (zero download); elsewhere bundled Chromium for portability.
if os.environ.get("DECANT_BROWSER") is not None:
    DEFAULT_CHANNEL = os.environ["DECANT_BROWSER"]
elif sys.platform == "win32":
    DEFAULT_CHANNEL = "msedge"
else:
    DEFAULT_CHANNEL = ""

LOGIN_URL_HINTS = ("login", "signin", "sign-in", "sso", "saml", "okta", "adfs", "/idp/", "auth/")
LOGIN_TITLE_HINTS = ("log in", "sign in", "login", "authentication required", "single sign")


class NeedsLogin(Exception):
    """Raised when a fetch lands on a login / SSO wall instead of the target page."""


# --- session layer (persistent profile) --------------------------------------

def _looks_like_login(url: str, html: str, title: str) -> bool:
    u = (url or "").lower()
    if any(h in u for h in LOGIN_URL_HINTS):
        return True
    if any(k in (title or "").lower() for k in LOGIN_TITLE_HINTS):
        return True
    # A password field on a page with very little content is almost certainly a login wall.
    if re.search(r'<input[^>]+type=["\']?password', html, re.I) and len(html) < 200_000:
        return True
    return False


def _launch_context(p, profile, *, headless, engine, channel):
    """Launch a persistent browser context for the chosen engine."""
    if engine == "firefox":
        return p.firefox.launch_persistent_context(user_data_dir=str(profile), headless=headless)
    kwargs = {"user_data_dir": str(profile), "headless": headless}
    if channel:
        kwargs["channel"] = channel
    return p.chromium.launch_persistent_context(**kwargs)


def fetch(url, *, profile=DEFAULT_PROFILE, engine=DEFAULT_ENGINE, channel=DEFAULT_CHANNEL,
          headless=True, settle_ms=1500, timeout_ms=30_000):
    """Render `url` in the persistent profile and return (final_url, html, title)."""
    from playwright.sync_api import sync_playwright

    profile = Path(profile)
    profile.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        ctx = _launch_context(p, profile, headless=headless, engine=engine, channel=channel)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except Exception:
                pass  # SPAs often never go fully idle; the settle wait below covers it.
            if settle_ms:
                page.wait_for_timeout(settle_ms)
            html = page.content()
            title = page.title()
            final_url = page.url
        finally:
            ctx.close()

    if _looks_like_login(final_url, html, title):
        raise NeedsLogin(final_url)
    return final_url, html, title


def interactive_login(url, *, profile=DEFAULT_PROFILE, engine=DEFAULT_ENGINE, channel=DEFAULT_CHANNEL):
    """Open a real (headful) window so the user can authenticate; persist the session."""
    from playwright.sync_api import sync_playwright

    profile = Path(profile)
    profile.mkdir(parents=True, exist_ok=True)
    label = "firefox" if engine == "firefox" else (channel or "chromium")
    print(f"[decant] launching {label} with profile: {profile}", file=sys.stderr)
    with sync_playwright() as p:
        ctx = _launch_context(p, profile, headless=False, engine=engine, channel=channel)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if url:
            page.goto(url)
        input("[decant] Log in (incl. 2FA) in the window, then press Enter here to save... ")
        ctx.close()
    print("[decant] session saved.", file=sys.stderr)


# --- extraction --------------------------------------------------------------

def tidy(md):
    """Conservative cleanup of common extractor artifacts (no paragraph-level reflow)."""
    # A markdown link glued to the next word -> insert a space: ](url)Word -> ](url) Word
    md = re.sub(r'(\]\([^)\s]+\))(\w)', r'\1 \2', md)
    # A word glued to the start of a link -> insert a space: word[text](url) -> word [text](url)
    md = re.sub(r'(\w)(\[[^\]]+\]\([^)\s]+\))', r'\1 \2', md)
    # Collapse runs of 3+ blank lines down to one blank line.
    md = re.sub(r'\n{3,}', '\n\n', md)
    return md.strip()


def to_markdown(html, url):
    """HTML -> clean Markdown. trafilatura first; markdownify as a structural fallback."""
    import trafilatura

    md = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_tables=True,
        include_formatting=True,
        include_comments=False,
        favor_recall=True,
    )
    if md:
        return tidy(md)
    from markdownify import markdownify as mdify
    return tidy(mdify(html, heading_style="ATX"))


def extract_meta(html, url):
    import trafilatura

    try:
        m = trafilatura.extract_metadata(html, default_url=url)
        if m is None:
            return {}
        return {
            "title": getattr(m, "title", None),
            "author": getattr(m, "author", None),
            "date": getattr(m, "date", None),
            "sitename": getattr(m, "sitename", None),
        }
    except Exception:
        return {}


# --- output ------------------------------------------------------------------

def _yaml_scalar(value):
    s = "" if value is None else str(value)
    if s == "" or re.search(r'[:#\[\]{}",&*!|>%@`]', s) or s != s.strip():
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def slugify(text, maxlen=80):
    text = re.sub(r"[^\w\s-]", "", (text or "").strip().lower(), flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return (text or "page")[:maxlen]


def build_frontmatter(url, title, extra):
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "---",
        f"url: {_yaml_scalar(url)}",
        f"title: {_yaml_scalar(title)}",
        f"domain: {_yaml_scalar(urllib.parse.urlparse(url).netloc)}",
        f"fetched_at: {_yaml_scalar(now)}",
    ]
    for key, val in extra.items():
        if val:
            lines.append(f"{key}: {_yaml_scalar(val)}")
    lines.append("---")
    return "\n".join(lines)


# --- CLI ---------------------------------------------------------------------

def cmd_login(args):
    interactive_login(args.url, profile=args.profile, engine=args.engine, channel=args.channel)


def cmd_get(args):
    try:
        final_url, html, title = fetch(
            args.url,
            profile=args.profile,
            engine=args.engine,
            channel=args.channel,
            headless=not args.no_headless,
            settle_ms=args.settle,
            timeout_ms=args.timeout,
        )
    except NeedsLogin as exc:
        print(
            f"[decant] hit a login wall at: {exc}\n"
            f"[decant] run once:  python decant.py login {args.url}",
            file=sys.stderr,
        )
        return 2

    meta = extract_meta(html, final_url)
    title = meta.get("title") or title
    body = html if args.raw else to_markdown(html, final_url)
    doc = build_frontmatter(
        final_url,
        title,
        {"author": meta.get("author"), "date": meta.get("date"), "sitename": meta.get("sitename")},
    ) + "\n\n" + body.rstrip() + "\n"

    sys.stdout.buffer.write(doc.encode("utf-8", "replace"))

    if args.out:
        outdir = Path(args.out)
        outdir.mkdir(parents=True, exist_ok=True)
        name = slugify(title or urllib.parse.urlparse(final_url).path)
        path = outdir / f"{name}.md"
        path.write_text(doc, encoding="utf-8")
        print(f"\n[decant] wrote {path}", file=sys.stderr)
    return 0


def cmd_serve(args):
    import serve
    serve.run(args.port)


def cmd_bookmarklet(args):
    import serve
    print(serve.bookmarklet_js(serve.get_token(), args.port))


def cmd_current(args):
    import serve
    if serve.LAST_FILE.exists():
        sys.stdout.buffer.write(serve.LAST_FILE.read_text(encoding="utf-8").encode("utf-8", "replace"))
        return 0
    print("[decant] no capture yet - hit the bookmarklet on a page first", file=sys.stderr)
    return 1


def build_parser():
    p = argparse.ArgumentParser(prog="decant", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", default=str(DEFAULT_PROFILE),
                   help=f"browser profile dir (default: {DEFAULT_PROFILE})")
    p.add_argument("--engine", default=DEFAULT_ENGINE, choices=["chromium", "firefox"],
                   help="browser engine (default: %(default)s)")
    p.add_argument("--channel", default=DEFAULT_CHANNEL,
                   help="chromium channel: msedge | chrome | '' for bundled Chromium")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get", help="fetch one page and print clean Markdown")
    g.add_argument("url")
    g.add_argument("--out", help="directory to also write a <slug>.md file into")
    g.add_argument("--raw", action="store_true", help="emit raw HTML instead of Markdown")
    g.add_argument("--no-headless", action="store_true", help="show the browser window")
    g.add_argument("--settle", type=int, default=1500, help="extra ms to wait after load")
    g.add_argument("--timeout", type=int, default=30_000, help="navigation timeout (ms)")
    g.set_defaults(func=cmd_get)

    l = sub.add_parser("login", help="open a real window to authenticate once")
    l.add_argument("url", nargs="?", help="page to open (e.g. your Confluence base URL)")
    l.set_defaults(func=cmd_login)

    default_port = int(os.environ.get("DECANT_PORT", "8765"))

    s = sub.add_parser("serve", help="run the local capture daemon (for the bookmarklet)")
    s.add_argument("--port", type=int, default=default_port)
    s.set_defaults(func=cmd_serve)

    b = sub.add_parser("bookmarklet", help="print the 'Rip this page' bookmarklet")
    b.add_argument("--port", type=int, default=default_port)
    b.set_defaults(func=cmd_bookmarklet)

    c = sub.add_parser("current", help="print the last captured page as Markdown")
    c.set_defaults(func=cmd_current)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
