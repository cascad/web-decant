"""decant serve - a tiny local daemon that turns a browser bookmarklet (or any
localhost caller) into a clean-Markdown capture, and remembers the *last* page so an
AI/CLI can read "the page I'm looking at" without you copying a URL.

It binds to 127.0.0.1 only and gates every real action behind a per-machine token
(baked into the generated bookmarklet) so a random website's JS can't drive it.

Endpoints (all but /health require ?t=<token>):
  GET  /health
  POST /capture?url=<u>     body = live page HTML (text/plain)  -> extract from the live DOM
  GET  /capture?url=<u>                                         -> daemon re-fetches via its own session
  GET  /current                                                 -> last captured Markdown
"""
from __future__ import annotations

import html as _html
import json
import os
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import decant  # shared session + extraction helpers
import media   # image/diagram/video extraction

HERE = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = int(os.environ.get("DECANT_PORT", "8765"))
OUT_DIR = Path(os.environ.get("DECANT_OUT") or (HERE / "captures"))
TOKEN_FILE = HERE / ".token"
LAST_FILE = HERE / ".last_capture.md"

# Re-fetching shares one persistent browser profile; serialize so two launches don't
# fight over the same user-data-dir. Live-DOM captures need no browser and run freely.
_fetch_lock = threading.Lock()


def get_token():
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    tok = secrets.token_hex(16)
    TOKEN_FILE.write_text(tok, encoding="utf-8")
    return tok


def save_capture(url, html=None):
    """Extract Markdown from live `html` (preferred) or by re-fetching `url`; persist + remember.

    Media (images/diagrams/video) is extracted only on the re-fetch path (it needs a live page);
    the live-DOM path writes a flat <slug>.md as before.
    """
    cap = None
    if html:
        final_url, raw, page_title, media_items = (url or ""), html, None, []
    else:
        with _fetch_lock:
            cap = decant.fetch(url, media_base=str(OUT_DIR))
        final_url, raw, page_title, media_items = cap.final_url, cap.html, cap.title, cap.media

    body = decant.to_markdown(raw, final_url)
    meta = decant.extract_meta(raw, final_url)
    title = meta.get("title") or page_title or final_url
    if media_items:
        body = media.weave_media(body, media_items)

    doc = decant.build_frontmatter(
        final_url,
        title,
        {"author": meta.get("author"), "date": meta.get("date"), "sitename": meta.get("sitename")},
    ) + "\n\n" + body.rstrip() + "\n"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if cap is not None and cap.page_dir:                       # media mode -> per-page folder
        page_dir = Path(cap.page_dir)
        page_dir.mkdir(parents=True, exist_ok=True)
        path = page_dir / f"{cap.slug}.md"
        path.write_text(doc, encoding="utf-8")
        media.write_manifest(page_dir, final_url, title, media_items)
    else:                                                      # flat (live-DOM path / no media)
        path = OUT_DIR / f"{decant.slugify(title or urllib.parse.urlparse(final_url).path)}.md"
        path.write_text(doc, encoding="utf-8")
    LAST_FILE.write_text(doc, encoding="utf-8")
    return {"ok": True, "title": title, "url": final_url, "file": str(path),
            "media": sum(1 for m in media_items if m.get("file")),
            "words": len(body.split())}


def _page(title, body):
    return (
        "<!doctype html><meta charset=utf-8><title>decant</title>"
        "<body style='font:16px system-ui;max-width:40rem;margin:3rem auto'>"
        f"<h2>{_html.escape(title)}</h2><p>{body}</p>"
        "<p style='color:#888'>Можно закрыть вкладку.</p>"
    )


def bookmarklet_js(token, port=PORT):
    base = f"http://{HOST}:{port}"
    return (
        "javascript:(function(){"
        f"var T='{token}',P='{base}',u=location.href;"
        "fetch(P+'/capture?t='+T+'&url='+encodeURIComponent(u),"
        "{method:'POST',headers:{'Content-Type':'text/plain'},body:document.documentElement.outerHTML})"
        ".then(function(r){return r.json()}).then(function(d){alert('decant \\u2713 '+(d.title||u))})"
        ".catch(function(e){window.open(P+'/capture?t='+T+'&url='+encodeURIComponent(u),'_blank')});"
        "})();"
    )


def make_handler(token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - match base signature
            pass  # quiet by default

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authed(self, qs):
            return qs.get("t", [""])[0] == token

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/health":
                return self._send(200, "ok", "text/plain; charset=utf-8")
            if not self._authed(qs):
                return self._send(403, "forbidden", "text/plain; charset=utf-8")
            if parsed.path == "/current":
                md = LAST_FILE.read_text(encoding="utf-8") if LAST_FILE.exists() else ""
                return self._send(200, md, "text/markdown; charset=utf-8")
            if parsed.path == "/capture":
                url = qs.get("url", [""])[0]
                try:
                    res = save_capture(url)
                except decant.NeedsLogin:
                    return self._send(200, _page("Нужен логин",
                        f"Сессия демона не авторизована. Выполни: <code>decant login {_html.escape(url)}</code>"),
                        "text/html; charset=utf-8")
                except Exception as exc:  # noqa: BLE001
                    return self._send(500, _page("Ошибка", _html.escape(str(exc))), "text/html; charset=utf-8")
                return self._send(200, _page("Готово ✓",
                    f"{_html.escape(res['title'])}<br><code>{_html.escape(res['file'])}</code>"),
                    "text/html; charset=utf-8")
            return self._send(404, "not found", "text/plain; charset=utf-8")

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if not self._authed(qs):
                return self._send(403, "forbidden", "text/plain; charset=utf-8")
            if parsed.path == "/capture":
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
                url = qs.get("url", [""])[0]
                try:
                    res = save_capture(url, html=body or None)
                except Exception as exc:  # noqa: BLE001
                    return self._send(500, json.dumps({"ok": False, "error": str(exc)}))
                return self._send(200, json.dumps(res))
            return self._send(404, "not found", "text/plain; charset=utf-8")

    return Handler


def run(port=PORT):
    token = get_token()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, port), make_handler(token))
    print(f"[decant] serving on http://{HOST}:{port}", flush=True)
    print(f"[decant] captures -> {OUT_DIR}", flush=True)
    print(f"[decant] bookmarklet (also: python decant.py bookmarklet):", flush=True)
    print("  " + bookmarklet_js(token, port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[decant] bye", flush=True)
    finally:
        server.shutdown()
