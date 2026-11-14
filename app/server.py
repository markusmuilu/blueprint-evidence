"""FastAPI backend for Blueprint Evidence.

POST /api/ask           {path, question, pdf_only?} -> answer JSON from bpqa.router.solve, plus timing,
                         status tier, "where" and numbered "why" lines, evidence id
POST /api/upload        multipart files -> {path: "upload:<id>/<name>"}; stored in the OS temp folder
GET  /api/drawings      browse the dataset: ?source=&q=&ext=&offset=&limit=
GET  /api/drawing       ?path= -> one drawing with its dataset questions
GET  /api/preview       ?path= -> PNG of the drawing (PNG/JPG as is, PDF page 1 rendered)
GET  /api/evidence/<id>.png[?body=1]  evidence overlay (body=1 drops the text header strip)
GET  /api/results       eval runs; /api/results/<run> one run's tables
GET  /api/status        data root, detector and OCR availability
GET  /api/ifc3d...      3D view of an IFC model per storey (app/ifc3d.py)
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bpqa.data import Question
from bpqa.router import Context, solve

from bpqa.evidence import marks_path

from . import costs
from . import ifc3d
from . import results as results_mod
from .catalog import SOURCE_NAMES, Catalog, find_data_root

REPO = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
APP_OUT = REPO / "out" / "app"
EVIDENCE_DIR = APP_OUT / "evidence"     # one PNG per cached answer, named by the cache key
RENDER_DIR = APP_OUT / "render"         # where the routes draw; copied to EVIDENCE_DIR after each solve
ANSWER_CACHE = APP_OUT / "answers"
PREVIEW_CACHE = APP_OUT / "preview"
UPLOADS = Path(tempfile.gettempdir()) / "blueprint-evidence" / "uploads"
UPLOAD_EXT = {"pdf", "dxf", "ifc", "png", "jpg", "jpeg", "tif", "tiff"}
MAX_UPLOAD = 200 * 1024 * 1024
CACHE_VERSION = "5"       # 5: approximations on scans without printed areas (approx field); 4: wall openings z0_m / z1_m

for d in (EVIDENCE_DIR, RENDER_DIR, ANSWER_CACHE, PREVIEW_CACHE, UPLOADS):
    d.mkdir(parents=True, exist_ok=True)

DATA_ROOT = find_data_root()
CATALOG = Catalog(DATA_ROOT)
SOLVE_LOCK = threading.Lock()     # routes keep module-level caches; one solve at a time

app = FastAPI(title="Blueprint Evidence", docs_url="/api/docs", redoc_url=None)


# ---------------------------------------------------------------- drawings and uploads

def resolve(ref: str) -> tuple[Path, str]:
    """A drawing reference -> (root, dataset-style relative path). Raises 404/400 on anything else."""
    ref = ref.strip().replace("\\", "/")
    if ref.startswith("upload:"):
        m = re.fullmatch(r"upload:([0-9a-f]{12})/([^/]+)", ref)
        if not m:
            raise HTTPException(400, "bad upload reference")
        root = UPLOADS / m.group(1)
        rel = f"data/upload/{m.group(2)}"
    else:
        if DATA_ROOT is None:
            raise HTTPException(404, "no dataset configured; set STOA_DATA or upload a drawing")
        root, rel = DATA_ROOT, ref
    f = (root / rel).resolve()
    if not f.is_file() or root.resolve() not in f.parents:
        raise HTTPException(404, f"drawing not found: {ref}")
    return root, rel


def display_name(stored: str) -> str:
    return re.sub(r"^u[0-9a-f]{12}-", "", stored)


def _safe_name(name: str) -> str:
    name = Path(name.replace("\\", "/")).name
    name = re.sub(r"[^\w.\- ]+", "_", name).strip(" .")
    return name[:120] or "drawing"


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    """Store one drawing, optionally with twins of the same name (a PDF and its DXF), in one folder."""
    uid = uuid.uuid4().hex[:12]
    folder = UPLOADS / uid / "data" / "upload"
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    for uf in files:
        name = _safe_name(uf.filename or "drawing")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in UPLOAD_EXT:
            shutil.rmtree(UPLOADS / uid, ignore_errors=True)
            raise HTTPException(400, f"{name}: use PDF, DXF, IFC, PNG or JPG")
        data = await uf.read()
        if len(data) > MAX_UPLOAD:
            shutil.rmtree(UPLOADS / uid, ignore_errors=True)
            raise HTTPException(413, f"{name}: larger than 200 MB")
        # the OCR and detector caches are keyed by file stem, so every upload gets a unique stem;
        # twins uploaded together keep a shared stem
        stored = f"u{uid}-{name}"
        (folder / stored).write_bytes(data)
        saved.append({"name": name, "stored": stored, "ext": ext, "bytes": len(data)})
    if not saved:
        raise HTTPException(400, "no file")
    # the drawing to ask about: a PDF or image over its DXF/IFC twin
    order = {"pdf": 0, "png": 1, "jpg": 1, "jpeg": 1, "tif": 1, "tiff": 1, "ifc": 2, "dxf": 3}
    main = sorted(saved, key=lambda s: order.get(s["ext"], 9))[0]
    return {"path": f"upload:{uid}/{main['stored']}", "name": main["name"], "ext": main["ext"], "files": saved}


@app.get("/api/drawings")
def drawings(source: str = "", q: str = "", ext: str = "", offset: int = 0, limit: int = Query(60, le=200)):
    res = CATALOG.search(source=source, q=q, ext=ext, offset=max(offset, 0), limit=limit)
    res["sources"] = CATALOG.sources()
    return res


@app.get("/api/drawing")
def drawing(path: str):
    if path.startswith("upload:"):
        root, rel = resolve(path)
        name = rel.rsplit("/", 1)[-1]
        twins = sorted(p.suffix[1:] for p in (root / rel).parent.iterdir() if p.name != name)
        return {"path": path, "name": display_name(name), "source": "upload", "ext": name.rsplit(".", 1)[-1].lower(),
                "split": None, "n_questions": 0, "types": [], "twins": twins, "questions": []}
    d = CATALOG.detail(path)
    if d is None:
        raise HTTPException(404, "not in the dataset")
    return d


@app.get("/api/preview")
def preview(path: str):
    root, rel = resolve(path)
    f = root / rel
    ext = f.suffix.lower()[1:]
    if ext in ("png", "jpg", "jpeg"):
        return FileResponse(f)
    if ext != "pdf":
        raise HTTPException(415, f"no preview for .{ext}; ask a question to render the evidence")
    st = f.stat()
    key = hashlib.sha1(f"{f}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]
    out = PREVIEW_CACHE / f"{key}.png"
    if not out.exists():
        import fitz
        with fitz.open(f) as doc:
            page = doc[0]
            zoom = min(4.0, 2000 / max(page.rect.width, page.rect.height, 1))
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            pix.save(str(out))
    return FileResponse(out, media_type="image/png")


# ---------------------------------------------------------------- answering

ROUTES = {
    "dxf-geometry": ("DXF geometry", "Read from the DXF layers of the same drawing: exact geometry, no vision model."),
    "ifc": ("IFC model", "Read from the IFC model: entities, storey containment and quantity sets."),
    "pdf-text": ("PDF text layer", "Read from the PDF text layer and its schedule tables."),
    "dxf-generic": ("DXF layers, generic", "Read from the DXF's layer and block names (AIA, ISO 13567 or plain words in several "
                    "languages), not a known office's layer convention."),
    "pdf-vector": ("PDF linework", "Read from the PDF's own vector linework and text, without a CAD twin."),
    "pdf-generic": ("PDF, convention-free", "Door swings or printed room labels read from the PDF without a recognised "
                    "drawing convention, so the count is an estimate."),
    "nemotron-estimate": ("Nemotron estimate", "Last resort after every local route abstained: NVIDIA Nemotron read the image "
                          "and named the regions it used. The reply was validated and the regions are drawn as given; "
                          "nothing checked them, so this is an estimate."),
    "raster-ocr-labels": ("Scan, room labels", "Room labels on the scan read by a local OCR model and matched to room types."),
    "raster-detector": ("Scan, symbol detector", "Symbols on the scan counted by a local object detector above a tuned score threshold."),
    "raster-cv": ("Scan, panel analysis", "Separate drawing panels found on the scan by image analysis."),
    "raster-cv-fixtures": ("Scan, outlines", "Closed outlines on the scan classified as fixtures."),
    "ifc-props": ("IFC properties", "Looked up in the IFC model's attributes and property sets; the answer lists the IDs of the elements it read."),
    "raster-area": ("Scan, printed areas", "Room areas printed on the scan, read by a local OCR model and attached to their room labels. "
                    "A room with no printed area is measured from its traced outline and a pixel scale."),
    "raster-prior": ("Baseline prior", "Most common answer for this question type. A baseline, not visual proof."),
    "parser": ("Question parser", "The question was not turned into a typed query."),
    "router": ("Router", "No route reads this file type."),
}
PROVEN_ROUTES = {"dxf-geometry", "dxf-generic", "ifc", "ifc-props", "pdf-text", "pdf-vector"}
ESTIMATE_ROUTES = {"pdf-generic", "nemotron-estimate", "nemotron"}     # never proven, whatever the confidence


def tier(a: dict) -> tuple[str, str]:
    """(status, rule). An explicit tier from the router wins; otherwise route family and confidence."""
    if a.get("abstain") is not None or a.get("value") is None:
        return "cannot", "No route could prove an answer, so none is given."
    explicit = str(a.get("tier") or a.get("status") or "").lower()
    reason = (a.get("tier_reason") or "").strip()
    if a.get("route") in ESTIMATE_ROUTES:
        return "estimate", "Estimate: " + (reason or "this route never proves an answer") + "."
    if explicit.startswith("estim"):
        return "estimate", "The route marked this answer as an estimate" + (f": {reason}." if reason else ".")
    if explicit.startswith("proven"):
        return "proven", f"The route marked this answer as proven (confidence {a.get('confidence')})."
    if a.get("route") in PROVEN_ROUTES and (a.get("confidence") or 0) >= 0.8:
        return "proven", "Structured route (CAD, BIM or PDF data) with confidence of at least 0.8."
    return "estimate", "Answer read from pixels or with confidence under 0.8: verify the numbered items."


def scale_source(a: dict, query: dict, ext: str) -> str | None:
    if query.get("measure") in (None, "count", "text"):
        return None
    route = a.get("route")
    if route == "dxf-geometry":
        return "DXF model units in metres; room outline area by the shoelace formula."
    if route == "ifc":
        return "IFC project units; NetFloorArea from quantity sets, else the space footprint."
    if route == "raster-area" and a.get("approx"):
        ap = a["approx"]
        return (f"Approximation: {ap['why_no_scale']}. {ap['scale']} The scale objects are boxed dashed amber "
                f"on the evidence, each with its size.")
    if route in ("pdf-vector", "pdf-generic"):
        m = re.search(r"; (scale .*)$", a.get("explanation") or "")
        return (m.group(1) if m else "Scale found on the sheet") + ". The text that set the scale is boxed blue on the evidence."
    return None


def where(a: dict, query: dict, rel: str) -> list[dict]:
    rows = [{"k": "Drawing", "v": display_name(rel.rsplit("/", 1)[-1])}]
    route = a.get("route") or ""
    rows.append({"k": "Route", "v": ROUTES.get(route, (route,))[0], "code": route})
    scope = []
    for key, label in (("level", "Level"), ("apartment", "Apartment"), ("room", "Room"), ("label", "Label")):
        if query.get(key):
            scope.append(f"{label} {query[key]}")
    rows.append({"k": "Part", "v": ", ".join(scope) if scope else "Whole drawing"})
    return rows


def why(a: dict, query: dict, ext: str) -> list[dict]:
    """Numbered evidence lines: method, reading, scale, checks, items."""
    out = []
    route = a.get("route") or ""
    out.append({"kind": "method", "text": ROUTES.get(route, (route, f"Route {route}."))[1]})
    q = {k: v for k, v in query.items() if k in ("measure", "obj", "scope", "level", "room", "apartment", "lang", "parser")}
    ex = query.get("extra") or {}
    if query.get("parser") == "llm" and ex.get("reading") and route != "parser":
        # a language-model reading is said plainly, so a person can catch a misreading
        why_llm = ex.get("llm_reason") or "the rule parser did not read the question"
        out.append({"kind": "query", "text": f"Question read by the language model as: {ex['reading']}. "
                                             f"(Asked because {why_llm}.) Check that this is what you asked."})
    elif q and route != "parser":
        bits = [f"{q.get('measure', '?')} of {q.get('obj', '?')}"]
        if q.get("lang"):
            bits.append(f"language {q['lang']}")
        bits.append(f"parsed by {q.get('parser', 'rules')}")
        out.append({"kind": "query", "text": "Question read as " + ", ".join(bits) + "."})
    expl = a.get("explanation") or ""
    sc = scale_source(a, query, ext)
    if route in ("pdf-vector", "pdf-generic") and "; scale" in expl:
        expl = expl.split("; scale")[0]          # the scale gets its own line
    if expl:
        out.append({"kind": "derivation", "text": expl})
    if sc:
        out.append({"kind": "scale", "text": sc})
    for c in a.get("checks") or []:
        out.append({"kind": "check", "status": c.get("status"), "text": f"{c.get('name')}: {c.get('detail')}"})
    if a.get("abstain"):
        out.append({"kind": "abstain", "text": a["abstain"]})
    return out


def _evidence_geometry(ev: Path, src: Path | None) -> dict:
    """Header height of the evidence PNG, and the scale from source pixels to evidence pixels."""
    from PIL import Image
    import numpy as np
    with Image.open(ev) as im:
        w, h = im.size
        arr = np.asarray(im.convert("RGB"))
    head = 0
    # the header strip ends with a full-width light grey rule (evidence.finish draws it at head_h - 2)
    for y in range(min(h, 400)):
        row = arr[y, : min(w, 900)]
        if (np.abs(row.astype(int) - 200) <= 2).all():
            head = y + 2
            break
    geo = {"w": w, "h": h, "header": head}
    if src is not None and src.suffix.lower() in (".png", ".jpg", ".jpeg"):
        try:
            with Image.open(src) as s:
                sw = s.width
            body_w = min(sw, 1600)
            geo["scale"] = body_w / sw
        except OSError:
            pass
    return geo


def _cache_key(root: Path, rel: str, question: str, pdf_only: bool) -> str:
    f = root / rel
    st = f.stat()
    raw = json.dumps([CACHE_VERSION, str(f), st.st_size, int(st.st_mtime), question.strip(), pdf_only])
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


class AskBody(BaseModel):
    path: str
    question: str
    pdf_only: bool = False
    refresh: bool = False


@app.post("/api/ask")
def ask(body: AskBody):
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "empty question")
    if len(question) > 1000:
        raise HTTPException(400, "question longer than 1000 characters")
    root, rel = resolve(body.path)
    key = _cache_key(root, rel, question, body.pdf_only)
    cached = ANSWER_CACHE / f"{key}.json"
    if cached.exists() and not body.refresh:
        rec = json.loads(cached.read_text(encoding="utf-8"))
        ev = rec.get("evidence_id")
        if not ev or (EVIDENCE_DIR / f"{ev}.png").exists():
            rec["cached"] = True
            return rec

    q = Question(qid=-1, path=rel, question=question)
    ctx = Context(root=root, q=q, out_dir=RENDER_DIR, evidence=True, pdf_only=body.pdf_only)
    with SOLVE_LOCK:
        t0, c0 = time.perf_counter(), time.process_time()
        query, ans = solve(ctx)
        ms = (time.perf_counter() - t0) * 1000
        cpu_s = time.process_time() - c0
    a = ans.as_dict()
    qd = query.as_dict()

    ev_id, geo, marks = None, None, None
    if a.get("evidence"):
        drawn = Path(a["evidence"])
        if drawn.exists() and RENDER_DIR.resolve() in drawn.resolve().parents:
            # the routes name evidence by drawing and question only, so a re-run with other
            # options would overwrite it; keep a copy per answer
            ev_id = f"{drawn.stem[:60]}__{key[:10]}"
            evp = EVIDENCE_DIR / f"{ev_id}.png"
            shutil.copyfile(drawn, evp)
            marks = None
            mk = marks_path(drawn)
            if mk.exists():
                shutil.copyfile(mk, marks_path(evp))
                try:
                    marks = json.loads(mk.read_text(encoding="utf-8"))
                except ValueError:
                    marks = None
            try:
                geo = _evidence_geometry(evp, root / rel)
                if marks:
                    geo["header"] = marks["header"]
            except Exception:
                geo = None
    status, rule = tier(a)
    qx = qd.get("extra") or {}
    parse_usd = float(qx.get("parse_cost_usd") or 0.0)      # question reading by the language model (0 when cached)
    gold = CATALOG.gold(rel, question) if not body.path.startswith("upload:") else None
    rec = {
        "path": body.path, "drawing": display_name(rel.rsplit("/", 1)[-1]), "question": question, "query": qd,
        "value": a.get("value"), "unit": a.get("unit") or "", "route": a.get("route"),
        "confidence": a.get("confidence"), "abstain": a.get("abstain"), "explanation": a.get("explanation"),
        "approx": a.get("approx"),
        "checks": a.get("checks") or [], "items": a.get("items") or [], "cost_usd": (a.get("cost_usd") or 0.0) + parse_usd,
        "evidence_id": ev_id, "evidence_geometry": geo, "tried": list(ctx.tried),
        "status": status, "status_rule": rule,
        "where": where(a, qd, rel), "why": why(a, qd, rel.rsplit(".", 1)[-1].lower()),
        "time_ms": round(ms, 1), "cached": False, "gold": gold, "marks": marks,
        "compute": costs.compute_record(ms / 1000, cpu_s, a.get("route"), a.get("cost_usd") or 0.0, parse_usd=parse_usd,
                                        parse_orig_usd=float(qx.get("parse_cost_orig_usd") or 0.0),
                                        parse_cached=bool(qx.get("parse_cached"))),
        "no_overlay_reason": None if ev_id or a.get("abstain") else
            "No overlay for this file (its licence does not allow derivatives, or the route draws none). "
            "The numbered items are the evidence.",
    }
    cached.write_text(json.dumps(rec, ensure_ascii=False, default=str), encoding="utf-8")
    return JSONResponse(json.loads(json.dumps(rec, ensure_ascii=False, default=str)))


class WitnessBody(BaseModel):
    path: str
    question: str
    refresh: bool = False


def _keep_evidence(drawn: str | None, key: str) -> tuple[str | None, dict | None]:
    """Copy a route's evidence PNG (and its marks sidecar) to EVIDENCE_DIR under a per-answer id."""
    if not drawn:
        return None, None
    d = Path(drawn)
    if not d.exists() or RENDER_DIR.resolve() not in d.resolve().parents:
        return None, None
    ev_id = f"{d.stem[:60]}__{key[:10]}"
    evp = EVIDENCE_DIR / f"{ev_id}.png"
    shutil.copyfile(d, evp)
    marks = None
    if marks_path(d).exists():
        shutil.copyfile(marks_path(d), marks_path(evp))
        try:
            marks = json.loads(marks_path(d).read_text(encoding="utf-8"))
        except ValueError:
            marks = None
    return ev_id, marks


@app.post("/api/witnesses")
def witnesses(body: WitnessBody):
    """The same question answered from every format of the drawing (DXF, IFC, PDF alone, PNG), side by side."""
    from bpqa import witnesses as wit
    question = body.question.strip()
    if not question or len(question) > 1000:
        raise HTTPException(400, "empty or too long question")
    root, rel = resolve(body.path)
    key = _cache_key(root, rel, question, False)
    cached = ANSWER_CACHE / f"w{key}.json"
    if cached.exists() and not body.refresh:
        rec = json.loads(cached.read_text(encoding="utf-8"))
        if all(not w.get("evidence_id") or (EVIDENCE_DIR / f"{w['evidence_id']}.png").exists() for w in rec["witnesses"]):
            rec["cached"] = True
            return rec

    def keep(row, ans):
        wkey = hashlib.sha1(f"{key}|{row['format']}".encode()).hexdigest()
        row["evidence_id"], row["marks"] = _keep_evidence(ans.evidence, wkey)
        a = ans.as_dict()
        row["items"] = a.get("items") or []
        row["route_name"] = ROUTES.get(row["route"] or "", (row["route"],))[0]
        row["compute"] = costs.compute_record(row["wall_s"], row["cpu_s"], row["route"])
        row.pop("evidence", None)

    t0 = time.perf_counter()
    with SOLVE_LOCK:
        res = wit.run(root, rel, question, out_dir=RENDER_DIR, evidence=True, on_answer=keep)
    res["n_disputed"] = wit.spatial_disagreements(res["witnesses"])
    res.update(path=body.path, question=question, time_ms=round((time.perf_counter() - t0) * 1000, 1), cached=False,
               gold=CATALOG.gold(rel, question) if not body.path.startswith("upload:") else None)
    cached.write_text(json.dumps(res, ensure_ascii=False, default=str), encoding="utf-8")
    return JSONResponse(json.loads(json.dumps(res, ensure_ascii=False, default=str)))


# ---------------------------------------------------------------- corrections (human verification record)

CORRECTIONS = APP_OUT / "corrections.jsonl"
CORR_COLS = ["at", "drawing", "question", "route", "status", "answer_value", "corrected_value", "unit", "removed", "added",
             "evidence_id"]


class CorrectionBody(BaseModel):
    path: str
    drawing: str = ""
    question: str
    route: str | None = None
    status: str | None = None
    answer_value: float | int | str | None = None
    corrected_value: float | int | str | None = None
    unit: str = ""
    removed: list = []        # item numbers struck out ("not this one")
    added: list = []          # [{x, y}] points clicked on the evidence image, body pixels
    evidence_id: str | None = None


@app.post("/api/corrections")
def add_correction(body: CorrectionBody):
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **body.model_dump()}
    with CORRECTIONS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True, "n": sum(1 for _ in CORRECTIONS.open(encoding="utf-8"))}


def _corrections() -> list[dict]:
    if not CORRECTIONS.exists():
        return []
    rows = []
    for line in CORRECTIONS.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


@app.get("/api/corrections")
def list_corrections():
    return {"corrections": _corrections(), "file": str(CORRECTIONS.relative_to(REPO)).replace("\\", "/")}


@app.get("/api/corrections.csv")
def corrections_csv():
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CORR_COLS)
    for r in _corrections():
        w.writerow([" ".join(map(str, r.get(c) or [])) if c == "removed" else
                    " ".join(f"{p.get('x')},{p.get('y')}" for p in r.get(c) or []) if c == "added" else r.get(c, "")
                    for c in CORR_COLS])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="corrections.csv"'})


@app.get("/api/witness_summary")
def witness_summary():
    f = REPO / "out" / "witness" / "witness_summary.json"
    if not f.exists():
        return {"missing": "out/witness/witness_summary.json not found; run tools/witness_eval.py"}
    return json.loads(f.read_text(encoding="utf-8"))


@app.get("/api/costs")
def cost_table():
    t = costs.cost_table()
    return t if t is not None else {"routes": [], "assumed": costs.assumptions(), "baseline": costs.baseline_cost(),
                                    "missing": "out/cost/cost_by_route.json not found; run tools/cost_eval.py"}


@app.get("/api/evidence/{eid}.png")
def evidence(eid: str, body: int = 0):
    if not re.fullmatch(r"[\w.\-]{1,120}", eid):
        raise HTTPException(400, "bad id")
    f = EVIDENCE_DIR / f"{eid}.png"
    if not f.exists():
        raise HTTPException(404, "evidence not found")
    if not body:
        return FileResponse(f, media_type="image/png")
    from PIL import Image
    geo = _evidence_geometry(f, None)
    with Image.open(f) as im:
        crop = im.crop((0, geo["header"], im.width, im.height))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")


# ---------------------------------------------------------------- results and status

@app.get("/api/results")
def results_list():
    return {"runs": [{k: r[k] for k in ("id", "header", "label", "dataset", "dataset_name", "split", "flags", "questions", "where")}
                     for r in results_mod.list_runs()]}


@app.get("/api/overview")
def results_overview():
    """One line per named test set (full set, preview, Nemotron baseline sample, unseen drawings) with its source file."""
    return results_mod.overview()


@app.get("/api/baseline")
def results_baseline():
    b = results_mod.baseline()
    if b is None:
        raise HTTPException(404, "out/baseline_nemotron.json not found")
    return b


@app.get("/api/results/{run_id}")
def results_run(run_id: str):
    data = results_mod.load_run(run_id)
    if data is None:
        raise HTTPException(404, "no such run")
    return data


@app.get("/api/status")
def status():
    det = REPO / "models" / "detector.pt"
    return {
        "data_root": str(DATA_ROOT) if DATA_ROOT else None,
        "drawings": len(CATALOG.drawings),
        "sources": {s: SOURCE_NAMES.get(s, s) for s in {d.source for d in CATALOG.drawings.values()}},
        "detector_model": det.exists(),
        "detector_runtime": importlib.util.find_spec("ultralytics") is not None,
        "ocr_runtime": importlib.util.find_spec("rapidocr_onnxruntime") is not None,
        "uploads": str(UPLOADS),
    }


app.include_router(ifc3d.router)      # 3D view of IFC models: /api/ifc3d, /api/ifc3d/geom, /api/ifc3d/element

app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
