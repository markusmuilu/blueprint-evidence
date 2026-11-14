"""raster-detector route: count toilets, sinks, showers/baths, doors and windows with a trained detector.

The detector (Ultralytics YOLO, classes toilet, sink, shower, bathtub, door, window) is trained by
tools/train_detector.py on public CubiCasa5K annotations (only plans allowed by tools/overlap.py)
and synthetic renders of Swiss DXF floors. A count is the number of boxes of the asked classes
scored at or above a per-object threshold; the threshold, and an uncertainty band around it, are
tuned on held-out Stoa train-split images by tools/tune_detector.py.

Every question the detector has a class for gets its best count, in one of two tiers:
  proven    no more than the tuned number of boxes scored inside the band, the count is inside the
            tuned range, the object is one the detector is right on more often than wrong, and dev measured
            such counts at least 98% exact (bpqa/calibration.py): in practice only a "clear" count (every
            view agrees, no box within 0.20 of the threshold) of a value dev measured, such as one WC seat
  estimate  otherwise; the reason is given and the boxes inside the band are listed (`uncertain`)
            and drawn amber on the evidence image, so a person can check exactly those
The route abstains only when there is no model or no tuning for the object.

Detection modes, per object in the thresholds file (so each object uses whichever model and mode won
on the tune images): the whole image at `imgsz`, or tiles at a fixed scale (bpqa/tiling.py) merged
at the seams; either one optionally with test-time augmentation (`tta`: the eight rotations and
mirrors of the square; on tiles each tile is turned and mirrored; boxes fused across views or the median
per-view count, bpqa/tiling.fuse_views). Tiles go through the model in batches.

Files (not committed): models/detector*.pt, models/detector_thresholds.json. Detections are cached
per model and mode under out/det/.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..answer import Answer, abstain, estimate
from .. import calibration
from ..evidence import AMBER, BLUE, GREY, ImageCanvas, evidence_path, finish
from .. import tiling

ROUTE = "raster-detector"
REPO = Path(__file__).resolve().parents[2]
MODEL = REPO / "models" / "detector.pt"
THRESHOLDS = Path(os.environ.get("STOA_DET_THRESHOLDS") or REPO / "models" / "detector_thresholds.json")  # override: eval
CLASSES = ["toilet", "sink", "shower", "bathtub", "door", "window"]
OBJECTS = {"toilet": ["toilet"], "sink": ["sink"], "shower_or_bath": ["shower", "bathtub"],
           "door": ["door"], "window": ["window"]}
NAMES = {"toilet": "WC seat", "sink": "sink or wash basin", "shower_or_bath": "shower or bathtub",
         "door": "door", "window": "window"}
MIN_CONF = 0.05


def model_tag(model: Path = MODEL) -> str | None:
    if not Path(model).exists():
        return None
    st = Path(model).stat()
    return hashlib.sha1(f"{st.st_size}-{int(st.st_mtime)}".encode()).hexdigest()[:10]


# one prediction at a time on a shared model: the upload precompute (app/precompute.py) runs the detector in a
# background thread while a question may run it in the request thread, and a YOLO predictor is not thread safe
PREDICT_LOCK = threading.RLock()


@lru_cache(maxsize=4)
def _model(path: str):
    from ..yolo_env import yolo
    return yolo()(path)


def _predict(model: str, img: np.ndarray, imgsz: int, device) -> list[dict]:
    with PREDICT_LOCK:
        res = _model(model).predict(img, imgsz=imgsz, conf=MIN_CONF, iou=0.5, max_det=600, verbose=False, device=device)[0]
    return [{"cls": CLASSES[int(c)], "conf": round(float(s), 4), "xyxy": [round(float(v), 1) for v in b]}
            for b, s, c in zip(res.boxes.xyxy.tolist(), res.boxes.conf.tolist(), res.boxes.cls.tolist())]


def _predict_batch(model: str, imgs: list[np.ndarray], imgsz: int, device, chunk: int = 8) -> list[list[dict]]:
    out = []
    for i in range(0, len(imgs), chunk):
        with PREDICT_LOCK:
            res = _model(model).predict(imgs[i:i + chunk], imgsz=imgsz, conf=MIN_CONF, iou=0.5, max_det=600,
                                        verbose=False, device=device)
        for r in res:
            out.append([{"cls": CLASSES[int(c)], "conf": round(float(s), 4), "xyxy": [float(v) for v in b]}
                        for b, s, c in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist())])
    return out


def _views_tiled(model: str, img: np.ndarray, tile: dict, tta: bool, device) -> list[list[dict]]:
    """Resize by the training scale rule, cut tiles (padded white to full size), run every tile (and, with
    TTA, each of its eight rotations and mirrors) in batches, map boxes back, merge the seams per view."""
    h0, w0 = img.shape[:2]
    f = tiling.scale_for(w0, h0, tile["scale"], tile["max_side"])
    w, h = round(w0 * f), round(h0 * f)
    im = cv2.resize(img, (w, h), interpolation=cv2.INTER_CUBIC) if (w, h) != (w0, h0) else img
    T, ov = tile["tile"], tile["overlap"]
    tiles = []
    for x0, y0 in tiling.tile_origins(w, h, T, ov):
        crop = im[y0:y0 + T, x0:x0 + T]
        pad = np.full((T, T, 3), 255, np.uint8)
        pad[: crop.shape[0], : crop.shape[1]] = crop
        tiles.append((x0, y0, crop.shape[1], crop.shape[0], pad))
    trans = tiling.DIHEDRAL if tta else [(0, False)]
    jobs = [(vi, ti) for vi in range(len(trans)) for ti in range(len(tiles))]
    preds = _predict_batch(model, [tiling.transform(tiles[ti][4], *trans[vi]) for vi, ti in jobs], T, device)
    views = [[] for _ in trans]
    for (vi, ti), ds in zip(jobs, preds):
        x0, y0, cw, ch, _ = tiles[ti]
        for d in ds:
            b = tiling.untransform_box(d["xyxy"], *trans[vi], T, T)
            if b[0] >= cw or b[1] >= ch:
                continue                              # in the white padding
            views[vi].append({**d, "xyxy": [round((b[0] + x0) / f, 1), round((b[1] + y0) / f, 1),
                                            round((min(b[2], cw) + x0) / f, 1), round((min(b[3], ch) + y0) / f, 1)]})
    return [tiling.merge_seams(v) for v in views]


def _views(model: str, img: np.ndarray, imgsz: int, tile: dict | None, tta: bool, device) -> list[list[dict]]:
    if tile:
        return _views_tiled(model, img, tile, tta, device)
    h, w = img.shape[:2]
    views = []
    for k, m in (tiling.DIHEDRAL if tta else [(0, False)]):
        ds = _predict(model, tiling.transform(img, k, m), imgsz, device)
        views.append([{**d, "xyxy": [round(v, 1) for v in tiling.untransform_box(d["xyxy"], k, m, w, h)]} for d in ds])
    return views


def detect_views(file: Path, model: Path = MODEL, imgsz: int = 1280, tile: dict | None = None, tta: bool = False,
                 device=None) -> list[list[dict]] | None:
    """Boxes scored >= MIN_CONF per view ([{"cls", "conf", "xyxy"}] in original pixels), cached."""
    tag = model_tag(model)
    if tag is None:
        return None
    mode = f"{imgsz}" + (f"-t{tile['tile']}s{tile['scale']}o{tile['overlap']}m{tile['max_side']}" if tile else "") \
           + ("-d4" if tta else "")
    cache = REPO / "out" / "det" / f"{tag}-{mode}" / f"{Path(file).stem}.json"
    if cache.exists():
        v = json.loads(cache.read_text(encoding="utf-8"))
        return v["views"] if isinstance(v, dict) else [v]     # a plain list: one view (older cache format)
    img = cv2.imread(str(file))
    if img is None:
        return None
    views = _views(str(model), img, imgsz, tile, tta, device)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"views": views}), encoding="utf-8")
    return views


def model_files(c: dict) -> list[Path]:
    """The weights a policy uses: one file, or several ("a.pt+b.pt") whose views are fused as an ensemble."""
    return [REPO / "models" / m for m in str(c["model"]).split("+")]


def tile_modes(c: dict) -> list[dict | None]:
    """The tiling of a policy: none, one tile config, or a list of them (several scales, fused)."""
    t = c.get("tile")
    return t if isinstance(t, list) else [t]


def views_for(file: Path, c: dict, device=None) -> list[list[dict]] | None:
    """Every view of every model and tile scale in the policy (an ensemble is the views of its models
    and scales, fused like TTA)."""
    out = []
    for m in model_files(c):
        for t in tile_modes(c):
            v = detect_views(Path(file), model=m, imgsz=c["imgsz"], tile=t, tta=c["tta"], device=device)
            if v is None:
                return None
            out += v
    return out


def combine(views: list[list[dict]]) -> list[dict]:
    return views[0] if len(views) == 1 else tiling.fuse_views(views)


def detect(file: Path, model: Path = MODEL, imgsz: int = 1280, device=None, tile=None, tta=False) -> list[dict] | None:
    v = detect_views(file, model=model, imgsz=imgsz, tile=tile, tta=tta, device=device)
    return None if v is None else combine(v)


@lru_cache(maxsize=1)
def thresholds() -> dict:
    return json.loads(THRESHOLDS.read_text(encoding="utf-8")) if THRESHOLDS.exists() else {}


def config(obj: str) -> dict | None:
    """Per-object policy merged over the file-wide defaults (model, imgsz, tile, tta)."""
    th = thresholds()
    if obj not in th:
        return None
    c = {"model": "detector.pt", "imgsz": th.get("_imgsz", 1280), "tile": th.get("_tile"), "tta": th.get("_tta", False)}
    c.update(th[obj])
    return c


def count(dets, obj, t, band):
    """(counted boxes, boxes inside the uncertainty band) for one object type."""
    cl = set(OBJECTS[obj])
    mine = sorted((d for d in dets if d["cls"] in cl), key=lambda d: -d["conf"])
    return [d for d in mine if d["conf"] >= t], [d for d in mine if t - band <= d["conf"] < t + band]


def count_views(views, fused, obj, c):
    """(count, counted boxes, boxes inside the band, spread of the per-view counts) for policy c.

    One view: boxes at or above the threshold. Several views (test-time augmentation): with agg
    "fuse" the fused boxes at or above the threshold; with agg "median" the median of the per-view
    counts, shown as that many of the highest-scored fused boxes."""
    t, band = c["conf"], c.get("band", 0.0)
    hits, unsure = count(fused, obj, t, band)
    if len(views) == 1:
        return len(hits), hits, unsure, 0
    per = sorted(len(count(v, obj, t, 0.0)[0]) for v in views)
    spread = per[-1] - per[0]
    if c.get("agg") == "median":
        n = per[(len(per) - 1) // 2]
        cl = set(OBJECTS[obj])
        hits = sorted((d for d in fused if d["cls"] in cl), key=lambda d: -d["conf"])[:n]
        return n, hits, unsure, spread
    return len(hits), hits, unsure, spread


def policy(hits, unsure, c, spread: int = 0, n: int | None = None) -> list[str]:
    """Why a count is only an estimate; empty when it is proven."""
    n = len(hits) if n is None else n
    why = []
    if not c.get("enabled", True):
        why.append("on held-out train-split images this object's tuned count was wrong more often than right")
    if len(unsure) >= c.get("max_unsure", 1):
        why.append(f"{len(unsure)} box(es) scored within {c.get('band', 0.0):.2f} of the threshold {c['conf']:.2f}")
    if n > c.get("max", 99):
        why.append(f"{n} counted, above the largest count seen while tuning ({c.get('max')})")
    if spread > c.get("max_spread", 99):
        why.append(f"the rotated and mirrored views disagree: their counts differ by {spread}")
    return why


CLEAR_MARGIN = 0.20      # "clear": every view gives the same count and no box of the object scores this close to t


def clear(views, fused, obj, c, spread: int) -> bool:
    """True when the count is unambiguous: the rotated and mirrored views all give the same count, and no box of
    the asked classes scores within CLEAR_MARGIN of the threshold, above or below it. Dev measured proven counts
    under this condition separately (bpqa/calibration.py)."""
    if len(views) > 1 and spread != 0:
        return False
    cl = set(OBJECTS[obj])
    t = c["conf"]
    return all(abs(d["conf"] - t) >= CLEAR_MARGIN for d in fused if d["cls"] in cl)


def answer(ctx, q, img=None) -> Answer:
    if q.obj not in OBJECTS:
        return abstain(ROUTE, f"detector has no class for {q.obj}")
    c = config(q.obj)
    if not c or not all(m.exists() for m in model_files(c)):
        return abstain(ROUTE, "detector not trained or not tuned (tools/train_detector.py, tools/tune_detector.py)")
    views = views_for(ctx.file, c)
    if views is None:
        return abstain(ROUTE, "image unreadable or detector model missing")
    dets = combine(views)
    t, band = c["conf"], c.get("band", 0.0)
    n, hits, unsure, spread = count_views(views, dets, q.obj, c)
    why = policy(hits, unsure, c, spread, n)
    cell = "clear" if clear(views, dets, q.obj, c, spread) else ""
    cal = None if why else calibration.reason(ROUTE, q.obj, n, cell=cell)   # proven only where dev supports it
    if cal:
        why.append(cal)
    mode = " ".join(m for m in ("on tiles" if c["tile"] else "", f"at {len(tile_modes(c))} scales" if len(tile_modes(c)) > 1 else "", "over rotations and mirrors" if c["tta"] else "",
                                f"fused over {len(model_files(c))} models" if len(model_files(c)) > 1 else "") if m)
    ans = Answer(value=n, unit="count", route=ROUTE, tier="proven", tier_cell=cell,
                 confidence=round(calibration.precision(ROUTE, q.obj) or c.get("precision", 0.5) if cal else
                                  c.get("precision_estimate" if why else "precision", c.get("precision", 0.5)), 2),
                 items=[{"n": i + 1, "class": d["cls"], "score": d["conf"], "xyxy": d["xyxy"]} for i, d in enumerate(hits)],
                 uncertain=[{"class": d["cls"], "score": d["conf"], "xyxy": d["xyxy"], "counted": d["conf"] >= t}
                            for d in unsure],
                 explanation=(f"median count of {len(views)} rotated and mirrored views at score >= {t:.2f} "
                              f"(per-view counts differ by {spread}); "
                              if c.get("agg") == "median" and len(views) > 1 else
                              f"{n} box(es) of {', '.join(OBJECTS[q.obj])} scored >= {t:.2f} by the detector{' ' + mode if mode else ''}; ")
                             + f"{len(unsure)} box(es) within {band:.2f} of the threshold")
    for w in why:
        estimate(ans, w)
    if ctx.evidence:
        if img is None:
            img = cv2.imread(str(ctx.file))
        cv = ImageCanvas(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
        amber = {id(d) for d in unsure}
        for d in dets:                       # the other boxes of this type, thin grey: what was not counted
            if d["cls"] in OBJECTS[q.obj] and 0.1 <= d["conf"] < t and id(d) not in amber:
                cv.box(d["xyxy"], color=GREY, width=1)
        for i, d in enumerate(hits):
            cv.box(d["xyxy"], n=i + 1, color=AMBER if id(d) in amber or why else BLUE,   # blue only when proven
                   width=3)
        for d in unsure:
            if d["conf"] < t:
                cv.box(d["xyxy"], color=AMBER, width=2)
        head = f"Answer: {n}" + ("   ESTIMATE: check the numbered boxes" if cal and not unsure else
                                 "   ESTIMATE: check the amber boxes" if why else "   proven")
        ans.evidence = finish(cv.img, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question,
                              f"{head}   ({ROUTE}, local model, €0)",
                              note=f"{'amber' if why else 'blue'} numbered boxes: each counted {NAMES[q.obj]} with its detector score >= {t:.2f}; "
                                   f"amber plain boxes: scored close to the threshold, not counted; "
                                   f"grey: lower-scored boxes, not counted"
                                   + (f". Estimate because: {ans.tier_reason}" if why else ""))
    return ans
