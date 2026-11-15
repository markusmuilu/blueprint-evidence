"""The apartment summary printed on Finnish scans ("3H+KT+S 73,50 m2") and what it answers.

A CubiCasa plan often prints one line for the whole flat: the number of rooms (H = huone), the kitchen type
(K, KK, KT, TK, TUPAK, ...), the other spaces (S sauna, VH walk-in closet, ...), and the flat's floor area. It is
a stated value, not a room: the label route skips it (labels.parts), and the area route must not read its area as
a room called "KT" (that was a printed "room" of 73.5 m2 in the scale and the sums).

  find_summary(tokens)      the summary in one OCR pass: rooms, extras, area, the boxes it sits in.
  summary_tokens(tokens)    the tokens (identity) that belong to a summary, so read_rooms leaves them out.
  answer(ctx, q)            "total floor area" -> the printed area; "how many rooms" -> the printed room count.
  largest_room(ctx, q)      "the largest room" -> the biggest printed room area.

The answer is the printed value. It is proven when the plan's own room areas do not contradict it (their sum is not
more than the flat's area), and an estimate when the OCR read the line less cleanly (area in a separate token that is
not clearly the flat's, or rooms that add up to more than the flat).
"""
from __future__ import annotations

import re
from pathlib import Path

from ..answer import Answer, abstain, estimate

ROUTE = "raster-area"
CELL = "printed-summary"

# "3H+KT+S", "3h+k+s!", "1 H + TUPAK.", "2H,K,S", "3H+K+S72m2", "6H+K= 176 m2": a digit right before H that is not
# part of a longer number or word ("B34H", "4.2H" are misreads and rejected), then + or , and the kitchen type.
# A stray stroke the OCR reads between H and the plus ("2H-+KK", scan hunt 15 Nov, high_quality_architectural-2145) is skipped.
START = re.compile(r"(?<![\d.,A-Za-z])(\d)\s*[Hh]\s*[-.]?\s*[+,]\s*")
COMP = re.compile(r"\s*([A-Za-z]{1,6})\.?(?:\s*\([A-Za-z ]+\))?\s*(?:[+,/]|(?=[^A-Za-z]|$))")
AREA_IN = re.compile(r"(\d{2,3}(?:[.,]\d{1,2})?)\s*[mM]")
KITCHEN = {"K": "kitchen", "KK": "kitchenette", "KT": "kitchen space", "TK": "kitchen", "TUPAK": "kitchen with dining space",
           "UPAK": "kitchen with dining space", "AVOK": "open kitchen", "KOK": "kitchen"}
EXTRA = {"S": "sauna", "VH": "walk-in closet", "KHH": "utility room", "ALK": "alcove", "KPH": "bathroom", "KH": "bathroom",
         "PH": "washroom", "WC": "WC", "VAR": "storage", "RT": "dining area", "P": "balcony", "PARV": "balcony"}
MIN_FLAT_M2, MAX_FLAT_M2 = 15.0, 400.0


def _box(t):
    xs = [p[0] for p in t["quad"]]
    ys = [p[1] for p in t["quad"]]
    return min(xs), min(ys), max(xs), max(ys)


def _parse_token(text: str):
    """(rooms, [component letters], area or None) for a token that reads as a flat summary, else None."""
    m = START.search(text)
    if not m:
        return None
    rest, comps, pos = text[m.end():], [], 0
    while True:
        c = COMP.match(rest, pos)
        if not c:
            break
        comps.append(c.group(1).upper())
        pos = c.end()
        if len(comps) >= 6 or not re.search(r"[+,/]\s*$", rest[:pos]):
            break
    if not comps or comps[0] not in KITCHEN:
        return None
    a = AREA_IN.search(rest[pos:].replace("=", " "))
    area = float(a.group(1).replace(",", ".")) if a else None
    if area is not None and not MIN_FLAT_M2 <= area <= MAX_FLAT_M2:
        area = None
    return int(m.group(1)), comps, area


def _gap(a, b):
    """(horizontal overlap fraction of the narrower box, vertical gap, same-line horizontal gap) between two boxes."""
    ox = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) / max(1.0, min(a[2] - a[0], b[2] - b[0]))
    vg = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    hg = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    return ox, vg, hg


def find_summary(toks: list[dict], min_conf: float = 0.5) -> dict | None:
    """The flat summary of one OCR pass: {rooms, comps, extras, text, area, area_text, boxes, tokens, conf, area_in_token}."""
    from .raster_area import parse_area
    toks = [t for t in toks if t["conf"] >= min_conf and not t.get("mirror")]
    for t in toks:
        p = _parse_token(t["text"])
        if p is None:
            continue
        rooms, comps, area = p
        out = {"rooms": rooms, "comps": comps, "text": t["text"], "area": area, "area_in_token": area is not None,
               "boxes": [_box(t)], "tokens": [t], "conf": t["conf"], "area_text": t["text"] if area else None}
        if area is None:                                   # the area printed next to it, in its own token
            b = _box(t)
            h = max(b[3] - b[1], 6.0)
            best = None
            for u in toks:
                if u is t:
                    continue
                pa = parse_area(u["text"])
                if pa is None or not MIN_FLAT_M2 <= pa[0] <= MAX_FLAT_M2 or pa[1]:
                    continue                                # a bare area: no room word beside it
                ub = _box(u)
                ox, vg, hg = _gap(b, ub)
                near = (ox >= 0.4 and vg <= 1.6 * h) or (vg == 0 and hg <= 3 * h and abs((ub[1] + ub[3]) / 2 - (b[1] + b[3]) / 2) < 0.7 * h)
                if near:
                    d = vg + hg
                    if best is None or d < best[0]:
                        best = (d, u, pa[0])
            if best is not None:
                others = [parse_area(u["text"])[0] for u in toks if u is not t and u is not best[1] and parse_area(u["text"])]
                if not others or best[2] > 1.2 * max(others):      # the flat is bigger than every room printed on its plan
                    out.update(area=best[2], area_text=best[1]["text"], boxes=out["boxes"] + [_box(best[1])],
                               tokens=out["tokens"] + [best[1]], conf=min(t["conf"], best[1]["conf"]))
        out["extras"] = [EXTRA.get(c, c) for c in comps[1:]]
        out["kitchen"] = KITCHEN[comps[0]]
        return out
    return None


def summary_tokens(toks: list[dict]) -> set[int]:
    s = find_summary(toks)
    return {id(t) for t in s["tokens"]} if s else set()


def read(f: Path, allow_run: bool = True) -> tuple[dict | None, list, tuple]:
    """(summary, the pass's tokens in its reading frame, orientation): the plan's best reading pass first, then the
    other non-mirrored passes. The text is read in the frame the pass reads upright in, so "below" means below the
    text; the boxes of the summary are mapped back to original pixels."""
    import cv2
    from .ocr import PASSES, ocr_image
    from .raster_area import _map_box, frame_fns, plan_tokens
    orient, toks, _ = plan_tokens(f, allow_run=allow_run)
    img = cv2.imread(str(f))
    if toks is None or img is None:
        return None, [], orient
    h, w = img.shape[:2]

    def in_frame(ts, p):
        fwd, inv = frame_fns(p[0], p[1], w, h)
        return [{**t, "quad": [list(fwd(*q)) for q in t["quad"]], "mirror": False} for t in ts], inv

    passes = [(orient, toks)]
    s = None
    for p, ts in passes:
        ft, inv = in_frame(ts, p)
        s = find_summary(ft)
        if s is not None:
            s["boxes"] = [_map_box(b, inv) for b in s["boxes"]]
            return s, ft, p
    allt = ocr_image(f, allow_run=False, partial=True) or []
    for p in PASSES:
        if p[0] or p == orient:
            continue
        pt = [t for t in allt if not t.get("mirror") and t["rot"] == p[1]]
        if not pt:
            continue
        ft, inv = in_frame(pt, p)
        s = find_summary(ft)
        if s is not None:
            s["boxes"] = [_map_box(b, inv) for b in s["boxes"]]
            return s, ft, p
    return None, [], orient


def rooms_phrase(s: dict) -> str:
    extras = "".join(f" plus {x}" for x in [s["kitchen"]] + s["extras"])
    return f"{s['rooms']} room{'s' if s['rooms'] != 1 else ''}{extras}"


def _printed_areas(toks, skip: set[int]) -> list[float]:
    from .raster_area import parse_area
    out = []
    for t in toks:
        if id(t) in skip or t["conf"] < 0.5 or t.get("mirror"):
            continue
        pa = parse_area(t["text"])
        if pa is not None:
            out.append(pa[0])
    return out


def _evidence(ctx, q, boxes, head, note, extra_boxes=()):
    from PIL import Image
    import cv2
    from ..evidence import AMBER, BLUE, ImageCanvas, evidence_path, finish
    img = cv2.imread(str(ctx.file))
    if img is None:
        return None
    cv = ImageCanvas(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
    for i, b in enumerate(boxes):
        cv.box((b[0] - 4, b[1] - 4, b[2] + 4, b[3] + 4), n=i + 1, color=BLUE, width=3)
    for b in extra_boxes:
        cv.box((b[0] - 3, b[1] - 3, b[2] + 3, b[3] + 3), n=None, color=AMBER, width=2)
    return finish(cv.img, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question, head, note=note)


def answer(ctx, q) -> Answer:
    """Total floor area ("how many m2 in total") or the number of rooms ("how many rooms in the flat") from the
    summary line printed on the plan."""
    f = Path(ctx.file)
    s, toks, orient = read(f)
    if s is None:
        return abstain(ROUTE, "no apartment summary line (such as 3H+K+S 73 m2) read on the plan")
    asks_area = q.measure == "area"
    if asks_area and s["area"] is None:
        return abstain(ROUTE, f"the plan prints its summary '{s['text']}' but no floor area was read next to it")
    skip = {id(t) for t in s["tokens"]}
    rooms_sum = round(sum(_printed_areas(toks, skip)), 2)
    shown = f"{s['text'].strip()}" + (f" {s['area_text']}" if s["area_text"] and s["area_text"] != s["text"] else "")
    if asks_area:
        value, unit = round(s["area"], 2), "m2"
        expl = (f"{value:g} m2: the floor area printed on the plan's apartment summary ('{shown}'), not summed from rooms. "
                f"The summary reads {rooms_phrase(s)}.")
    else:
        value, unit = s["rooms"], "rooms"
        expl = (f"{value} rooms: read from the apartment summary printed on the plan ('{shown}'), which reads "
                f"{rooms_phrase(s)} (H = huone, a room; the kitchen type and the sauna are named separately).")
    ans = Answer(value=value, unit=unit, route=ROUTE, confidence=0.9, tier="proven", tier_cell=CELL, explanation=expl,
                 items=[{"n": 1, "reading": "apartment summary", "text": s["text"], "rooms": s["rooms"],
                         "kitchen": s["kitchen"], "other": s["extras"], "area_m2": s["area"], "area_text": s["area_text"]}])
    if asks_area and s["area"] is not None and rooms_sum > 1.05 * s["area"]:
        estimate(ans, f"the room areas printed on the plan add up to {rooms_sum:g} m2, more than the {s['area']:g} m2 "
                      f"of the summary: one of the two was misread")
        ans.tier_cell = ""
    elif re.search(r"[Hh]\s*[-.]\s*[+,]", s["text"]):
        estimate(ans, f"the summary line was read with a stray mark between H and the plus ('{s['text'].strip()}')")
        ans.tier_cell = ""
    elif s["conf"] < 0.85:
        estimate(ans, f"the summary line was read with low OCR confidence ({s['conf']:.2f})")
        ans.tier_cell = ""
    elif asks_area and not s["area_in_token"]:
        ans.explanation += f" The area sits in its own text box below or beside the summary ({s['area_text']}); the rooms printed on the plan add up to {rooms_sum:g} m2."
    if ctx.evidence:
        head = f"Answer: {value:g} {unit}   route: {ROUTE} (cost €0)"
        ans.evidence = _evidence(ctx, q, s["boxes"], head,
                                 f"The apartment summary printed on the plan, boxed: {shown}. It reads {rooms_phrase(s)}"
                                 + (f", {s['area']:g} m2." if s["area"] is not None else "."))
    return ans


def largest_room(ctx, q) -> Answer:
    """The biggest room area printed on a scan whose rooms print their areas."""
    from .. import takeoff
    import cv2
    from .raster_area import plan_rooms, plan_tokens
    f = Path(ctx.file)
    orient, toks, extra = plan_tokens(f)
    img = cv2.imread(str(f))
    if toks is None or img is None:
        return abstain(ROUTE, "image or OCR unavailable")
    rooms, free, _ = plan_rooms(toks, orient, (img.shape[1], img.shape[0]), extra)
    rooms = [r for r in rooms if r.printed is not None and not (r.label and "balcony" in takeoff.room_types(r.label))]
    if len(rooms) < 3:
        return abstain(ROUTE, f"only {len(rooms)} printed room area(s) read on the plan")
    top = max(rooms, key=lambda r: r.printed)
    unpaired = [l["text"] for l in free if (takeoff.room_types(l["text"]) - {"balcony"})]
    name = top.label or "an unlabelled room"
    s, _, _ = read(f)
    total = round(sum(r.printed for r in rooms), 2)
    ans = Answer(value=round(top.printed, 2), unit="m2", route=ROUTE, confidence=0.8, tier="proven", tier_cell=CELL,
                 items=[{"n": i + 1, "room": r.label or "unlabelled", "area_m2": r.printed}
                        for i, r in enumerate(sorted(rooms, key=lambda r: -r.printed))],
                 explanation=(f"{top.printed:g} m2: {name} is the largest of the {len(rooms)} room areas printed on the plan "
                              f"(next: " + ", ".join(f"{r.label or 'unlabelled'} {r.printed:g}" for r in sorted(rooms, key=lambda r: -r.printed)[1:4])
                              + "); balconies not counted."))
    why = []
    if unpaired:
        why.append("room label(s) without a printed area: " + ", ".join(unpaired[:6]))
    if top.label is None:
        why.append("the largest area has no room label next to it")
    if s is not None and s["area"] is not None:
        if total > 1.05 * s["area"]:
            why.append(f"the printed room areas add up to {total:g} m2, more than the flat's printed {s['area']:g} m2")
        elif total < 0.8 * s["area"]:
            why.append(f"the printed room areas add up to {total:g} m2 of the flat's {s['area']:g} m2: a room's area may not have been read")
    else:
        why.append("no printed flat area to check that no room's area was missed")
    if why:
        estimate(ans, "; ".join(why))
        ans.tier_cell = ""
    else:
        ans.explanation += f" The printed room areas add up to {total:g} m2, within the flat's printed {s['area']:g} m2."
    if ctx.evidence:
        boxes = [top.area_box] + ([top.label_box] if top.label_box is not None else [])
        ans.evidence = _evidence(ctx, q, boxes, f"Answer: {ans.value:g} m2 ({name})   route: {ROUTE} (cost €0)",
                                 f"Largest printed room area: {name} {top.printed:g} m2 (boxed). "
                                 f"{len(rooms)} printed room areas read.",
                                 extra_boxes=[r.area_box for r in rooms if r is not top and r.area_box is not None])
    return ans


# ----------------------------------------------------------------------------------------------- rooms from labels

# The rooms a Finnish "3H+K" counts: the living room and the other rooms named "huone" (OH, MH, H, TYOH ...). The
# English and German words are the same rooms (living and sleeping rooms). The kitchen is named separately, and
# bathrooms, WCs, halls, storage, saunas and balconies are spaces, not rooms.
ROOM_LABEL = re.compile(r"(?:OH|OLOHUONE|MH|MHH|MAKUUH(?:UONE)?|H|HUONE|TYOH(?:UONE)?|ASKH|AH|LIVINGROOM|LIVING|LOUNGE|"
                        r"SITTINGROOM|FAMILYROOM|DRAWINGROOM|BEDROOM|BEDRM|BR|WOHNEN|WOHNZIMMER|SCHLAFZIMMER|"
                        r"KINDERZIMMER|ELTERNZIMMER|GASTZIMMER|ZIMMER|VARDAGSRUM|SOVRUM|SOV)\d?\.?")


FI_ROOM = re.compile(r"(?:OH|OLOHUONE|MH|MHH|MAKUUH(?:UONE)?|H|HUONE|TYOH(?:UONE)?|ASKH|AH)\d?\.?")


def label_rooms(ctx, q) -> Answer:
    """'How many rooms?' on a scan without a summary line: the room labels read, counted by the Finnish "3H+K"
    convention (living room and other rooms; the kitchen separately), every labelled space listed. An estimate:
    a label the OCR did not read is a room not counted, and no dev question measures this."""
    from .labels import dedupe, exact, is_word, norm, parts, quad_box, _plan_word
    from .ocr import ocr_image
    toks = ocr_image(Path(ctx.file)) or []
    if not toks:
        return abstain(ROUTE, "no text read on the plan, so no room labels to count")
    skip = summary_tokens(toks)
    rooms, kitchens, spaces, boxes = [], [], [], []
    for t in dedupe([t for t in toks if id(t) not in skip], prefer=is_word):
        bare = norm(t["text"]).strip(" .")
        # a lone "H" (huone) is a room label; parts() drops it, as "3H" in a summary line is not a room
        for p in ([bare] if re.fullmatch(r"H\d?", bare) else parts(t["text"])):
            if ROOM_LABEL.fullmatch(p):
                rooms.append(p)
                boxes.append(quad_box(t["quad"]))
            elif exact("kitchen", p):
                kitchens.append(p)
            elif any(exact(o, p) for o in ("bathroom", "wc_room", "sauna", "balcony", "bedroom")) or _plan_word(p):
                spaces.append(p)
    if not rooms:
        return abstain(ROUTE, "no living room or bedroom label read on the plan"
                              + (f" (labels read: {', '.join(kitchens + spaces)})" if kitchens or spaces else ""))
    n = len(rooms)
    total = n + len(kitchens) + len(spaces)
    kit = f", plus kitchen {', '.join(kitchens)}" if kitchens else ""
    fi = any(FI_ROOM.fullmatch(r) for r in rooms)
    conv = (f"by the Finnish convention ({', '.join(rooms)}: the living room and the other rooms, as in a '{n}H+K' "
            f"summary; the kitchen is named separately)" if fi else
            f"counting living rooms and bedrooms ({', '.join(rooms)}; the kitchen separately)")
    expl = (f"{n} room{'s' if n != 1 else ''} {conv}{kit}; {total} labelled spaces in "
            f"total: {', '.join(rooms + kitchens + spaces)}. Bathrooms, WCs, halls, storage, saunas and balconies are not "
            f"counted as rooms. No apartment summary is printed on this plan; a room whose label was not read is not counted.")
    ans = Answer(value=n, unit="rooms", route=ROUTE, confidence=0.5, explanation=expl,
                 items=[{"n": i + 1, "label": r, "xyxy": [round(v) for v in b]} for i, (r, b) in enumerate(zip(rooms, boxes))]
                       + [{"label": x, "counted": False} for x in kitchens + spaces])
    estimate(ans, "rooms counted from the labels read, by the '3H+K' convention; no printed summary to confirm it")
    if ctx.evidence:
        ans.evidence = _evidence(ctx, q, boxes, f"Answer: {n} rooms (estimate)   route: {ROUTE} (cost €0)",
                                 f"Room labels counted (boxed): {', '.join(rooms)}{kit}. Other labelled spaces: "
                                 f"{', '.join(spaces) or 'none'}.")
    return ans
