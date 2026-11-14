"""Read an uploaded raster drawing in the background, straight after upload, so a judge's first question waits only
for whatever is still running.

  large sheet (long side >= bpqa.sheet.LARGE_SIDE)   three stages, each cached on disk by its own module:
        text     OCR tiles and the printed scale          bpqa.sheet.analyse(regions=False)
        rooms    wall tracing, room outlines, footprint   bpqa.sheet.analyse(regions=True)
        doors    the door / window detector on tiles      bpqa.routes.sheet_plan.detect
      These run outside the server's SOLVE_LOCK, so other requests are never blocked by them. A question on the same
      sheet waits (outside the lock) for the stage it needs (`needed_stage`); the per-drawing locks in sheet.analyse
      and sheet_plan.detect also make a question that gets there first do the work once, the job then reads its cache.
  scan (any other PNG / JPG)   one normal solve of a window question (detector, OCR and room caches), taken under
      SOLVE_LOCK only when it is free: a scan takes 3 to 26 s on CPU, and a question asked meanwhile waits for it.

One worker thread, one job at a time, in upload order. `status(path)` is what GET /api/precompute returns.

Several containers (app/shared.py): the job runs on the container that took the upload. Its status and, as each stage
ends, the cache files that stage wrote are published to the shared Dict. Another container asked for the status
reports the published one (not a 404), and one asked a question about the drawing waits for the shared stage the
question needs (polling the Dict), pulls the cache files, and so reads nothing again. Without the Dict (local runs)
none of this exists.
"""
from __future__ import annotations

import queue
import re
import threading
import time
from pathlib import Path

from . import shared

STAGES = ("text", "rooms", "doors")
LABEL = {"text": "text", "rooms": "room outlines", "doors": "doors and windows"}
RASTER = {"png", "jpg", "jpeg", "tif", "tiff"}
WAIT_MAX_S = 600              # a question never waits longer than this for the background job
STALE_S = 300                 # a shared status not refreshed for this long belongs to a container that is gone
POLL_S = 0.75                 # how often a question polls the shared Dict for the stage it waits for
PUBLISH_EVERY_S = 3.0         # progress within a stage is published at most this often

_Q: "queue.Queue[tuple[str, Path]]" = queue.Queue()
_JOBS: dict[str, dict] = {}
_COND = threading.Condition()
_worker: threading.Thread | None = None
_PUB: dict[str, tuple] = {}   # path -> (signature, time) of the last status published


def needed_stage(question: str) -> str:
    """The last precompute stage a question on a sheet reads (the sheet route's own triggers, simplified)."""
    q = question.lower()
    if re.search(r"\b(doors?|windows?|ovi\w*|ikkun\w*|openings?)\b", q):
        return "doors"
    if re.search(r"\b(area|areas|perimeter|skirting|baseboard|base board|m2|square|gross|footprint|pinta-ala)\b", q):
        return "rooms"
    return "text"


def start(path: str, file: Path, solve_lock: threading.Lock, ask_scan) -> dict | None:
    """Queue the background reading of an uploaded drawing; None for a file that needs none (PDF, DXF, IFC)."""
    ext = file.suffix.lower().lstrip(".")
    if ext not in RASTER:
        return None
    from bpqa import sheet as S
    kind = "sheet" if S.is_large(file) else "scan"
    with _COND:
        _JOBS[path] = {"state": "queued", "kind": kind, "stage": None, "done_stages": [], "stage_done": 0,
                       "stage_total": 0, "queued_at": time.time(), "started_at": None, "finished_at": None,
                       "error": None, "_lock": solve_lock, "_ask": ask_scan}
    _publish(path, force=True)
    _Q.put((path, file))
    _ensure_worker()
    return status(path)


def _public(j: dict) -> dict:
    return {k: (list(v) if isinstance(v, list) else v) for k, v in j.items() if not k.startswith("_")}


def _publish(path: str, force: bool = False):
    """Publish this container's status of `path` to the shared Dict (no-op without it). Progress inside a stage is
    throttled; a change of state, stage or finished stages always goes out."""
    if not shared.enabled():
        return
    with _COND:
        j = _JOBS.get(path)
        if j is None:
            return
        out = _public(j)
    sig = (out["state"], out["stage"], tuple(out["done_stages"]))
    last = _PUB.get(path)
    now = time.time()
    if not force and last and last[0] == sig and now - last[1] < PUBLISH_EVERY_S:
        return
    _PUB[path] = (sig, now)
    shared.publish_status(path, out)


def _shared_status(path: str) -> dict | None:
    """The status another container published. One nobody refreshed for STALE_S died with its container: it is
    reported as failed, and questions then read the drawing themselves."""
    j = shared.fetch_status(path)
    if j is None:
        return None
    if j["state"] in ("queued", "running") and time.time() - j.get("updated_at", 0) > STALE_S:
        j = {**j, "state": "error", "error": "the container reading this drawing stopped", "finished_at": time.time()}
    return j


def status(path: str) -> dict | None:
    with _COND:
        j = _JOBS.get(path)
        out = _public(j) if j is not None else None
    if out is None:
        out = _shared_status(path)       # the job runs on another container
        if out is None:
            return None
        out = {k: v for k, v in out.items() if k != "updated_at"}
    out["message"] = message(out)
    now = out["finished_at"] or time.time()
    out["elapsed_s"] = round(now - out["started_at"], 1) if out["started_at"] else 0.0
    return out


def message(j: dict) -> str:
    if j["state"] == "done":
        return "Drawing read: questions now answer in about a second."
    if j["state"] == "error":
        return "Reading the drawing in the background failed; questions still work, the first one is slower."
    if j["kind"] == "scan":
        return "Reading the drawing in the background (a few seconds); you can ask now."
    if j["state"] == "queued":
        return "Reading the drawing in the background, about 1 to 2 minutes for a large sheet; you can ask now."
    st = j["stage"]
    pct = f" {round(100 * j['stage_done'] / j['stage_total'])}%" if j["stage_total"] else ""
    rest = [LABEL[s] for s in STAGES[STAGES.index(st) + 1:]] if st in STAGES else []
    return (f"Reading the drawing: {LABEL.get(st, st)}{pct}" + (f" now, {' and '.join(rest)} next" if rest else "")
            + ". You can ask now; a question waits only for the part it needs.")


def wait_for(path: str, question: str, timeout: float = WAIT_MAX_S) -> float:
    """Block (outside any lock) until the background job of `path` has done what `question` needs. Returns the
    seconds waited. A queued job behind another is not waited for: the question does its own work, and the job
    later finds the caches filled. A job on another container is waited for through the shared Dict and its cache
    files are pulled here before the question is solved; on a timeout or a dead container the question computes."""
    t0 = time.time()
    with _COND:
        j = _JOBS.get(path)
        if j is not None:
            if j["state"] == "running":
                need = needed_stage(question) if j["kind"] == "sheet" else None

                def ready():
                    if j["state"] != "running":
                        return True
                    return need is not None and need in j["done_stages"]
                _COND.wait_for(ready, timeout=timeout)
            return round(time.time() - t0, 2)
    if shared.enabled():
        _wait_shared(path, question, t0, timeout)
    return round(time.time() - t0, 2)


def _stem(path: str) -> str:
    return Path(path.split("/", 1)[1]).stem


def _wait_shared(path: str, question: str, t0: float, timeout: float):
    st = _shared_status(path)
    if st is None:
        return
    need = needed_stage(question) if st["kind"] == "sheet" else None
    while st["state"] == "running" and not (need is not None and need in st["done_stages"]):
        if time.time() - t0 > timeout:
            break                        # stop waiting: the question computes what is missing
        time.sleep(POLL_S)
        st = _shared_status(path)
        if st is None:
            return
    pull(path)


def pull(path: str):
    """Fetch the cache files the reading of `path` has published, and drop this container's older in-memory copy
    of a sheet reading that a newer file replaced."""
    written = shared.pull_caches(_stem(path))
    if written:
        from bpqa import sheet as S
        for f in written:
            S._MEM.pop(str(f), None)


def _set(path: str, **kw):
    with _COND:
        j = _JOBS.get(path)
        if j is not None:
            j.update(kw)
            _COND.notify_all()
    _publish(path)


def _progress(path: str):
    def cb(stage, done, total):
        _set(path, stage=stage, stage_done=done, stage_total=total)
    return cb


def _run_sheet(path: str, file: Path):
    from bpqa import sheet as S
    from bpqa.routes import sheet_plan as SP
    cb = _progress(path)
    _set(path, stage="text", stage_done=0, stage_total=0)
    d = S.analyse(file, regions=False, progress=cb)
    shared.publish_caches(file.stem, ["text"])           # before the status says the stage is done
    _set(path, done_stages=["text"], stage="rooms", stage_done=0, stage_total=0)
    if d["scale"]["ppm"]:
        S.analyse(file, regions=True)
    shared.publish_caches(file.stem, ["text", "rooms"])
    _set(path, done_stages=["text", "rooms"], stage="doors", stage_done=0, stage_total=0)
    SP.detect(file, d["scale"]["ppm"], progress=cb)
    shared.publish_caches(file.stem, list(STAGES))
    _set(path, done_stages=list(STAGES))


def _run_scan(path: str, j: dict):
    _set(path, stage="scan")
    with j["_lock"]:            # the routes behind a scan keep module-level state: one solve at a time
        j["_ask"](path)
    shared.publish_caches(_stem(path), ["scan"])


def _loop():
    while True:
        path, file = _Q.get()
        with _COND:
            j = _JOBS.get(path)
        if j is None:
            continue
        _set(path, state="running", started_at=time.time())
        try:
            if not Path(file).exists():
                raise FileNotFoundError(file)
            if j["kind"] == "sheet":
                _run_sheet(path, file)
            else:
                _run_scan(path, j)
            _set(path, state="done", finished_at=time.time())
        except Exception as ex:          # the background reading must never take the app down
            print(f"[precompute] {path}: {type(ex).__name__}: {ex}", flush=True)
            _set(path, state="error", error=f"{type(ex).__name__}: {ex}"[:300], finished_at=time.time())


def _ensure_worker():
    global _worker
    with _COND:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, daemon=True, name="upload-precompute")
            _worker.start()
