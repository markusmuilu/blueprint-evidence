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

Counted answers come in two tiers (Answer.tier): "proven" when the route's tuned conditions hold,
"estimate" otherwise, with the reason. An earlier dev-majority fallback (`--prior`) is no longer
in the route; eval.py --prior reports it as a labelled baseline only.
"""
from __future__ import annotations


import cv2
import numpy as np
from PIL import Image

from ..answer import Answer, abstain, estimate
from ..evidence import PALETTE, ImageCanvas, evidence_path, finish
from .labels import ROOM_PATTERNS, dedupe, find_rooms, is_word, quad_box
from .ocr import ocr_image
from . import detector
from . import fixtures as fx

ROUTE = "raster-cv"


def panels(img_bgr, white=245, min_frac=0.04):
    h, w = img_bgr.shape[:2]
    fg = (img_bgr.min(axis=2) < white).astype(np.uint8)
    k = max(3, int(min(h, w) * 0.01))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(fg)
    out = [tuple(int(v) for v in st[i, :4]) for i in range(1, n) if st[i, 4] > min_frac * h * w]
    return sorted(out, key=lambda b: (b[0], b[1]))


def answer(ctx, q) -> Answer:
    if q.measure != "count":
        return abstain(ROUTE, "raster route only counts")
    img = cv2.imread(str(ctx.file))
    if img is None:
        return abstain(ROUTE, "unreadable image")

    if q.obj == "storey":
        boxes = panels(img)
        value = max(1, len(boxes))
        ans = Answer(value=value, unit="count", route=ROUTE, confidence=0.85 if value == 1 else 0.75, tier="proven",
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
    return first or abstain(ROUTE, f"no raster method for {q.obj}")


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
    n = len(hits)
    rule = ANSWER_COUNTS[q.obj]
    ans = Answer(value=n, unit="count", route=LABEL_ROUTE, confidence=0.7, tier="proven",
                 items=[{"n": i + 1, "label": h["text"], "match": h["part"], "ocr_conf": h["conf"],
                         "xyxy": [round(v) for v in h["box"]]} for i, h in enumerate(hits)],
                 explanation=f"{n} room label(s) read as {NAMES[q.obj]}"
                             + (f"; {len(words)} room labels read in total, none of this type" if not n else ""))
    if not hits and len(words) < MIN_WORDS_FOR_ZERO:
        estimate(ans, f"only {len(words)} room labels readable, so a zero is not proof of absence")
    elif not (rule == "all" or (rule == "positive" and n > 0) or (isinstance(rule, set) and n in rule)):
        estimate(ans, f"{n} {q.obj} label(s) read; on dev this count is often wrong for {q.obj} "
                      f"(unlabelled rooms, or labels of a neighbouring flat in the scan)")
    if ans.tier == "estimate":
        ans.confidence = 0.3
        ans.uncertain = [{"label": h["text"], "xyxy": [round(v) for v in h["box"]]} for h in hits]
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
        tier = "   (ESTIMATE: " + ans.tier_reason + ")" if ans.tier == "estimate" else "   (proven)"
        ans.evidence = finish(cv.img, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question,
                              f"Answer: {n}{tier}   route: {LABEL_ROUTE} (local OCR, cost $0)",
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
