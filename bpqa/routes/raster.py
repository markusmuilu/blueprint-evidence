"""Raster route (scanned plans, CubiCasa5K PNGs). Classical computer vision, no model.

Implemented:
  storeys  = number of separate drawing panels. Multi-storey CubiCasa plans paste each floor
             as its own scan onto a pure-white canvas, so panels are the large connected
             components of non-white pixels after a morphological close.

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
    if q.measure != "count":
        return abstain(ROUTE, "raster route only counts")
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
    return abstain(ROUTE, f"no classical-CV detector for {q.obj} yet")
