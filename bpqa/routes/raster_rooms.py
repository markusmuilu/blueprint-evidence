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
  agree on 0 is never proven: no label of the type was read, which says nothing about unlabelled rooms
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
from .. import calibration
from ..evidence import AMBER, BLUE, GREY, ImageCanvas, evidence_path, finish
from .. import tiling
from . import labels, raster

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
ORIENT = os.environ.get("STOA_ROOMS_ORIENT", "1") != "0"     # 0: the detector sees the plan as drawn
LABEL_FILTER = os.environ.get("STOA_ROOMS_LABEL_FILTER", "1") != "0"     # 0: no label-based dropping, no Shower labels


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


def detect_views(file: Path, imgsz: int, tta: bool, quarter: int = 0) -> list[list[dict]] | None:
    """Room boxes scored >= MIN_CONF per view, in original pixels; cached under out/rooms_det/. `quarter`: the
    single view is the plan turned by this many quarter turns (an upside-down plan); with tta all eight dihedral
    views are used and the argument has no effect."""
    tag = model_tag()
    if tag is None:
        return None
    quarter = 0 if tta else quarter % 4
    suffix = ("-d4" if tta else "") + (f"-q{quarter}" if quarter else "")
    cache = REPO / "out" / "rooms_det" / f"{tag}-{imgsz}{suffix}" / f"{Path(file).stem}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))["views"]
    img = cv2.imread(str(file))
    if img is None:
        return None
    h, w = img.shape[:2]
    views = []
    for k, m in (tiling.DIHEDRAL if tta else [(quarter, False)]):
        ds = _predict(tiling.transform(img, k, m), imgsz)
        views.append([{**d, "xyxy": [round(v, 1) for v in tiling.untransform_box(d["xyxy"], k, m, w, h)]} for d in ds])
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"views": views}), encoding="utf-8")
    return views


def drawn_score(views: list[list[dict]]) -> float:
    """How much room-like drawing the detector sees: summed score of its boxes at 0.3 or more, per view."""
    return float(np.mean([sum(d["conf"] for d in v if d["conf"] >= 0.3) for v in views])) if views else 0.0


UPSIDE_DOWN_GAIN = 1.5      # the plan turned 180 degrees must show this many times the drawing, and at least +1.0


def upright_views(file: Path, imgsz: int, tta: bool):
    """(views, quarter): the detector's views of the plan as drawn, or turned 180 degrees when it is drawn upside
    down (the detector is trained on upright plans; the turned view must score clearly higher: drawn_score)."""
    v0 = detect_views(file, imgsz, tta)
    if not ORIENT or v0 is None or tta:
        return v0, 0
    s0 = drawn_score(v0)
    if s0 >= 4.0:
        return v0, 0                 # plenty of rooms found as it is: no second pass
    v2 = detect_views(file, imgsz, tta, quarter=2)
    s2 = drawn_score(v2)
    if v2 is not None and s2 >= UPSIDE_DOWN_GAIN * s0 and s2 >= s0 + 1.0:
        return v2, 2
    return v0, 0


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


def _center_in(box, b) -> bool:
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def other_type_labels(toks, obj: str) -> list[dict]:
    """Printed labels of a room type other than `obj`, read exactly (no OCR-error matches, no one-letter
    abbreviations: a lone K or S is as likely a north arrow or a stair mark as a room)."""
    out = []
    for o in CLASSES:
        if o == obj:
            continue
        out += [h for h in labels.find_rooms(toks, o) if not h.get("fuzzy") and len(h["part"]) >= 2]
    return out


def drop_labelled_other(boxes: list[dict], other: list[dict]) -> tuple[list[dict], list[dict]]:
    """(kept, dropped): a drawn room whose box holds the printed label of another room type is that room
    (Tyneside: two "Bedroom" rooms scored 0.39 and 0.35 as bathrooms)."""
    kept, dropped = [], []
    for k in boxes:
        hit = next((h for h in other if _center_in(k["xyxy"], h["box"])), None)
        (dropped if hit else kept).append({**k, "why": hit["text"]} if hit else k)
    return kept, dropped


def shower_labels(toks, boxes: list[dict]) -> list[dict]:
    """A printed "Shower" (or "Shower room") label inside a drawn bathroom box. The word alone is not a room
    label (labels.py: it is also printed next to the fixture symbol), but inside a box the detector drew as a
    bathroom it names that room."""
    out = []
    for t in labels.dedupe(toks):
        if t["conf"] < 0.5 or not labels.clean_token(t["text"]):
            continue
        if labels.norm(t["text"]).replace(" ", "") not in ("SHOWER", "SHOWERROOM", "DUSCHE", "DUSCHRAUM"):
            continue
        b = labels.quad_box(t["quad"])
        if any(_center_in(k["xyxy"], b) for k in boxes):
            out.append({"text": t["text"], "box": b})
    return out


def combine(obj: str, o: int, o_proven: bool, d: int, cfg: dict, n_unsure: int = 0,
            ocr_words: bool = False) -> tuple[int, bool, str]:
    """(value, proven, which) from the OCR count o and the detector count d, per the tuned policy.

    ocr_words: every label the OCR read is a full English or German room word read exactly ("Bedroom",
    "Kuche"). The detector was trained on Finnish CubiCasa drawing styles, so where it finds fewer rooms than
    such labels name, the labels win (still an estimate)."""
    if o == d:
        rule = cfg.get("proven_rule", "agree")
        proven = rule == "agree" or (rule == "agree_ocr" and o_proven) or (rule == "agree_clean" and n_unsure == 0)
        if o == 0:
            proven = False       # agreement by absence: no label of this type was read and none was drawn
        return o, proven, "both"
    if ocr_words and o > d and cfg.get("prefer") != "off":
        return o, False, "ocr"
    pref = cfg.get("prefer", "ocr_proven")
    if pref == "off":
        return o, o_proven, "ocr_only"
    if pref == "det" or (pref == "ocr_proven" and not o_proven):
        return d, False, "det"
    return o, False, "ocr"


def answer(ctx, q) -> Answer:
    if q.measure != "count" or q.obj not in CLASSES:
        return raster.answer(ctx, q)
    cfg = thresholds().get(q.obj)
    img = cv2.imread(str(ctx.file))
    if img is None or not cfg or cfg.get("prefer") == "off":
        return raster.answer(ctx, q)       # no tuning, or on the tune images the OCR route alone did better
    keep_ev = ctx.evidence
    ctx.evidence = False                      # the OCR reading is drawn below, with the rooms
    try:
        ocr = raster.room_labels(ctx, q, img, calibrate=False)   # the OCR route's own tier, as tuned
    finally:
        ctx.evidence = keep_ev
    views, quarter = (upright_views(ctx.file, cfg.get("imgsz", 640), cfg.get("tta", False)) if cfg else (None, 0))
    if not cfg or views is None:
        return raster.answer(ctx, q)
    d, kept, unsure = det_count(views, q.obj, cfg)
    toks = raster.ocr_image(ctx.file)
    dropped = []
    if LABEL_FILTER and (kept or unsure):
        other = other_type_labels(toks, q.obj)
        kept, dropped = drop_labelled_other(kept, other)
        unsure, _ = drop_labelled_other(unsure, other)
        d = len(kept)
    o, o_proven = int(ocr.value), ocr.tier == "proven"
    showers = shower_labels(toks, kept) if LABEL_FILTER and q.obj == "bathroom" and o < d else []
    if showers:                      # printed "Shower" in a drawn bathroom: that room is labelled, as an estimate
        o, o_proven = o + len(showers), False
    # every label a full room word: read exactly, or (words of 8+ letters, "PESUIHUONE") with one OCR error
    words = o > 0 and all(labels.full_word(q.obj, it.get("match", "")) or len(labels.fuzzy(q.obj, it.get("match", "")) or "") >= 8
                          for it in ocr.items)
    value, proven, which = combine(q.obj, o, o_proven, d, cfg, len(unsure), words)
    if showers:
        proven = False               # a fixture word names the room: never proven on that alone
    name = NAMES[q.obj]
    det_items = [{"n": i + 1, "reading": "drawn room", "type": q.obj, "score": k["conf"],
                  "xyxy": [round(v) for v in k["xyxy"]]} for i, k in enumerate(kept)]
    lab_items = [{"n": f"L{i + 1}", "reading": "label", **{k: v for k, v in it.items() if k != "n"}}
                 for i, it in enumerate(ocr.items)]
    readings = f"room detector: {d} {name}(s) drawn; room labels: {o} read"
    if dropped:
        readings += (f"; {len(dropped)} drawn room(s) dropped because they hold another room's label: "
                     + ", ".join(f"{k['why']} (score {k['conf']:.2f})" for k in dropped))
    if showers:
        readings += f"; labels include {len(showers)} printed 'Shower' inside a drawn bathroom"
    if which == "both":
        expl = f"{value} {name}(s): the drawn rooms and the printed labels agree ({readings})"
    else:
        expl = (f"{value} {name}(s), from the {'room detector' if which == 'det' else 'room labels'}; "
                f"the two readings disagree ({readings})")
    ans = Answer(value=value, unit="count", route=ROUTE, confidence=0.85 if proven else 0.4,
                 tier="proven" if proven else "estimate", items=det_items + lab_items, explanation=expl,
                 tier_cell="ocr-proven" if which == "both" and o_proven else "")
    cal = calibration.reason(ROUTE, q.obj, value, cell=ans.tier_cell) if proven else None
    if cal:                                   # the route's rule held, but dev does not support proven here
        proven = False
        ans.confidence = round(calibration.precision(ROUTE, q.obj) or 0.4, 2)
    if cal:
        estimate(ans, cal)
        ans.uncertain = [{"reading": "drawn room", "score": k["conf"], "xyxy": [round(v) for v in k["xyxy"]]}
                         for k in kept] + [{"reading": "label", "label": it.get("label"), "xyxy": it.get("xyxy")}
                                           for it in ocr.items]
    elif not proven:
        if which == "both" and value == 0:
            estimate(ans, f"no {name} label was read and none was drawn; both readings give 0, but absence of "
                          f"a label is not proof that the plan has none")
        elif which == "both" and showers:
            estimate(ans, f"both readings give {value}, but the {name} labels are printed 'Shower' words, which also "
                          f"name a fixture")
        elif which == "both":
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
