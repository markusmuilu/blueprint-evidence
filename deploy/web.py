"""The public demo: the Blueprint Evidence app (app/server.py, unchanged) with deployment limits and credits.

Wraps the existing FastAPI app instead of editing it:
- data root: the credited demo set (`STOA_DATA`, set before the app is imported)
- uploads: PDF, DXF, IFC, PNG, JPG only; 30 MB per request; stored in the container's temp folder and
  deleted, with every cache file derived from them, UPLOAD_TTL seconds after upload (and with the container)
- credits: `/api/credits?path=` and `/credits`, and a credit bar injected into the page that names the
  licence and credit of the drawing on screen
- `/api/deploy`: memory, CPU and demo-set facts for sizing (no secrets; Nemotron only as on/off)

Run locally: `python -m deploy.web` (port 8000), with STOA_DATA unset it uses deploy/demo_data.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import shutil
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEMO = Path(os.environ.get("STOA_DATA") or HERE / "demo_data")
os.environ["STOA_DATA"] = str(DEMO)
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

MAX_BYTES = int(os.environ.get("BPE_MAX_UPLOAD_MB", "30")) * 1024 * 1024
UPLOAD_EXT = {"pdf", "dxf", "ifc", "png", "jpg", "jpeg"}
UPLOAD_TTL = int(os.environ.get("BPE_UPLOAD_TTL", "900"))      # seconds an upload is kept

from fastapi import Request                      # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, Response   # noqa: E402
from starlette.routing import Mount              # noqa: E402

from app import server                           # noqa: E402

server.UPLOAD_EXT = UPLOAD_EXT                   # read at call time by server.upload
server.MAX_UPLOAD = MAX_BYTES
app = server.app

STARTED = time.time()
CREDITS: dict = {}
if (DEMO / "credits.json").exists():
    CREDITS = json.loads((DEMO / "credits.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- upload limits and cleanup

class UploadLimit:
    """Reject upload requests over MAX_BYTES before the body is read (Content-Length, then streamed bytes)."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/api/upload":
            return await self.inner(scope, receive, send)
        limit = MAX_BYTES + 256 * 1024                       # multipart framing
        headers = dict(scope.get("headers") or [])
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = 0
        if length > limit:
            await self._drain(receive)
            return await self._too_big(send)
        seen = [0]

        async def counted():
            msg = await receive()
            if msg["type"] == "http.request":
                seen[0] += len(msg.get("body") or b"")
                if seen[0] > limit:
                    if msg.get("more_body"):
                        await self._drain(receive)
                    raise _TooBig()
            return msg
        try:
            return await self.inner(scope, counted, send)
        except _TooBig:
            return await self._too_big(send)

    @staticmethod
    async def _drain(receive, cap: int = 256 * 1024 * 1024):
        """Read and discard the rest of the body, so the client gets the 413 instead of a reset connection."""
        n = 0
        while n < cap:
            msg = await receive()
            if msg["type"] != "http.request":
                return
            n += len(msg.get("body") or b"")
            if not msg.get("more_body"):
                return

    @staticmethod
    async def _too_big(send):
        body = json.dumps({"detail": f"upload larger than {MAX_BYTES // (1024 * 1024)} MB"}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class _TooBig(Exception):
    pass


def _purge_upload(uid: str):
    shutil.rmtree(server.UPLOADS / uid, ignore_errors=True)
    tag = f"u{uid}"
    out = REPO / "out"
    if out.is_dir():
        for p in out.rglob(f"*{tag}*"):          # evidence, render, OCR and detector caches of this upload
            try:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
            except OSError:
                pass
    # cached answers name the upload in their JSON, not their file name
    for p in server.ANSWER_CACHE.glob("*.json"):
        try:
            if f'"upload:{uid}/' in p.read_text(encoding="utf-8"):
                p.unlink()
        except OSError:
            pass


def _cleaner():
    while True:
        time.sleep(60)
        now = time.time()
        for d in list(server.UPLOADS.iterdir()) if server.UPLOADS.is_dir() else []:
            try:
                if d.is_dir() and now - d.stat().st_mtime > UPLOAD_TTL:
                    _purge_upload(d.name)
            except OSError:
                pass


threading.Thread(target=_cleaner, daemon=True, name="upload-cleaner").start()


# ---------------------------------------------------------------- credits

def credit_for(path: str) -> dict:
    if path.startswith("upload:"):
        return {"source": "Your upload", "licence": "yours",
                "credit": f"Kept in temporary storage only and deleted {UPLOAD_TTL // 60} minutes after upload."}
    c = CREDITS.get(path)
    if c is None:
        return {"source": "unknown", "licence": "see /credits", "credit": ""}
    return c


@app.get("/api/credits", include_in_schema=False)
def api_credits(path: str = ""):
    if path:
        return credit_for(path)
    return CREDITS


@app.get("/credits", include_in_schema=False, response_class=HTMLResponse)
def credits_page():
    rows = "".join(
        f"<tr><td class=m>{html.escape(p)}</td><td>{html.escape(c.get('licence', ''))}</td>"
        f"<td>{html.escape(c.get('credit', ''))}"
        + (f" <a href='{html.escape(c['url'])}' rel=noopener>source</a>" if c.get("url", "").startswith("http") else "")
        + "</td></tr>" for p, c in CREDITS.items())
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1"><title>Demo set credits</title>
<style>body{{background:#0d1117;color:#c9d1d9;font:14px/1.5 system-ui,sans-serif;margin:0;padding:24px 16px;max-width:1100px}}
a{{color:#85b7e0}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #30363d;padding:6px 8px;
text-align:left;vertical-align:top}}.m{{font-family:ui-monospace,monospace;font-size:12px;word-break:break-all}}</style></head><body>
<p><a href="/">Back to the app</a></p><h1>Demo set credits</h1>
<p>A small set of drawings from Stoa's Junction 2026 challenge dataset, used for the challenge only
(non-commercial), plus two public drawings the system never saw in development. Each source folder's
LICENSE.md is in the demo set; evidence overlays carry the licence of their drawing (CubiCasa overlays are
non-commercial and ShareAlike). NoDerivatives files are not in this set.</p>
<table><tr><th>file</th><th>licence</th><th>credit</th></tr>{rows}</table></body></html>"""


CREDIT_JS = r"""
(function () {
  const bar = document.createElement("div");
  bar.id = "bpe-credit";
  bar.setAttribute("role", "note");
  bar.style.cssText = "position:fixed;left:0;right:0;bottom:0;z-index:50;padding:4px 16px;font:12px/1.4 'IBM Plex Sans',system-ui,sans-serif;" +
    "background:rgba(13,17,23,.92);color:#8b949e;border-top:1px solid #30363d;white-space:nowrap;overflow:hidden;text-overflow:ellipsis";
  const base = "Demo set from Stoa's Junction 2026 challenge data, non-commercial use. ";
  const link = '<a href="/credits" style="color:#85b7e0">All credits</a>';
  const esc = s => String(s || "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  bar.innerHTML = base + link;
  document.addEventListener("DOMContentLoaded", () => document.body.appendChild(bar));
  async function show(path) {
    try {
      const c = await (await window.__bpeFetch("/api/credits?path=" + encodeURIComponent(path))).json();
      bar.innerHTML = link + " | <strong style='color:#c9d1d9'>" + esc(c.licence || "licence: see credits") + "</strong>. " +
        esc(c.credit) +
        (c.url && /^https?:/.test(c.url) ? ' <a href="' + esc(c.url) + '" rel="noopener" target="_blank" style="color:#85b7e0">source</a>' : "");
      bar.title = bar.textContent;
    } catch (e) { /* keep the generic line */ }
  }
  window.__bpeFetch = window.fetch.bind(window);
  const MAX = __MAX_BYTES__;
  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    if (/\/api\/upload/.test(url) && init && init.body instanceof FormData) {
      let total = 0;
      for (const v of init.body.values()) if (v && typeof v.size === "number") total += v.size;
      if (total > MAX) {
        const detail = "Upload larger than " + Math.round(MAX / 1048576) + " MB; this public demo takes up to that size.";
        return Promise.resolve(new Response(JSON.stringify({detail}), {status: 413, headers: {"Content-Type": "application/json"}}));
      }
    }
    const m = url.match(/\/api\/drawing\?path=([^&]+)/);
    if (m) show(decodeURIComponent(m[1]));
    return window.__bpeFetch(input, init);
  };
})();
"""


@app.get("/deploy/credits.js", include_in_schema=False)
def credits_js():
    return Response(CREDIT_JS.replace("__MAX_BYTES__", str(MAX_BYTES)), media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})


def _index() -> HTMLResponse:
    page = (server.STATIC / "index.html").read_text(encoding="utf-8")
    tag = '<script src="/deploy/credits.js"></script>'
    page = page.replace("<head>", "<head>\n" + tag, 1) if "<head>" in page else tag + page
    # The static files carry no cache headers, so a browser can keep an old app.css next to a new page
    # (14 Nov: the new panel grips then took grid cells). A content hash per file forces a fresh copy on each deploy.
    for name in ("app.css", "app.js", "ordersheet.js", "viewer3d.js"):
        f = server.STATIC / name
        if f.exists():
            v = hashlib.sha1(f.read_bytes()).hexdigest()[:10]
            page = page.replace(f'"{name}"', f'"{name}?v={v}"')
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
def index():
    return _index()


@app.get("/index.html", include_in_schema=False)
def index_html():
    return _index()


# ---------------------------------------------------------------- sizing facts

def _cg(name: str):
    for base in ("/sys/fs/cgroup/", "/sys/fs/cgroup/memory/"):
        p = Path(base + name)
        if p.exists():
            try:
                return p.read_text().strip()
            except OSError:
                return None
    return None


@app.get("/api/deploy", include_in_schema=False)
def deploy_status():
    from bpqa import nemotron
    mb = lambda v: round(int(v) / 2**20) if v and v.isdigit() else None
    rss = None
    try:
        import resource
        rss = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    except Exception:
        pass
    return {
        "memory_current_mb": mb(_cg("memory.current") or _cg("memory.usage_in_bytes")),
        "memory_peak_mb": mb(_cg("memory.peak") or _cg("memory.max_usage_in_bytes")),
        "process_max_rss_mb": rss,
        "cpus_visible": os.cpu_count(),
        "process_cpu_s": round(sum(os.times()[:2]), 1),
        "uptime_s": round(time.time() - STARTED, 1),
        "torch_threads": _torch_threads(),
        "nemotron_enabled": nemotron.enabled(),
        "demo_drawings": len(server.CATALOG.drawings),
        "upload_limit_mb": MAX_BYTES // 2**20,
        "upload_ttl_s": UPLOAD_TTL,
        "uploads_held": len(list(server.UPLOADS.iterdir())) if server.UPLOADS.is_dir() else 0,
    }


def _torch_threads():
    import sys
    t = sys.modules.get("torch")
    return t.get_num_threads() if t else None


# ---------------------------------------------------------------- prewarm

def _prewarm():
    """Load torch, the detector weights and the OCR engine at container start (a few seconds of CPU), so the
    first scan question pays only for its own inference. Runs one tiny inference per model."""
    t0 = time.perf_counter()
    try:
        import numpy as np
        from bpqa.routes import detector, ocr
        names = {c["model"] for k, c in detector.thresholds().items()
                 if not k.startswith("_") and isinstance(c, dict) and c.get("model")} or {detector.MODEL.name}
        blank = np.full((640, 640, 3), 255, np.uint8)
        for name in sorted(names):
            path = detector.REPO / "models" / name
            if path.exists():
                detector._model(str(path)).predict(blank, imgsz=640, verbose=False, device="cpu")
        ocr._engine()(blank)
    except Exception as ex:          # prewarm must never take the app down
        print(f"[prewarm] {type(ex).__name__}: {ex}", flush=True)
    print(f"[prewarm] models loaded in {time.perf_counter() - t0:.1f}s", flush=True)
    if os.environ.get("BPE_PRECOMPUTE", "1") == "0":
        return
    # detector and OCR results are cached per image on disk; fill that cache for the demo scans, smallest
    # first, so a judge's first question on one answers in under a second instead of 3 to 26 s on CPU.
    # Each goes through the normal ask (and its lock), so a user's question waits behind at most one image.
    scans = sorted((d for d in server.CATALOG.drawings.values() if d.ext in ("png", "jpg", "jpeg")),
                   key=lambda d: (DEMO / d.path).stat().st_size if (DEMO / d.path).exists() else 0)
    for d in scans:
        try:
            server.ask(server.AskBody(path=d.path, question="How many windows are shown on this floor plan?"))
            server.ask(server.AskBody(path=d.path, question="How many bedrooms are on this floor plan?"))
        except Exception as ex:
            print(f"[prewarm] {d.path}: {type(ex).__name__}: {ex}", flush=True)
    print(f"[prewarm] {len(scans)} demo scans cached in {time.perf_counter() - t0:.1f}s", flush=True)


if os.environ.get("BPE_PREWARM", "1") != "0":
    threading.Thread(target=_prewarm, daemon=True, name="prewarm").start()


# the static mount serves "/" and must stay last, after the routes added here
_mounts = [r for r in app.router.routes if isinstance(r, Mount)]
for _m in _mounts:
    app.router.routes.remove(_m)
    app.router.routes.append(_m)

asgi = UploadLimit(app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(asgi, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), log_level="warning")
