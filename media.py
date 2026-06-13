"""Media extraction for decant.

Pulls image / diagram / video BYTES out of an already-rendered, authenticated page
into a `media/` folder, records *where* each piece appeared in the document, and emits a
`media.json` manifest. Understanding (vision description / OCR / transcription) is left to a
separate, later step: decant only fills the extraction facts; `processed/description/ocr/mermaid`
stay null for an agent or model to fill afterwards.

Runs only on the Playwright path (needs the live page for authed downloads + element
screenshots + SVG serialization + document positions).
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import mimetypes
import urllib.parse
from pathlib import Path

_VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "wistia", "loom.com", "dailymotion")

_MIME_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/gif": "gif",
    "image/webp": "webp", "image/svg+xml": "svg", "image/bmp": "bmp", "image/avif": "avif",
    "image/x-icon": "ico", "video/mp4": "mp4", "video/webm": "webm", "video/ogg": "ogv",
    "video/quicktime": "mov",
}

# Tags media elements in document order, returns descriptors (incl. position hints).
_COLLECT_JS = r"""
(minSize) => {
  const sels = ["#main-content", ".wiki-content", "[data-test-id=\"content-body\"]", "main", "article", "body"];
  let root = null;
  for (const s of sels) { try { const e = document.querySelector(s); if (e) { root = e; break; } } catch (_) {} }
  root = root || document.body;
  const vhost = /youtube\.com|youtu\.be|vimeo\.com|wistia|loom\.com|dailymotion/i;
  const isVis = el => { const st = getComputedStyle(el); return st.display !== 'none' && st.visibility !== 'hidden' && parseFloat(st.opacity || '1') > 0; };
  const headings = Array.from(root.querySelectorAll('h1,h2,h3,h4,h5,h6'));
  const nodes = Array.from(root.querySelectorAll('img,video,svg,canvas,iframe,[data-macro-name]'));
  const out = []; let i = 0;
  for (const el of nodes) {
    if (el.closest('[data-decant-id]')) continue;            // skip nested (e.g. img inside a diagram macro)
    const tag = el.tagName.toLowerCase();
    let src = el.getAttribute('src') || '';
    if (tag === 'img' && el.src) src = el.src;
    if (tag === 'video' && !src) { const s = el.querySelector('source'); if (s) src = s.src || s.getAttribute('src') || ''; src = src || el.currentSrc || ''; }
    if (tag === 'iframe') { const u = el.src || src; if (!vhost.test(u)) continue; src = u; }
    const r = el.getBoundingClientRect();
    if (tag !== 'video' && tag !== 'iframe' && (r.width < minSize || r.height < minSize)) continue;
    if (!isVis(el)) continue;
    let heading = '';
    for (const h of headings) { if (h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) heading = (h.textContent || '').trim(); else break; }
    let prev = ''; let p = el.previousElementSibling; let guard = 0;
    while (p && prev.length < 160 && guard < 6) { prev = (p.textContent || '').trim() + ' ' + prev; p = p.previousElementSibling; guard++; }
    prev = prev.replace(/\s+/g, ' ').trim().slice(-180);
    const id = 'm' + i; el.setAttribute('data-decant-id', id);
    out.push({ id, tag, src, alt: el.getAttribute('alt') || '', title: el.getAttribute('title') || '',
               w: Math.round(r.width), h: Math.round(r.height), heading, prev, order: i,
               macro: el.getAttribute('data-macro-name') || '' });
    i++;
  }
  return out;
}
"""


def autoscroll(page, step=1200, pause_ms=120, max_steps=40):
    """Scroll the page so lazy-loaded images/diagrams render, then return to top."""
    try:
        for _ in range(max_steps):
            page.evaluate("(s) => window.scrollBy(0, s)", step)
            page.wait_for_timeout(pause_ms)
            at_bottom = page.evaluate(
                "() => (window.scrollY + window.innerHeight) >= document.body.scrollHeight - 4"
            )
            if at_bottom:
                break
        page.evaluate("() => window.scrollTo(0, 0)")
    except Exception:
        pass


def _ext_for(ctype, url=""):
    if ctype in _MIME_EXT:
        return _MIME_EXT[ctype]
    ext = Path(urllib.parse.urlparse(url).path).suffix.lstrip(".").lower()
    if ext and len(ext) <= 5:
        return ext
    return (mimetypes.guess_extension(ctype or "") or ".bin").lstrip(".")


def _decode_data_uri(uri):
    head, _, data = uri.partition(",")
    mime = head[5:].split(";")[0] or "application/octet-stream"
    raw = base64.b64decode(data) if ";base64" in head else urllib.parse.unquote_to_bytes(data)
    return raw, _MIME_EXT.get(mime, "bin")


def _is_external(src):
    try:
        host = urllib.parse.urlparse(src).netloc.lower()
    except Exception:
        return False
    return any(h in host for h in _VIDEO_HOSTS)


def _get_binary(page, url):
    resp = page.request.get(url, timeout=30_000)
    if not resp.ok:
        return None, None
    body = resp.body()
    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    return body, _ext_for(ctype, url)


def _screenshot(loc):
    return loc.screenshot(type="png", timeout=15_000)


def _kind_for(tag, macro):
    if macro:
        return "diagram"
    return {"img": "image", "svg": "svg", "canvas": "diagram", "video": "video", "iframe": "video"}.get(tag)


def _item(mid, kind, file, original_file, source_url, d, occurrences, sha, *, stub=False):
    return {
        "id": mid,
        "kind": kind,
        "file": file,
        "original_file": original_file,
        "source_url": source_url or None,
        "mime": None,
        "width": d.get("w"),
        "height": d.get("h"),
        "alt": d.get("alt") or None,
        "title": d.get("title") or None,
        "sha256": sha,
        "stub": stub,
        "occurrences": occurrences,
        # --- filled later by a separate agent / vision model, NOT by decant ---
        "processed": False,
        "description": None,
        "ocr": None,
        "mermaid": None,
    }


def extract_media(page, media_dir, min_size=64):
    """Walk the rendered page, save media bytes into `media_dir`, return manifest items."""
    media_dir = Path(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    try:
        descriptors = page.evaluate(_COLLECT_JS, min_size)
    except Exception:
        descriptors = []

    items = []
    by_hash = {}
    counters = {}

    def next_id(prefix):
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"{prefix}-{counters[prefix]:03d}"

    for d in descriptors:
        tag = d["tag"]
        src = d.get("src") or ""
        kind = _kind_for(tag, d.get("macro"))
        if kind is None:
            continue
        prefix = {"image": "img", "diagram": "dia", "svg": "svg", "video": "vid"}.get(kind, "med")
        loc = page.locator(f'[data-decant-id="{d["id"]}"]').first
        occ = {
            "order": d["order"],
            "after_heading": d.get("heading") or None,
            "preceding_text": d.get("prev") or None,
        }

        primary = primary_ext = original = original_ext = None
        is_stub = False
        try:
            if tag == "img":
                if src.startswith("data:"):
                    primary, primary_ext = _decode_data_uri(src)
                elif src.startswith("http"):
                    primary, primary_ext = _get_binary(page, src)
                if primary is None:
                    primary, primary_ext = _screenshot(loc), "png"
            elif tag == "svg":
                original = loc.evaluate("el => el.outerHTML").encode("utf-8")
                original_ext = "svg"
                primary, primary_ext = _screenshot(loc), "png"
            elif tag == "canvas" or d.get("macro"):
                primary, primary_ext = _screenshot(loc), "png"
            elif tag == "video":
                if src and not _is_external(src) and src.startswith("http"):
                    primary, primary_ext = _get_binary(page, src)
                is_stub = primary is None
            elif tag == "iframe":
                is_stub = True
        except Exception:
            try:
                if kind in ("image", "diagram", "svg"):
                    primary, primary_ext = _screenshot(loc), "png"
            except Exception:
                primary = None

        if primary is None or is_stub:
            # No bytes (external video / iframe, or capture failed): keep a stub so it isn't lost.
            items.append(_item(next_id(prefix), kind, None, None, src, d, [occ], None, stub=True))
            continue

        sha = hashlib.sha256(primary).hexdigest()
        if sha in by_hash:
            by_hash[sha]["occurrences"].append(occ)
            continue

        mid = next_id(prefix)
        fname = f"{mid}.{primary_ext or 'bin'}"
        (media_dir / fname).write_bytes(primary)
        original_rel = None
        if original is not None:
            oname = f"{mid}.{original_ext}"
            (media_dir / oname).write_bytes(original)
            original_rel = f"media/{oname}"

        item = _item(mid, kind, f"media/{fname}", original_rel, src, d, [occ], sha)
        item["mime"] = mimetypes.types_map.get("." + (primary_ext or ""), None)
        item["bytes"] = len(primary)
        by_hash[sha] = item
        items.append(item)

    return items


def weave_media(md, items, media_subdir="media"):
    """Rewrite inline image links to local files and append an appendix for the rest."""
    import re

    matched = set()
    for it in items:
        if not it.get("file"):
            continue
        url = it.get("source_url")
        local = f"{media_subdir}/{Path(it['file']).name}"
        if url:
            pat = re.compile(r'(!?\[[^\]]*\])\(' + re.escape(url) + r'(?:\s+"[^"]*")?\)')
            md, n = pat.subn(lambda m: f'{m.group(1)}({local}) <!-- decant:media id={it["id"]} -->', md)
            if n:
                matched.add(it["id"])

    leftover = [it for it in items if it["id"] not in matched]
    if leftover:
        lines = ["", "## Медиа (вне текста)", ""]
        for it in leftover:
            ref = (f"{media_subdir}/{Path(it['file']).name}" if it.get("file")
                   else it.get("source_url") or "—")
            occ = (it.get("occurrences") or [{}])[0]
            where = occ.get("after_heading")
            near = occ.get("preceding_text")
            tag = "🎬" if it["kind"] == "video" else "🖼"
            extra = "".join([
                f" — после «{where}»" if where else "",
                f" — рядом: «{near}»" if near else "",
                " — *внешний эмбед, без байтов*" if it.get("stub") else "",
            ])
            lines.append(f'- {tag} `{it["id"]}` ({it["kind"]}): [{ref}]({ref}){extra}')
        md = md.rstrip() + "\n" + "\n".join(lines) + "\n"
    return md


def write_manifest(page_dir, page_url, title, items, captured_at=None):
    page_dir = Path(page_dir)
    page_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "page_url": page_url,
        "title": title,
        "captured_at": captured_at or datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "media": items,
    }
    (page_dir / "media.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
