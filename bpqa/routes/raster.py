"""Raster route (scanned plans, CubiCasa5K PNGs). Classical computer vision, no model.

Implemented:
  storeys  = number of separate drawing panels. Multi-storey CubiCasa plans paste each floor
             as its own scan onto a pure-white canvas, so panels are the large connected
             components of non-white pixels after a morphological close.

  rooms    = bedrooms, kitchens, saunas, WC rooms, bathrooms, balconies: room labels read with a
             local OCR model (RapidOCR) and matched against the Finnish abbreviations (MH, K, KK,
             S, WC, KH, PH, PARVEKE ...). Each matched label is boxed and numbered.

  fixtures, doors, windows = the raster-detector route (routes/detector.py), when a trained model
             and tuned thresholds exist; otherwise the older fixture classifier, which abstains
             without its model.

Everything else abstains by default. With `prior=True` (eval.py --prior) the route instead
answers with the most common dev-split value for that object type, labelled route
"raster-prior", low confidence and an evidence image that says plainly it is not visual proof.
That is a baseline to beat, not a method.
"""
from __future__ import annotations

import json
from collections import Counter
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..answer import Answer, abstain
from ..evidence import PALETTE, ImageCanvas, evidence_path, finish
from .labels import ROOM_PATTERNS, dedupe, find_rooms, is_word, quad_box
from .ocr import ocr_image
from . import detector
from . import fixtures as fx

ROUTE = "raster-cv"
PRIOR_FILE = Path(__file__).resolve().parents[2] / "out" / "priors.json"


def panels(img_bgr, white=245, min_frac=0.04):
    h, w = img_bgr.shape[:2]
    fg = (img_bgr.min(axis=2) < white).astype(np.uint8)
    k = max(3, int(min(h, w) * 0.01))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(fg)
    out = [tuple(int(v) for v in st[i, :4]) for i in range(1, n) if st[i, 4] > min_frac * h * w]
    return sorted(out, key=lambda b: (b[0], b[1]))


@lru_cache(maxsize=1)
def priors() -> dict:
    if PRIOR_FILE.exists():
        return json.loads(PRIOR_FILE.read_text(encoding="utf-8"))
    return {}


def fit_priors(questions, parse, split_of) -> dict:
    """Most common answer per (source, object) on the dev split only."""
    c: dict = {}
    for q in questions:
        if split_of(q.path) != "dev" or q.ext not in ("png", "jpg", "jpeg"):
            continue
        p = parse(q.question)
        if p.measure == "count" and p.obj:
            c.setdefault(f"{q.source}/{p.obj}", Counter())[q.answer] += 1
    out = {k: {"value": v.most_common(1)[0][0], "freq": v.most_common(1)[0][1] / sum(v.values()), "n": sum(v.values())}
           for k, v in c.items()}
    PRIOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRIOR_FILE.write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    priors.cache_clear()
    return out


def answer(ctx, q) -> Answer:
    if q.measure == "area" and q.variant == "type_sum":
        img = cv2.imread(str(ctx.file))
        if img is None:
            return abstain(ROUTE, "unreadable image")
        from . import raster_area
        return raster_area.takeoff_answer(ctx, q, img)
    if q.measure != "count":
        return abstain(ROUTE, "raster route only counts (and room-type area takeoffs)")
    img = cv2.imread(str(ctx.file))
    if img is None:
        return abstain(ROUTE, "unreadable image")

    if q.obj == "storey":
        boxes = panels(img)
        value = max(1, len(boxes))
        ans = Answer(value=value, unit="count", route=ROUTE, confidence=0.85 if value == 1 else 0.75,
                     items=[{"panel": i + 1, "xywh": b} for i, b in enumerate(boxes)],
                     explanation=f"{value} separate drawing panel(s) on the white canvas")
        if ctx.evidence:
            cv = ImageCanvas(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
            for i, (x, y, w, h) in enumerate(boxes):
                cv.box((x, y, x + w, y + h), n=i + 1, color=PALETTE[i % len(PALETTE)], width=4)
            ans.evidence = finish(cv.img, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question,
                                  f"Answer: {value}   route: {ROUTE} (cost $0)",
                                  note="each numbered box is one separately scanned floor panel")
        return ans

    first = None
    if q.obj in ROOM_PATTERNS:
        first = room_labels(ctx, q, img)
    elif q.obj in detector.OBJECTS:
        first = detector.answer(ctx, q, img)
        if not first.answered and q.obj in FIXTURE_HEADS:
            second = fixture_count(ctx, q, img)
            if second.answered:
                first = second
            else:
                first.abstain = f"{first.abstain}; {second.route}: {second.abstain}"
    if first is not None and (first.answered or not getattr(ctx, "prior", False)):
        return first

    if getattr(ctx, "prior", False):
        pr = priors().get(f"{ctx.q.source}/{q.obj}")
        if pr:
            ans = Answer(value=pr["value"], unit="count", route="raster-prior", confidence=round(pr["freq"] * 0.5, 2),
                         explanation=f"no visual method yet; most common dev answer ({pr['freq']:.0%} of {pr['n']} dev questions)")
            if ctx.evidence:
                im = Image.open(ctx.file).convert("RGB")
                ans.evidence = finish(im, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question,
                                      f"Answer: {pr['value']} (PRIOR, not visual proof)   route: raster-prior",
                                      note=ans.explanation)
            return ans
    return first or abstain(ROUTE, f"no classical-CV detector for {q.obj} yet")


LABEL_ROUTE = "raster-ocr-labels"
NAMES = {"bedroom": "bedroom (MH)", "kitchen": "kitchen (K, KK, KEITTIO, AVOK, TUPAK)", "sauna": "sauna (S, SAUNA, LH)",
         "wc_room": "WC room (WC)", "bathroom": "bathroom (KH, KPH, PH, PESUH, SH)", "balcony": "balcony (PARVEKE)"}
MIN_WORDS_FOR_ZERO = 3      # a zero answer needs proof that the plan's labels were readable
# Which label counts are answered, per room type (set on the dev split, see CHANGELOG.md). Elsewhere
# the route abstains: kitchens and bathrooms are often unlabelled (an open kitchen inside "OH"), so a
# zero is not proof; two or more kitchen, bathroom or sauna labels usually means the scan also shows
# part of a neighbouring flat, or a stray "S"/"K" was read.
ANSWER_COUNTS = {"kitchen": {1}, "bathroom": {1}, "sauna": {1}, "bedroom": "positive",
                 "wc_room": "all", "balcony": {0, 1}}


def room_labels(ctx, q, img) -> Answer:
    toks = ocr_image(ctx.file)
    hits = find_rooms(toks, q.obj)
    words = [t for t in dedupe(toks) if is_word(t["text"])]
    if not hits and len(words) < MIN_WORDS_FOR_ZERO:
        return abstain(LABEL_ROUTE, f"only {len(words)} room labels readable; cannot prove absence")
    n = len(hits)
    rule = ANSWER_COUNTS[q.obj]
    if not (rule == "all" or (rule == "positive" and n > 0) or (isinstance(rule, set) and n in rule)):
        return abstain(LABEL_ROUTE, f"{n} {q.obj} label(s) read ({', '.join(h['text'] for h in hits)}); "
                                    f"this count is not reliable for {q.obj} on dev, abstaining")
    ans = Answer(value=n, unit="count", route=LABEL_ROUTE, confidence=0.7,
                 items=[{"n": i + 1, "label": h["text"], "match": h["part"], "ocr_conf": h["conf"],
                         "xyxy": [round(v) for v in h["box"]]} for i, h in enumerate(hits)],
                 explanation=f"{n} room label(s) read as {NAMES[q.obj]}"
                             + (f"; {len(words)} room labels read in total, none of this type" if not n else ""))
    if ctx.evidence:
        cv = ImageCanvas(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
        hit_boxes = {tuple(h["box"]) for h in hits}
        for t in words:                      # every other label that was read, thin grey, so absence is checkable
            b = quad_box(t["quad"])
            if tuple(b) not in hit_boxes:
                cv.box((b[0] - 2, b[1] - 2, b[2] + 2, b[3] + 2), color=(150, 150, 150), width=1)
        for i, h in enumerate(hits):
            b = h["box"]
            cv.box((b[0] - 3, b[1] - 3, b[2] + 3, b[3] + 3), n=i + 1, color=PALETTE[0], width=3)
        ans.evidence = finish(cv.img, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question,
                              f"Answer: {n}   route: {LABEL_ROUTE} (local OCR, cost $0)",
                              note=f"red numbered boxes: labels read as {NAMES[q.obj]}: "
                                   + ", ".join(h["text"] for h in hits) + "; grey boxes: other room labels read")
    return ans


FIXTURE_ROUTE = "raster-cv-fixtures"
FIXTURE_MODEL = "fixtures_mil_toilet_sink_shower_or_bath.npz"
FIXTURE_HEADS = {"toilet": 0, "sink": 1, "shower_or_bath": 2}
FIXTURE_NAMES = {"toilet": "toilet bowl", "sink": "sink or wash basin", "shower_or_bath": "shower or bathtub"}
UNSURE = (0.3, 0.7)          # a candidate scored inside this band makes the count unprovable
MAX_ANSWER = {"toilet": 3, "sink": 4, "shower_or_bath": 2}


def fixture_count(ctx, q, img) -> Answer:
    model = fx.load_model(FIXTURE_MODEL)
    if model is None:
        return abstain(FIXTURE_ROUTE, "fixture model not trained (tools/train_mil.py final)")
    if q.obj not in [str(h) for h in model["heads"]]:
        return abstain(FIXTURE_ROUTE, f"no {q.obj} head in the fixture model")
    j = [str(h) for h in model["heads"]].index(q.obj)
    cands = fx.candidates(img)
    if not cands:
        return abstain(FIXTURE_ROUTE, "no closed outlines found")
    sc = fx.score(model, fx.features(img, cands))[:, j]
    pos = [i for i in np.argsort(-sc) if sc[i] > 0.5]
    unsure = [i for i in range(len(sc)) if UNSURE[0] < sc[i] < UNSURE[1]]
    n = len(pos)
    if unsure:
        return abstain(FIXTURE_ROUTE, f"{n} {q.obj} found but {len(unsure)} candidate(s) scored between "
                                      f"{UNSURE[0]} and {UNSURE[1]}; count not provable")
    if n == 0 or n > MAX_ANSWER[q.obj]:
        return abstain(FIXTURE_ROUTE, f"{n} {q.obj} found; outside the range the detector is reliable for")
    items = [{"n": k + 1, "xywh": list(cands[i]["xywh"]), "score": round(float(sc[i]), 2)} for k, i in enumerate(pos)]
    ans = Answer(value=n, unit="count", route=FIXTURE_ROUTE, confidence=0.6, items=items,
                 explanation=f"{n} closed outline(s) classified as {FIXTURE_NAMES[q.obj]} "
                             f"(classifier trained from dev image counts; every other candidate scored < {UNSURE[0]})")
    if ctx.evidence:
        cv = ImageCanvas(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
        for it in items:
            x, y, w, h = it["xywh"]
            cv.box((x - 4, y - 4, x + w + 4, y + h + 4), n=it["n"], color=PALETTE[1], width=3)
        ans.evidence = finish(cv.img, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question,
                              f"Answer: {n}   route: {FIXTURE_ROUTE} (cost $0)",
                              note=f"each numbered box is the inside of one drawn {FIXTURE_NAMES[q.obj]} outline")
    return ans
