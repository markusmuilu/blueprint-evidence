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

import re

import cv2
import numpy as np
from PIL import Image

from ..answer import Answer, abstain, estimate
from .. import calibration
from ..evidence import AMBER, BLUE, GREY, PALETTE, ImageCanvas, evidence_path, finish
from .labels import ROOM_PATTERNS, dedupe, find_rooms, fuzzy_reads, is_word, label_reads, quad_box
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


def printed_label_area(ctx, q) -> Answer | None:
    """'How big is MH 1?' on a scan: the area printed beside the label read exactly as asked (QA 14 Nov: a Cannot,
    while "MH 1 11,0 m2" is printed). The label token must read as the asked label with its number; the area is the
    nearest printed area above, below or after it. Estimate: an OCR reading, not a measurement. None when not found."""
    from . import raster_area as RA
    from .labels import quad_box
    want = re.sub(r"[^a-z0-9]", "", (q.label or q.room_no or "").lower())
    if not want or not re.search(r"\d", want) or not re.search(r"[a-z]", want):
        return None
    _, toks, _ = RA.plan_tokens(ctx.file)
    best = None
    for t in toks or []:
        pa = RA.parse_area(t["text"])
        if pa is not None and re.sub(r"[^a-z0-9]", "", pa[1].lower()) == want:
            best = (0.0, t, t, pa[0])              # label and area in one token: "MH 1 11,0 m2"
            break
        if re.sub(r"[^a-z0-9]", "", t["text"].lower()) != want:
            continue
        a = quad_box(t["quad"])
        h = max(a[3] - a[1], 4.0)
        for u in toks:
            pu = RA.parse_area(u["text"])
            if u is t or pu is None or pu[1]:
                continue
            b = quad_box(u["quad"])
            ox = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) / max(1.0, min(a[2] - a[0], b[2] - b[0]))
            vg = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
            hg = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
            near = (ox >= 0.3 and vg <= 1.5 * h) or (vg == 0 and hg <= 2 * h)
            if near and (best is None or vg + hg < best[0]):
                best = (vg + hg, t, u, pu[0])
    if best is None:
        return None
    _, t, u, v = best
    label = q.label or q.room_no
    shown = t["text"] if t is u else f"{t['text']} / {u['text']}"
    return Answer(value=round(v, 2), unit="m2", route="raster-area", confidence=0.6, tier="estimate",
                  tier_reason="read from the area printed beside the room label on the scan (OCR), not measured",
                  explanation=f"{v:g} m2: the floor area printed beside the room label {label} on the plan ('{shown}').",
                  items=[{"n": 1, "room": label, "area_m2": round(v, 2), "printed": shown,
                          "xyxy": [round(c) for c in quad_box(u["quad"])]}])


def answer(ctx, q) -> Answer:
    if q.measure == "area" and (q.label or q.room_no) and q.variant != "type_sum":
        a = printed_label_area(ctx, q)
        if a is not None:
            return a
    if q.measure == "area" and q.variant == "type_sum":
        img = cv2.imread(str(ctx.file))
        if img is None:
            return abstain(ROUTE, "unreadable image")
        from . import raster_area
        return raster_area.takeoff_answer(ctx, q, img)
    whole_flat = q.obj == "room" and not q.label and not q.room_no and not q.apartment
    if whole_flat and ((q.measure == "area" and q.variant in ("total", "max")) or (q.measure == "count" and q.variant is None)):
        # the flat's own printed summary line ("3H+KT+S 73,50 m2"), or the largest printed room area
        from . import raster_summary
        a = (raster_summary.largest_room(ctx, q) if q.variant == "max" else raster_summary.answer(ctx, q))
        if not a.answered and q.measure == "area" and q.variant == "total":
            # no flat area printed: the labelled rooms of every type added up, as an estimate that says so
            b = _rooms_of_every_type(ctx, q)
            if b.answered:
                return b
            a.abstain = f"{a.abstain}; the rooms of every labelled type could not be measured either ({b.abstain})"
        if a.answered or q.measure == "area":
            return a
        if q.measure == "count" and not q.extra.get("apartment_scope"):
            # no summary line: the room labels read, by the "3H+K" convention, as an estimate
            b = raster_summary.label_rooms(ctx, q)
            if b.answered:
                return b
            a.abstain = f"{a.abstain}; {b.abstain}"
    if q.measure != "count":
        return abstain(ROUTE, "raster route only counts (and room-type area takeoffs)")
    img = cv2.imread(str(ctx.file))
    if img is None:
        return abstain(ROUTE, "unreadable image")

    if q.obj == "storey":
        boxes = panels(img)
        value = max(1, len(boxes))
        ans = Answer(value=value, unit="count", route=ROUTE, confidence=0.85 if value == 1 else 0.75, tier="proven",
                     items=[{"panel": i + 1, "xywh": b} for i, b in enumerate(boxes)],
                     explanation=f"{value} separate drawing panel(s) on the white canvas")
        calibration.apply(ans, q.obj)
        if ctx.evidence:
            cv = ImageCanvas(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
            for i, (x, y, w, h) in enumerate(boxes):
                cv.box((x, y, x + w, y + h), n=i + 1, color=PALETTE[i % len(PALETTE)], width=4)
            ans.evidence = finish(cv.img, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question,
                                  f"Answer: {value}" + ("   ESTIMATE" if ans.tier == "estimate" else "   proven")
                                  + f"   route: {ROUTE} (cost €0)",
                                  note="each numbered box is one separately scanned floor panel"
                                       + (f". Estimate because: {ans.tier_reason}" if ans.tier == "estimate" else ""))
        return ans

    first = None
    if q.obj == "room" and q.label:
        first = named_labels(ctx, q, img)
    elif q.obj in ROOM_PATTERNS:
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


def _with_waste(ctx, a: Answer) -> Answer:
    """A waste said in the question ("total floor area for laminate flooring with 8% waste") added to a summed area
    (judge review 14 Nov: the 8% was dropped). The rooms' net sum stays in the explanation."""
    from ..walls import parse_inputs
    w = parse_inputs(ctx.q.question).get("waste_pct")
    if not a.answered or not w or a.unit != "m2" or not isinstance(a.value, (int, float)):
        return a
    net = float(a.value)
    a.value = round(net * (1 + w / 100.0), 2)
    a.explanation = (f"{a.value:.2f} m2 to order = net {net:.2f} m2 + {w:g}% waste {a.value - net:.2f} m2 (given in the "
                     f"question). " + (a.explanation or ""))
    return a


def _rooms_of_every_type(ctx, q) -> Answer:
    """'Total floor area' on a scan with no printed flat area: the room-type takeoff over every known type."""
    from ..compose import ALL_TYPES, _base_query
    from . import raster_area
    img = cv2.imread(str(ctx.file))
    if img is None:
        return abstain(ROUTE, "unreadable image")
    q2 = _base_query(ctx.q.question, ALL_TYPES)
    b = raster_area.takeoff_answer(ctx, q2, img)
    if b.answered:
        b.explanation = ("No floor area is printed for the whole flat: the rooms of every labelled type are added up "
                         "(rooms without a readable label are not included). " + (b.explanation or ""))
        estimate(b, "the flat's area is not printed; sum of the labelled rooms")
        return _with_waste(ctx, b)
    # one type at a time: the types that are measured are added up, the others are named
    got, missed, seen, first = [], [], set(), None
    for t in ALL_TYPES:
        at = raster_area.takeoff_answer(ctx, _base_query(ctx.q.question, [t]), img)
        if at.answered:
            first = first or at
            for it in at.items or []:
                if isinstance(it, dict) and isinstance(it.get("area_m2"), (int, float)):
                    k = (it.get("room"), round(it["area_m2"], 2), tuple(it.get("xyxy") or ()))
                    if k not in seen:
                        seen.add(k)
                        got.append(it)
        elif at.abstain and not re.search(r"no room (of type|on this floor is labelled)|not found|no room", at.abstain):
            missed.append(t)
    if first is None or not got:
        return b
    total = round(sum(it["area_m2"] for it in got), 2)
    # the room labels read on the plan that are not in the sum, by name (night QA 15 Nov, high_quality-10241: a total
    # of the measured rooms only, with H, ET and TK left out unsaid)
    left = []
    try:
        _, toks, _ = raster_area.plan_tokens(ctx.file, allow_run=False)
        inside = {str(it.get("room") or "").strip().upper() for it in got}
        for t in toks or []:
            s = t["text"].strip()
            if raster_area.room_word(s) and s.upper() not in inside and s not in left:
                left.append(s)
    except Exception:
        left = []
    expl = (f"{total:.2f} m2: no floor area is printed for the whole flat, so the rooms of every labelled type are added "
            f"up, one type at a time: " + " + ".join(f"{it.get('room') or 'room'} {it['area_m2']:.2f}" for it in got)
            + (f". Not measured: {', '.join(missed)}" if missed else "")
            + (f". Labelled on the plan but not in this sum: {', '.join(left[:12])}; the total is too small by their area"
               if left else "")
            + ". Rooms without a readable label are not included.")
    if left:
        missed = missed + [f"labelled rooms {', '.join(left[:6])}"]
    out = Answer(value=total, unit="m2", route=first.route, confidence=0.4, items=got, explanation=expl,
                 evidence=first.evidence)
    estimate(out, "the flat's area is not printed; sum of the labelled rooms"
                  + (f"; not measured: {', '.join(missed)}" if missed else ""))
    return _with_waste(ctx, out)


LABEL_ROUTE = "raster-ocr-labels"
NAMES = {"bedroom": "bedroom (MH, BEDROOM, SCHLAFZIMMER)", "kitchen": "kitchen (K, KK, KEITTIO, AVOK, TUPAK, KITCHEN, KUCHE)",
         "sauna": "sauna (S, SAUNA, LH)", "wc_room": "WC room (WC, TOILET)",
         "bathroom": "bathroom (KH, KPH, PH, PE, PESUH, SH, BATHROOM, BADEZIMMER)", "balcony": "balcony (PARVEKE, BALCONY, BALKON)"}
MIN_WORDS_FOR_ZERO = 3      # a zero answer needs proof that the plan's labels were readable
# Which label counts are answered, per room type (set on the dev split, see CHANGELOG.md). Elsewhere
# the route abstains: kitchens and bathrooms are often unlabelled (an open kitchen inside "OH"), so a
# zero is not proof; two or more kitchen, bathroom or sauna labels usually means the scan also shows
# part of a neighbouring flat, or a stray "S"/"K" was read.
ANSWER_COUNTS = {"kitchen": {1}, "bathroom": {1}, "sauna": {1}, "bedroom": "positive",
                 "wc_room": "all", "balcony": {0, 1}}


def room_labels(ctx, q, img, calibrate: bool = True) -> Answer:
    """Room-label count. calibrate=False keeps the route's own tier (raster-rooms combines that tier with
    its detector reading, as tuned; the combined answer is calibrated as its own route)."""
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
    # labels the OCR read with one error (English/German words only): the plan's text is misread, so a
    # count that needs such a read, or a zero (another label may be misread beyond recognition), is not proof
    fz = [h for h in hits if h.get("fuzzy")]
    if fz:
        estimate(ans, f"{len(fz)} label(s) read with one OCR error: "
                      + ", ".join(f"{h['text']} as {h['fuzzy']}" for h in fz))
    elif not hits:
        misread = fuzzy_reads(toks)
        if misread:
            estimate(ans, f"the OCR misreads room labels on this plan ({', '.join(f'{t} as {w}' for t, w in misread[:4])}), "
                          f"so a zero is not proof of absence")
    if calibrate:
        calibration.apply(ans, q.obj)
    if ans.tier == "estimate":
        ans.confidence = 0.3
        ans.uncertain = [{"label": h["text"], "xyxy": [round(v) for v in h["box"]]} for h in hits]
    if ctx.evidence:
        cv = ImageCanvas(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
        hit_boxes = {tuple(h["box"]) for h in hits}
        for t in words:                      # every other label that was read, thin grey, so absence is checkable
            b = quad_box(t["quad"])
            if tuple(b) not in hit_boxes:
                cv.box((b[0] - 2, b[1] - 2, b[2] + 2, b[3] + 2), color=GREY, width=1)
        col = AMBER if ans.tier == "estimate" else BLUE
        for i, h in enumerate(hits):
            b = h["box"]
            cv.box((b[0] - 3, b[1] - 3, b[2] + 3, b[3] + 3), n=i + 1, color=col, width=3)
        tier = "   ESTIMATE" if ans.tier == "estimate" else "   proven"
        ans.evidence = finish(cv.img, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question,
                              f"Answer: {n}{tier}   ({LABEL_ROUTE}, local OCR, €0)",
                              note=f"{'amber' if ans.tier == 'estimate' else 'blue'} numbered boxes: labels read as {NAMES[q.obj]}: "
                                   + (", ".join(h["text"] for h in hits) or "none") + "; grey boxes: other room labels read"
                                   + (f". Estimate because: {ans.tier_reason}" if ans.tier == "estimate" else ""))
    return ans


def named_labels(ctx, q, img) -> Answer:
    """"How many rooms labelled 'X'": the readings of that label on the scan. Always an estimate: no Stoa
    scan question asks this, so the count has no dev measurement behind it."""
    toks = ocr_image(ctx.file)
    hits = label_reads(toks, q.label)
    words = [t for t in dedupe(toks) if len(t["text"].strip()) >= 2]
    n = len(hits)
    fz = [h for h in hits if h["fuzzy"]]
    ans = Answer(value=n, unit="count", route=LABEL_ROUTE, confidence=0.5, tier="estimate",
                 items=[{"n": i + 1, "label": h["text"], "ocr_conf": h["conf"], "xyxy": [round(v) for v in h["box"]]}
                        for i, h in enumerate(hits)],
                 uncertain=[{"label": h["text"], "xyxy": [round(v) for v in h["box"]]} for h in hits],
                 explanation=f"{n} reading(s) of the label '{q.label}' on the scan"
                             + (f" ({len(fz)} with one OCR error)" if fz else "")
                             + (f"; {len(words)} text items read in total" if not n else ""),
                 tier_reason="named-label counts on scans have no dev measurement (no Stoa scan question asks one)"
                             + ("" if n else "; a zero needs every label on the plan to be readable"))
    if ctx.evidence:
        cv = ImageCanvas(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
        for i, h in enumerate(hits):
            b = h["box"]
            cv.box((b[0] - 3, b[1] - 3, b[2] + 3, b[3] + 3), n=i + 1, color=AMBER, width=3)
        ans.evidence = finish(cv.img, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question,
                              f"Answer: {n}   ESTIMATE   ({LABEL_ROUTE}, local OCR, €0)",
                              note=f"amber numbered boxes: readings of '{q.label}': " + ", ".join(h["text"] for h in hits)
                                   + f". Estimate because: {ans.tier_reason}")
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
            cv.box((x - 4, y - 4, x + w + 4, y + h + 4), n=it["n"], color=BLUE, width=3)
        ans.evidence = finish(cv.img, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question,
                              f"Answer: {n}   route: {FIXTURE_ROUTE} (cost €0)",
                              note=f"each numbered box is the inside of one drawn {FIXTURE_NAMES[q.obj]} outline")
    return ans
