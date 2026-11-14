"""Smoke-test a deployed (or local) Blueprint Evidence app over HTTP.

    python deploy/smoke_test.py [URL] [--unseen-dxf PATH]

Asks the tiling question on a Swiss PDF, an IFC area, a CubiCasa count and an unseen DXF upload, plus the
upload limits and the credit endpoints. Every ask uses refresh=true, so the answer cache is bypassed.
Prints one line per check with the HTTP round trip and the server-side solve time.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

DEFAULT_URL = "https://muilu-markus--blueprint-evidence.us-east.modal.run"
REPO = Path(__file__).resolve().parent.parent

ASKS = [
    ("tiling, Swiss PDF (DXF twin)", "data/swiss_dwellings/site3387_bldg5238_floor23498.pdf",
     "Kuinka monta neliötä laattaa kylpyhuoneisiin?", False, None),
    ("tiling, Swiss PDF only", "data/swiss_dwellings/site3387_bldg5238_floor23498.pdf",
     "Kuinka monta neliötä laattaa kylpyhuoneisiin?", True, None),
    ("IFC area", "data/ifc_models/kit-fzk-haus_ac20-fzk-haus_plan-01-erdgeschoss.pdf",
     "What is the net floor area of the room 'Küche (6)' on 'Erdgeschoss', in m²?", False, 16.31),
    ("IFC tiling (molio)", "data/ifc_models/bsc-molio_molio-with-uris_plan-04-1-sal.pdf",
     "How many square meters of tiling should I order for the toilets?", False, None),
    ("CubiCasa count", "data/cubicasa5k/high_quality_architectural-10372.png",
     "How many windows are shown on this floor plan?", False, 12),
    ("CubiCasa count (toilets)", "data/cubicasa5k/high_quality_architectural-10372.png",
     "How many toilet bowls (WC seats) are drawn on this floor plan?", False, 2),
    ("CubiCasa count, 2nd image", "data/cubicasa5k/colorful-11960.png",
     "How many windows are shown on this floor plan?", False, None),
]


def req(url, data=None, headers=None, method=None, timeout=600):
    r = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            body = resp.read()
            code = resp.status
    except urllib.error.HTTPError as e:
        body, code = e.read(), e.code
    except urllib.error.URLError as e:     # the server answered early and closed (oversized upload)
        body, code = f"connection closed by server before the body was sent ({e.reason})".encode(), 0
    return code, body, time.perf_counter() - t0


def ask(base, path, question, pdf_only=False):
    data = json.dumps({"path": path, "question": question, "pdf_only": pdf_only, "refresh": True}).encode()
    code, body, dt = req(base + "/api/ask", data, {"Content-Type": "application/json"})
    return code, (json.loads(body) if code == 200 else body.decode(errors="replace")[:300]), dt


def multipart(files):
    b = uuid.uuid4().hex
    out = io.BytesIO()
    for name, data in files:
        out.write(f"--{b}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{name}\"\r\n"
                  f"Content-Type: application/octet-stream\r\n\r\n".encode())
        out.write(data)
        out.write(b"\r\n")
    out.write(f"--{b}--\r\n".encode())
    return out.getvalue(), {"Content-Type": f"multipart/form-data; boundary={b}"}


def line(label, code, rec, dt, gold=None):
    if isinstance(rec, dict):
        v = rec.get("value")
        extra = f"value={v} {rec.get('unit') or ''} status={rec.get('status')} route={rec.get('route')} " \
                f"solve={rec.get('time_ms')}ms evidence={'yes' if rec.get('evidence_id') else 'no'}"
        if gold is not None:
            extra += f" gold={gold}"
        if rec.get("abstain"):
            extra += f" abstain={str(rec['abstain'])[:120]}"
    else:
        extra = str(rec)
    print(f"{label:32s} HTTP {code}  {dt:6.2f}s  {extra}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default=DEFAULT_URL)
    ap.add_argument("--unseen-dxf", default=str(REPO.parent / "data" / "unseen" / "kjdraw_architecture_floor_plan"
                                                / "architecture-floor-plan.dxf"))
    ap.add_argument("--only", help="run only checks whose label contains this text")
    args = ap.parse_args()
    base = args.url.rstrip("/")
    sel = lambda label: not args.only or args.only.lower() in label.lower()

    code, body, dt = req(base + "/api/status")
    print(f"{'status':32s} HTTP {code}  {dt:6.2f}s  {body.decode()[:200]}", flush=True)

    for label, path, q, pdf_only, gold in ASKS:
        if sel(label):
            code, rec, dt = ask(base, path, q, pdf_only)
            line(label, code, rec, dt, gold)
            if code == 200 and rec.get("evidence_id"):
                c2, b2, d2 = req(f"{base}/api/evidence/{rec['evidence_id']}.png")
                print(f"{'  evidence png':32s} HTTP {c2}  {d2:6.2f}s  {len(b2)} bytes", flush=True)

    if sel("upload"):
        dxf = Path(args.unseen_dxf)
        data, hdr = multipart([(dxf.name, dxf.read_bytes())])
        code, body, dt = req(base + "/api/upload", data, hdr)
        print(f"{'upload unseen DXF':32s} HTTP {code}  {dt:6.2f}s  {body.decode()[:160]}", flush=True)
        if code == 200:
            ref = json.loads(body)["path"]
            for q, gold in (("How many doors (interior doors and entrance doors together) are drawn on this floor plan?", 12),
                            ("How many windows are drawn on this floor plan?", 6)):
                code, rec, dt = ask(base, ref, q)
                line("  ask uploaded DXF", code, rec, dt, gold)
            code, body, dt = req(base + "/api/credits?path=" + urllib.parse.quote(ref))
            print(f"{'  credit of upload':32s} HTTP {code}  {dt:6.2f}s  {body.decode()[:160]}", flush=True)
        data, hdr = multipart([("notes.txt", b"hello")])
        code, body, dt = req(base + "/api/upload", data, hdr)
        print(f"{'upload .txt (expect 400)':32s} HTTP {code}  {dt:6.2f}s  {body.decode()[:120]}", flush=True)
        data, hdr = multipart([("big.pdf", b"0" * (31 * 1024 * 1024))])
        code, body, dt = req(base + "/api/upload", data, hdr)
        print(f"{'upload 31 MB (expect 413)':32s} HTTP {code}  {dt:6.2f}s  {body.decode()[:120]}", flush=True)

    code, body, dt = req(base + "/api/credits?path=data/cubicasa5k/high_quality_architectural-10372.png")
    print(f"{'credit, CubiCasa':32s} HTTP {code}  {dt:6.2f}s  {body.decode()[:160]}", flush=True)
    code, body, dt = req(base + "/")
    print(f"{'index has credit script':32s} HTTP {code}  {dt:6.2f}s  {b'/deploy/credits.js' in body}", flush=True)
    code, body, dt = req(base + "/api/overview")
    print(f"{'results overview':32s} HTTP {code}  {dt:6.2f}s  {body.decode()[:200]}", flush=True)
    code, body, dt = req(base + "/api/deploy")
    print(f"{'deploy facts':32s} HTTP {code}  {dt:6.2f}s  {body.decode()}", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import urllib.parse  # noqa: F401
    main()
