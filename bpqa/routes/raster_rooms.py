"""raster-rooms route: count kitchens, bathrooms, WC rooms, saunas, bedrooms and balconies on scans from
two independent readings, the drawn rooms and the printed labels.

1. Room detector (Ultralytics YOLO, `models/rooms.pt`, classes kitchen, bathroom, wc_room, sauna,
   bedroom, balcony) trained by tools/train_rooms_modal.py on public CubiCasa5K room polygons
   (tools/build_rooms_dataset.py; only plans allowed by tools/overlap.py). It finds rooms by what is
   drawn in them (counters and stove, toilet without shower, sauna benches, beds, balcony outline), so
   it works when the label is missing or unreadable. A room is counted when its box scores at or above
   the per-type threshold; boxes inside the per-type uncertainty band are listed as uncertain.
2. Room labels read by local OCR (raster.room_labels, route raster-ocr-labels), unchanged.

Combination, per question (thresholds and policies tuned on the held-out train-split tune images by
tools/tune_rooms.py, stored in models/rooms_thresholds.json):
  agree      the two counts are equal: that count, tier "proven" when the type's tuned rule holds
             (`proven_rule`: "agree", "agree_ocr" = the OCR reading is one its own rules prove,
             "agree_clean" = no room box scored inside the uncertainty band, or "none"); the rule is the
             one giving the most proven answers at >= 95% precision on the tune images
  disagree   tier "estimate"; the value is the reading the type's tuned policy prefers (`prefer`:
             "ocr", "det", or "ocr_proven": OCR when its own rules would have proven it, else the
             detector); both readings and their boxes are in the answer
Without a model or tuning for the type the route returns the OCR route's answer unchanged. Questions
that are not room-type counts go to the raster route (routes/raster.py) unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..answer import Answer, estimate
from ..evidence import AMBER, BLUE, GREY, ImageCanvas, evidence_path, finish
from .. import tiling
from . import raster

ROUTE = "raster-rooms"
REPO = Path(__file__).resolve().parents[2]
MODEL = Path(os.environ.get("STOA_ROOMS_MODEL") or REPO / "models" / "rooms.pt")
THRESHOLDS = Path(os.environ.get("STOA_ROOMS_THRESHOLDS") or REPO / "models" / "rooms_thresholds.json")
CLASSES = ["kitchen", "bathroom", "wc_room", "sauna", "bedroom", "balcony"]
NAMES = {"kitchen": "kitchen", "bathroom": "bathroom", "wc_room": "WC room", "sauna": "sauna",
         "bedroom": "bedroom", "balcony": "balcony"}
MIN_CONF = 0.05
DEVICE = os.environ.get("STOA_ROOMS_DEVICE") or "cpu"     # batch runs (tuning, eval caches) may pass a GPU
LABEL_COLOR = (18, 146, 192)


def model_tag(model: Path = MODEL) -> str | None:
    if not Path(model).exists():
        return None
    st = Path(model).stat()
    return hashlib.sha1(f"{st.st_size}-{int(st.st_mtime)}".encode()).hexdigest()[:10]


@lru_cache(maxsize=2)
def _model(path: str):
    from ..yolo_env import yolo
    return yolo()(path)


@lru_cache(maxsize=1)
def thresholds() -> dict:
    try:
        return json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _predict(img: np.ndarray, imgsz: int) -> list[dict]:
    res = _model(str(MODEL)).predict(img, imgsz=imgsz, conf=MIN_CONF, iou=0.6, agnostic_nms=True, max_det=100,
                                     verbose=False, device=DEVICE)[0]
    return [{"cls": CLASSES[int(c)], "conf": round(float(s), 4), "xyxy": [round(float(v), 1) for v in b]}
            for b, s, c in zip(res.boxes.xyxy.tolist(), res.boxes.conf.tolist(), res.boxes.cls.tolist())]


def detect_views(file: Path, imgsz: int, tta: bool) -> list[list[dict]] | None:
    """Room boxes scored >= MIN_CONF per view, in original pixels; cached under out/rooms_det/."""
    tag = model_tag()
    if tag is None:
        return None
    cache = REPO / "out" / "rooms_det" / f"{tag}-{imgsz}{'-d4' if tta else ''}" / f"{Path(file).stem}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))["views"]
    img = cv2.imread(str(file))
    if img is None:
        return None
    h, w = img.shape[:2]
    views = []
    for k, m in (tiling.DIHEDRAL if tta else [(0, False)]):
        ds = _predict(tiling.transform(img, k, m), imgsz)
        views.append([{**d, "xyxy": [round(v, 1) for v in tiling.untransform_box(d["xyxy"], k, m, w, h)]} for d in ds])
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"views": views}), encoding="utf-8")
    return views


def det_count(views: list[list[dict]], obj: str, cfg: dict) -> tuple[int, list[dict], list[dict]]:
    """(count, counted boxes, uncertain boxes) for one room type. With several views the count is the
    median of the per-view counts and the boxes shown are those of the view closest to it."""
    thr, lo, hi = cfg["thr"], cfg["band"][0], cfg["band"][1]
    per = [[d for d in v if d["cls"] == obj] for v in views]
    counts = [sum(d["conf"] >= thr for d in v) for v in per]
    n = int(np.median(counts)) if len(counts) > 1 else counts[0]
    best = min(range(len(per)), key=lambda i: (abs(counts[i] - n), i))
    kept = sorted([d for d in per[best] if d["conf"] >= thr], key=lambda d: -d["conf"])
    unsure = [d for d in per[best] if lo <= d["conf"] < hi]
    return n, kept, unsure


def combine(obj: str, o: int, o_proven: bool, d: int, cfg: dict, n_unsure: int = 0) -> tuple[int, bool, str]:
    """(value, proven, which) from the OCR count o and the detector count d, per the tuned policy."""
    if o == d:
        rule = cfg.get("proven_rule", "agree")
        proven = rule == "agree" or (rule == "agree_ocr" and o_proven) or (rule == "agree_clean" and n_unsure == 0)
        return o, proven, "both"
    pref = cfg.get("prefer", "ocr_proven")
    if pref == "det" or (pref == "ocr_proven" and not o_proven):
        return d, False, "det"
    return o, False, "ocr"


def answer(ctx, q) -> Answer:
    if q.measure != "count" or q.obj not in CLASSES:
        return raster.answer(ctx, q)
    cfg = thresholds().get(q.obj)
    img = cv2.imread(str(ctx.file))
    if img is None:
        return raster.answer(ctx, q)
    keep_ev = ctx.evidence
    ctx.evidence = False                      # the OCR reading is drawn below, with the rooms
    try:
        ocr = raster.room_labels(ctx, q, img)
    finally:
        ctx.evidence = keep_ev
    views = detect_views(ctx.file, cfg.get("imgsz", 640), cfg.get("tta", False)) if cfg else None
    if not cfg or views is None:
        return raster.answer(ctx, q)
    d, kept, unsure = det_count(views, q.obj, cfg)
    o, o_proven = int(ocr.value), ocr.tier == "proven"
    value, proven, which = combine(q.obj, o, o_proven, d, cfg, len(unsure))
    name = NAMES[q.obj]
    det_items = [{"n": i + 1, "reading": "drawn room", "type": q.obj, "score": k["conf"],
                  "xyxy": [round(v) for v in k["xyxy"]]} for i, k in enumerate(kept)]
    lab_items = [{"n": f"L{i + 1}", "reading": "label", **{k: v for k, v in it.items() if k != "n"}}
                 for i, it in enumerate(ocr.items)]
    readings = f"room detector: {d} {name}(s) drawn; room labels: {o} read"
    if which == "both":
        expl = f"{value} {name}(s): the drawn rooms and the printed labels agree ({readings})"
    else:
        expl = (f"{value} {name}(s), from the {'room detector' if which == 'det' else 'room labels'}; "
                f"the two readings disagree ({readings})")
    ans = Answer(value=value, unit="count", route=ROUTE, confidence=0.85 if proven else 0.4,
                 tier="proven" if proven else "estimate", items=det_items + lab_items, explanation=expl)
    if not proven:
        if which == "both":
            estimate(ans, f"both readings give {value}, but the proof rule for {name} counts "
                          f"({cfg.get('proven_rule')}) does not hold on this plan")
        else:
            estimate(ans, f"the readings disagree: {d} drawn {name}(s) found by the room detector, "
                          f"{o} {name} label(s) read" + (f" (label reading: {ocr.tier_reason})" if ocr.tier_reason else ""))
        ans.uncertain = [{"reading": "drawn room", "score": u["conf"], "xyxy": [round(v) for v in u["xyxy"]]}
                         for u in unsure] + [{"reading": "label", "label": it.get("label"), "xyxy": it.get("xyxy")}
                                             for it in ocr.items]
    if ctx.evidence:
        cv = ImageCanvas(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
        col = BLUE if proven else AMBER
        for u in unsure:
            cv.box(tuple(u["xyxy"]), color=GREY, width=2)
        for it in det_items:
            cv.box(tuple(it["xyxy"]), n=it["n"], color=col, width=4)
        for it in lab_items:
            b = it["xyxy"]
            cv.box((b[0] - 3, b[1] - 3, b[2] + 3, b[3] + 3), n=it["n"], color=LABEL_COLOR, width=2)
        tier = "proven" if proven else "ESTIMATE"
        ans.evidence = finish(cv.img, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question,
                              f"Answer: {value}   {tier}   ({ROUTE}: room detector + local OCR, $0)",
                              note=f"numbered thick boxes: {d} drawn {name}(s) found by the room detector; "
                                   f"L-numbered thin boxes: {o} {name} label(s) read"
                                   + (f": {', '.join(str(it.get('label')) for it in lab_items)}" if lab_items else "")
                                   + ("; grey: rooms scored near the threshold" if unsure else "")
                                   + (f". Estimate because: {ans.tier_reason}" if not proven else ""))
    return ans
