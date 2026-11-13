"""raster-detector route: count toilets, sinks, showers/baths, doors and windows with a trained detector.

The detector (Ultralytics YOLO, classes toilet, sink, shower, bathtub, door, window) is trained by
tools/train_detector.py on public CubiCasa5K annotations (only plans allowed by tools/overlap.py)
and synthetic renders of Swiss DXF floors. A count is the number of boxes of the asked classes
scored at or above a per-object threshold; the threshold, and an uncertainty band around it, are
tuned on held-out Stoa train-split images by tools/tune_detector.py. If any box scores inside the
band, the count is not provable and the route abstains. Every counted box is drawn and numbered.

Files (not committed): models/detector.pt, models/detector_thresholds.json. Without them the route
abstains. Detections are cached per model under out/det/.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

import cv2
from PIL import Image

from ..answer import Answer, abstain
from ..evidence import PALETTE, ImageCanvas, evidence_path, finish

ROUTE = "raster-detector"
REPO = Path(__file__).resolve().parents[2]
MODEL = REPO / "models" / "detector.pt"
THRESHOLDS = REPO / "models" / "detector_thresholds.json"
CLASSES = ["toilet", "sink", "shower", "bathtub", "door", "window"]
OBJECTS = {"toilet": ["toilet"], "sink": ["sink"], "shower_or_bath": ["shower", "bathtub"],
           "door": ["door"], "window": ["window"]}
NAMES = {"toilet": "WC seat", "sink": "sink or wash basin", "shower_or_bath": "shower or bathtub",
         "door": "door", "window": "window"}
MIN_CONF = 0.05


def model_tag(model: Path = MODEL) -> str | None:
    if not model.exists():
        return None
    st = model.stat()
    return hashlib.sha1(f"{st.st_size}-{int(st.st_mtime)}".encode()).hexdigest()[:10]


@lru_cache(maxsize=2)
def _model(path: str):
    from ..yolo_env import yolo
    return yolo()(path)


def detect(file: Path, model: Path = MODEL, imgsz: int = 1280, device=None) -> list[dict] | None:
    """All boxes scored >= MIN_CONF: [{"cls", "conf", "xyxy"}], cached per model and image."""
    tag = model_tag(model)
    if tag is None:
        return None
    cache = REPO / "out" / "det" / f"{tag}-{imgsz}" / f"{Path(file).stem}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    res = _model(str(model)).predict(str(file), imgsz=imgsz, conf=MIN_CONF, iou=0.5, max_det=600, verbose=False,
                                     device=device)[0]
    out = [{"cls": CLASSES[int(c)], "conf": round(float(s), 4), "xyxy": [round(float(v), 1) for v in b]}
           for b, s, c in zip(res.boxes.xyxy.tolist(), res.boxes.conf.tolist(), res.boxes.cls.tolist())]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


@lru_cache(maxsize=1)
def thresholds() -> dict:
    return json.loads(THRESHOLDS.read_text(encoding="utf-8")) if THRESHOLDS.exists() else {}


def count(dets, obj, t, band):
    """(counted boxes, boxes inside the uncertainty band) for one object type."""
    cl = set(OBJECTS[obj])
    mine = sorted((d for d in dets if d["cls"] in cl), key=lambda d: -d["conf"])
    return [d for d in mine if d["conf"] >= t], [d for d in mine if t - band <= d["conf"] < t + band]


def answer(ctx, q, img=None) -> Answer:
    if q.obj not in OBJECTS:
        return abstain(ROUTE, f"detector has no class for {q.obj}")
    th = thresholds().get(q.obj)
    if not MODEL.exists() or not th:
        return abstain(ROUTE, "detector not trained or not tuned (tools/train_detector.py, tools/tune_detector.py)")
    if not th.get("enabled", True):
        return abstain(ROUTE, f"detector switched off for {q.obj}: on held-out train-split images it was wrong more "
                              f"often than right (tools/tune_detector.py)")
    dets = detect(ctx.file, imgsz=thresholds().get("_imgsz", 1280))
    if dets is None:
        return abstain(ROUTE, "detector model missing")
    t, band, mx = th["conf"], th.get("band", 0.0), th.get("max", 99)
    hits, unsure = count(dets, q.obj, t, band)
    if unsure:
        return abstain(ROUTE, f"{len(hits)} {q.obj} detected at score >= {t:.2f}, but {len(unsure)} box(es) scored within "
                              f"{band:.2f} of the threshold; count not provable")
    if len(hits) > mx:
        return abstain(ROUTE, f"{len(hits)} {q.obj} detected; above the range tuned on dev (max {mx})")
    n = len(hits)
    ans = Answer(value=n, unit="count", route=ROUTE, confidence=round(th.get("precision", 0.5), 2),
                 items=[{"n": i + 1, "class": d["cls"], "score": d["conf"], "xyxy": d["xyxy"]} for i, d in enumerate(hits)],
                 explanation=f"{n} box(es) of {', '.join(OBJECTS[q.obj])} scored >= {t:.2f} by the detector; "
                             f"no box within {band:.2f} of the threshold")
    if ctx.evidence:
        if img is None:
            img = cv2.imread(str(ctx.file))
        cv = ImageCanvas(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
        for d in dets:                       # the other boxes of this type, thin grey: what was not counted
            if d["cls"] in OBJECTS[q.obj] and d["conf"] < t and d["conf"] >= 0.1:
                cv.box(d["xyxy"], color=(160, 160, 160), width=1)
        for i, d in enumerate(hits):
            cv.box(d["xyxy"], n=i + 1, color=PALETTE[CLASSES.index(d["cls"]) % len(PALETTE)], width=3)
        ans.evidence = finish(cv.img, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question,
                              f"Answer: {n}   route: {ROUTE} (local model, cost $0)",
                              note=f"numbered boxes: each counted {NAMES[q.obj]} with its detector score >= {t:.2f}; "
                                   f"grey: lower-scored boxes of the same type, not counted")
    return ans
