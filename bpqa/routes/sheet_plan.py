"""raster-sheet route: questions on a large raster drawing sheet (bpqa/sheet.py reads it once, cached).

Handled here, on images with a long side of sheet.LARGE_SIDE px or more:
  doors, windows    the scan detector run on tiles of a copy at DET_PPM px per m (inside its training scales),
                    one view, blank tiles skipped; windows are the boxes in the outer walls (the detector also
                    boxes glazed panels in internal walls); always tier "estimate": the detector was trained on
                    CubiCasa scans and was checked by eye on one sheet only (EYE_CHECK)
  is there a ...    "yes" when a room name read on the sheet matches; a name not read abstains (not a "no")
  rooms by name     the question's room words are matched to the room names read on the sheet ("classrooms"
                    matches "Secondary Classroom" and "Secondary Classroom (Severe Needs)"; "accessible toilet"
                    only that label); count = the labels matched, area = their traced regions x the sheet scale
  total floor area  the building footprint traced from the walls (gross, walls included), with the sum of the
                    named rooms' regions (net) stated next to it
  room perimeters   the traced outline length of the named rooms (openings not deducted); `rooms()` gives each
                    room its perimeter and the door boxes in its walls, in the record shape bpqa/baseboard.py reads
Everything is tier "estimate". A scale where the printed ratio and the scale bar agree is strong evidence for
the scale, but the room outlines are traced, not printed, so a measured area is still an estimate.
Questions this route cannot read get an abstention that says so: the scan routes built for 2000 px images
would run out of memory on a sheet.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..answer import Answer, abstain
from ..evidence import AMBER, BLUE, GREY, ImageCanvas, evidence_path, finish, record_context, record_steps
from .. import sheet as S

ROUTE = "raster-sheet"
RASTER_EXT = {"png", "jpg", "jpeg", "tif", "tiff"}
DET_PPM = 80.0             # px per m the detector sees (training tiles: CubiCasa at about 50 px/m x 1.25 to 2.0)
DET_TILE, DET_OVERLAP = 1024, 192
CANVAS_SIDE = 4000         # evidence is drawn on a copy this long (a 14000 px canvas costs 400 MB)
STOP = {"how", "many", "what", "is", "the", "area", "areas", "total", "of", "are", "there", "on", "this", "plan",
        "drawing", "floor", "level", "in", "a", "an", "and", "sum", "combined", "net", "gross", "size", "sizes",
        "number", "count", "do", "does", "we", "have", "shown", "drawn", "square", "metres", "meters", "m2", "sqm",
        "all", "each", "per", "every", "find", "give", "me", "tell", "s", "it", "its", "which", "with", "at",
        "for", "to", "be", "these", "those", "their", "perimeter", "length", "skirting", "baseboard", "much",
        "big", "large", "ground", "storey", "story", "sheet", "building", "school", "exist", "exists", "i", "can",
        "see", "please", "measure", "measured", "room", "rooms"}
GENERIC = {"room", "rooms", "space", "spaces"}
TOTAL_RE = re.compile(r"\b(total|whole|overall|entire|gross)\b.*\b(floor )?area\b|\bfloor area\b|\bgross\b", re.I)
PERIM_RE = re.compile(r"\b(perimeter|skirting|baseboard|base board)\b", re.I)
EXIST_RE = re.compile(r"^\s*(is|are) there\b|\b(does|do) (the|this|it|we)\b.*\b(have|has|contain|include|show)\b|"
                      r"\bis there (a|an|any)\b|\bare there any\b", re.I)
COUNT_RE = re.compile(r"\bhow many\b|\bnumber of\b|\bcount\b|\bkuinka monta\b|\bmontako\b|\bwie ?viele\b|\banzahl\b|"
                      r"\bcombien\b|\bhvor mange\b|\bhur manga\b", re.I)
AREA_Q_RE = re.compile(r"\b(area|perimeter|skirting|baseboard|m2|square)\b|pinta-?ala|neliometri|neliömetri|fl(a|ä)che|"
                       r"quadratmeter|m²", re.I)


def applies(ctx) -> bool:
    return ctx.q.ext in RASTER_EXT and S.is_large(ctx.file)


def sing(w: str) -> str:
    w = w.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("es") and w[:-2].endswith(("sh", "ch", "ss", "x")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


# words a question may use for a room name printed differently on English drawings (singular, after sing());
# a two-word key is matched before one-word keys
SYNONYMS = {("class", "room"): ["classroom"], ("store", "room"): ["store"], ("storage", "room"): ["store"],
            ("disabled", "toilet"): ["accessible", "toilet"],
            ("accessible", "wc"): ["accessible", "toilet"], ("disabled", "wc"): ["accessible", "toilet"],
            ("staircase",): ["stair"], ("stairway",): ["stair"], ("stairwell",): ["stair"],
            ("wc",): ["toilet"], ("lavatory",): ["toilet"], ("loo",): ["toilet"], ("restroom",): ["toilet"],
            ("elevator",): ["lift"], ("storeroom",): ["store"], ("storage",): ["store"]}


def _norm(text: str) -> str:
    return text.lower().replace("’", "'").replace("‘", "'").replace("-", "").replace("'", "")


def label_words(text: str) -> list[str]:
    t = re.sub(r"\(.*?\)", " ", text)                    # "(Severe Needs)", "(Ground)": qualifiers, not the name
    return [sing(w) for w in re.findall(r"[a-z]+", _norm(t))]


# Finnish and German room words a question may use on an English sheet (judge review 14 Nov: "Kuinka monta
# wc-tilaa" abstained while the English question found 7 toilet rooms). Matched on the folded word, so inflected
# and compound forms match (wc-tilaa -> wctilaa, WC-Raume -> wcraume, kaytavien, luokkahuoneita).
FOREIGN_ROOM = [(re.compile(r"^(wc\w*|vess\w+|toalet\w*|toilette\w*|klo|klos)$"), "toilet"),
                (re.compile(r"^(kaytav\w*|korridor\w*|flur\w*)$"), "corridor"),
                (re.compile(r"^(luokkahuone\w*|luok(ka|an|ass|ia|ien|at)\w*|klassenzimmer\w*|klassenr(au|ae|a)m\w*)$"), "classroom"),
                (re.compile(r"^(keitti\w*|kuche\w*|kueche\w*)$"), "kitchen"),
                (re.compile(r"^(toimisto\w*|buro\w*|buero\w*)$"), "office"),
                (re.compile(r"^(varast\w*|lager\w*|abstellr\w*)$"), "store"),
                (re.compile(r"^(porra\w*|portai\w*|treppe\w*)$"), "stair"),
                (re.compile(r"^(hissi\w*|aufzug\w*|aufzuge)$"), "lift"),
                (re.compile(r"^(suihku\w*|dusche\w*)$"), "shower"),
                (re.compile(r"^(pukuhuone\w*|umkleide\w*)$"), "changing")]


def _foreign(w: str) -> str:
    for rx, en in FOREIGN_ROOM:
        if rx.match(w):
            return en
    return w


def question_words(q: str) -> list[str]:
    from ..query import fold
    ws = ["wc" if w == "wcs" else sing(_foreign(w)) for w in re.findall(r"[a-z]+", _norm(fold(q)))]
    out, i = [], 0
    while i < len(ws):
        for n in (2, 1):
            k = tuple(ws[i:i + n])
            if len(k) == n and k in SYNONYMS:
                out += SYNONYMS[k]
                i += n
                break
        else:
            out.append(ws[i])
            i += 1
    return out


def room_labels(d: dict) -> list[dict]:
    return [l for l in d["labels"] if S.is_room_name(l["text"]) and l.get("rot", 0) in (0, 90)]


def contains(seq: list[str], sub: list[str]) -> bool:
    n = len(sub)
    return any(seq[i:i + n] == sub for i in range(len(seq) - n + 1))


def name_heads(text: str) -> list[list[str]]:
    """The parts of a room name joined by "and" ("Changing and Showers" -> [changing], [shower]); in English the
    last word of each part is its head noun."""
    ws, parts, cur = label_words(text), [], []
    for w in ws:
        if w == "and":
            parts.append(cur)
            cur = []
        else:
            cur.append(w)
    parts.append(cur)
    return [p for p in parts if p]


def ends_with(text: str, ph: list[str]) -> bool:
    return any(p[-len(ph):] == ph for p in name_heads(text) if len(p) >= len(ph))


def match_phrase(question: str, labels: list[dict]) -> tuple[list[str], list[dict]]:
    """(phrase, labels) for the longest run of question words that appears, as consecutive words, in at least one
    room name read on the sheet. When some names end with the phrase (its head noun: "lift" in "Lift", not in
    "Lift Landing"; "store" in "Secure Store"), only those count. A lone generic word ("room") never matches."""
    qw = question_words(question)
    best: tuple[list[str], list[dict]] = ([], [])
    for n in range(min(5, len(qw)), 0, -1):
        for i in range(len(qw) - n + 1):
            ph = qw[i:i + n]
            if all(w in STOP for w in ph) or (n == 1 and ph[0] in GENERIC):
                continue
            if ph[0] in STOP - GENERIC or ph[-1] in STOP - GENERIC:
                continue
            hits = [l for l in labels if contains(label_words(l["text"]), ph)]
            heads = [l for l in hits if ends_with(l["text"], ph)]
            hits = heads or hits
            if hits and (len(ph) > len(best[0])):
                best = (ph, hits)
        if best[0]:
            break
    return best


# ---------------------------------------------------------------- detector on the sheet

_DET_LOCKS: dict[str, threading.Lock] = {}


def detect(file: Path, ppm: float | None, progress=None) -> tuple[list[dict], float]:
    """Boxes (original px) of the scan detector on tiles of a copy at DET_PPM px per m, blank tiles skipped;
    cached per drawing. Returns (boxes, factor). One run per drawing at a time: a second caller (a question
    asked while the upload precompute runs the detector) waits for the first and reads its cache.
    `progress("doors", done, total)` is called per batch of tiles."""
    with _DET_LOCKS.setdefault(str(Path(file).resolve()), threading.Lock()):
        return _detect(file, ppm, progress)


def _detect(file: Path, ppm: float | None, progress=None) -> tuple[list[dict], float]:
    from . import detector as D
    c = D.config("door") or {}
    model = D.REPO / "models" / c.get("model", "detector.pt")
    g = S.gray(file)
    H, W = g.shape[:2]
    f = min(2.0, DET_PPM / ppm) if ppm else min(1.0, 4096 / max(H, W))
    cache = S.CACHE / f"{Path(file).stem}.det.{D.model_tag(model)}.{f:.3f}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8")), f
    im = S.scaled(g, f)
    h, w = im.shape[:2]
    dark = im < 170
    jobs = []
    for x0, y0 in S._tiles(w, h, DET_TILE, DET_OVERLAP):
        if dark[y0:y0 + DET_TILE, x0:x0 + DET_TILE].mean() < S.BLANK_DARK_FRAC:
            continue
        crop = im[y0:y0 + DET_TILE, x0:x0 + DET_TILE]
        pad = np.full((DET_TILE, DET_TILE), 255, np.uint8)
        pad[:crop.shape[0], :crop.shape[1]] = crop
        jobs.append((x0, y0, crop.shape[1], crop.shape[0], cv2.cvtColor(pad, cv2.COLOR_GRAY2BGR)))
    preds = []
    for i in range(0, len(jobs), 8):
        preds += D._predict_batch(str(model), [j[4] for j in jobs[i:i + 8]], DET_TILE, "cpu", chunk=4)
        if progress:
            progress("doors", min(i + 8, len(jobs)), len(jobs))
    boxes = []
    for (x0, y0, cw, ch, _), ds in zip(jobs, preds):
        for b in ds:
            x_0, y_0, x_1, y_1 = b["xyxy"]
            if x_0 >= cw or y_0 >= ch:
                continue
            boxes.append({**b, "xyxy": [round((x_0 + x0) / f, 1), round((y_0 + y0) / f, 1),
                                        round((min(x_1, cw) + x0) / f, 1), round((min(y_1, ch) + y0) / f, 1)]})
    boxes = D.tiling.merge_seams(boxes)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(boxes), encoding="utf-8")
    return boxes, f


# ---------------------------------------------------------------- rooms for other routes (baseboards, finishes)

DOOR_NEAR_M = 0.3          # a door box whose centre and both ends lie this close to a room's outline is in its wall
PERIMETER_SRC = "outline traced between the walls on the sheet image, converted with the sheet scale"
OPENING_SRC = "door detector box on the sheet; its long side spans the opening in the wall"


def on_outline(contour: np.ndarray, xyxy, near_px: float) -> bool:
    """True when a door box sits in the wall the outline runs along: its centre and the two points 40% of its
    length either side of the centre, along its long side, are all within near_px of the outline. A door in the
    wall of a neighbouring room at right angles has one end far from this outline, so it is not taken."""
    x0, y0, x1, y1 = xyxy
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if x1 - x0 >= y1 - y0:
        h = 0.4 * (x1 - x0)
        pts = [(cx, cy), (cx - h, cy), (cx + h, cy)]
    else:
        h = 0.4 * (y1 - y0)
        pts = [(cx, cy), (cx, cy - h), (cx, cy + h)]
    return all(abs(cv2.pointPolygonTest(contour, (float(x), float(y)), True)) <= near_px for x, y in pts)


def door_openings(file: Path, room: dict, conf: float | None = None, boxes: list[dict] | None = None,
                  ppm: float | None = None) -> list[dict]:
    """Door boxes (the scan detector, run on the sheet as `detect`) in the walls of the room's traced outline
    (`on_outline`): [{"xyxy", "score", "width_m"}], width_m = the box's long side in metres. On a drawing sheet the
    door box covers the opening in the wall, not the swing, and a double door is one box across both leaves.
    Empty when the room has no outline. `boxes` and `ppm` may be passed to skip the cached reads."""
    from . import detector as D
    if not room.get("contour"):
        return []
    if ppm is None:
        ppm = S.analyse(file, regions=False)["scale"]["ppm"]
    if boxes is None:
        boxes, _ = detect(file, ppm)
    t = conf if conf is not None else (D.config("door") or {"conf": 0.4})["conf"]
    c = np.array(room["contour"], np.float32).reshape(-1, 1, 2)
    near = DOOR_NEAR_M * (ppm or 100.0)
    out = []
    for b in boxes:
        if b["cls"] != "door" or b["conf"] < t or not on_outline(c, b["xyxy"], near):
            continue
        x0, y0, x1, y1 = b["xyxy"]
        out.append({"xyxy": b["xyxy"], "score": b["conf"],
                    "width_m": round(max(x1 - x0, y1 - y0) / ppm, 2) if ppm else None})
    return out


def rooms(file: Path, doors: bool = True) -> list[dict]:
    """The named rooms of a sheet, for takeoffs that need a room's outline (baseboards, finishes). Cached. Each:
      {"id", "label", "lines", "box", "area_m2", "perimeter_m", "contour" (original px), "split", "shared_with"}
    and, when the outline closed, the record shape bpqa/walls.py and bpqa/baseboard.py read:
      "perimeter" (m), "perimeter_src", "openings": [{"kind": "door", "id", "width" (m), "src", "score", "xyxy"}],
      "openings_unknown" (True when doors=False), "doors_m" (sum of door widths), "baseboard_m" (perimeter - doors_m).
    A room without a closed outline carries "why" and no perimeter. A room whose outline was divided from a
    neighbour's ("split") has the dividing line in its perimeter, though no wall stands there."""
    d = S.analyse(file, regions=True)
    ppm = d["scale"]["ppm"]
    boxes = detect(file, ppm)[0] if doors and ppm else []
    keys = ("text", "lines", "box", "area_m2", "perimeter_m", "contour", "split", "shared_with", "why")
    out = []
    for i, l in enumerate(room_labels(d)):
        r = {"id": i, **{("label" if k == "text" else k): l.get(k) for k in keys}}
        if r.get("perimeter_m") is not None:
            ops = door_openings(file, r, boxes=boxes, ppm=ppm) if doors else []
            r["perimeter"] = r["perimeter_m"]
            r["perimeter_src"] = PERIMETER_SRC
            r["openings"] = [{"kind": "door", "id": f"door-{round(o['xyxy'][0])}-{round(o['xyxy'][1])}",
                              "width": o["width_m"], "src": OPENING_SRC, "score": o["score"], "xyxy": o["xyxy"]}
                             for o in ops]
            r["openings_unknown"] = not doors
            r["doors_m"] = round(sum(o["width_m"] or 0.0 for o in ops), 2)
            r["baseboard_m"] = round(max(r["perimeter"] - r["doors_m"], 0.0), 2)
        out.append(r)
    return out


# ---------------------------------------------------------------- evidence

class Canvas:
    """An ImageCanvas on a copy of the sheet CANVAS_SIDE px long; takes original-pixel coordinates."""

    def __init__(self, file: Path):
        g = S.gray(file)
        self.k = min(1.0, CANVAS_SIDE / max(g.shape))
        self.cv = ImageCanvas(Image.fromarray(S.scaled(g, self.k)).convert("RGB"))

    def r(self, b):
        return [v * self.k for v in b]

    def p(self, pts):
        return [(x * self.k, y * self.k) for x, y in pts]


def scale_steps(cv: Canvas, sc: S.SheetScale) -> dict:
    rects, caps = [], []
    if sc.ratio and sc.ratio.get("hits"):
        for h in sc.ratio["hits"][:2]:
            rects.append(cv.r(h["box"]))
            caps.append(f"printed {h['text']}")
    if sc.sheet:
        rects.append(cv.r(sc.sheet["box"]))
        caps.append(f"sheet {sc.sheet['name']}")
    if sc.bar:
        for lb in sc.bar["labels"]:
            rects.append(cv.r(lb["box"]))
            caps.append(f"bar {lb['text']}")
        if sc.bar.get("bar"):
            b = sc.bar["bar"]
            rects.append(cv.r([b["x0"], b["y0"], b["x1"], b["y1"]]))
            caps.append(f"bar {b['x1'] - b['x0']:.0f} px")
    for r, c in zip(rects, caps):
        cv.cv.box(r, color=BLUE if sc.strong else AMBER, width=2)
        record_context(cv.cv.img, r, kind="scale_object", tone="blue" if sc.strong else "amber", label=c)
    return {"kind": "scale", "title": "Scale", "text": short_scale(sc), "note": sc.detail[:300], "rects": rects,
            "caps": caps}


def short_scale(sc: S.SheetScale) -> str:
    if not sc.ppm:
        return "no scale read"
    r = sc.ratio.get("denom") if sc.ratio else None
    return (f"1:{r} on {sc.sheet['name']}" if r and sc.sheet and "ratio" in sc.source.split(",")[0] else sc.source) \
        + f" -> {sc.ppm:.1f} px/m" + (" (scale bar agrees)" if sc.strong else "")


def _finish(ctx, cv: Canvas, head: str, note: str) -> str:
    return finish(cv.cv.img, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question,
                  f"{head}   ESTIMATE   ({ROUTE}, local OCR and vision, €0)", note=note)


# ---------------------------------------------------------------- answers

def answer(ctx, query) -> Answer:
    q = ctx.q.question
    obj = query.obj if query is not None else None
    measure = query.measure if query is not None else None
    if obj in ("door", "window") and measure in (None, "count"):
        return objects(ctx, obj)
    d = S.analyse(ctx.file, regions=False)
    labels = room_labels(d)
    tk = (query.extra.get("takeoff") if query is not None else None) or None
    if measure in ("area", "length") or AREA_Q_RE.search(q) or tk:
        d = S.analyse(ctx.file, regions=True)
        d = S.assumed(d, getattr(ctx, "assume", None))     # scale-assumption: the person's ratio and sheet, if any
        labels = room_labels(d)
        ph, hits = match_phrase(q, labels)
        if PERIM_RE.search(q):
            return measured(ctx, d, ph, hits, labels, perimeter=True)
        if not ph and TOTAL_RE.search(q):
            return total_area(ctx, d, labels)
        return measured(ctx, d, ph, hits, labels, tk=tk)
    if EXIST_RE.search(q) and not COUNT_RE.search(q):
        ph, hits = match_phrase(q, labels)
        if ph:
            return exists(ctx, d, ph, hits, labels)
    if measure == "count" or COUNT_RE.search(q):
        ph, hits = match_phrase(q, labels)
        if ph:
            return named_count(ctx, d, ph, hits, labels)
        if obj == "room" or re.search(r"\brooms?\b", q, re.I):
            return named_count(ctx, d, ["room"], labels, labels, every=True)
        if obj == "toilet":
            ph, hits = match_phrase("toilet", labels)
            if ph:
                return named_count(ctx, d, ph, hits, labels)
    names = sorted({l["text"] for l in labels})
    return abstain(ROUTE, "on this drawing sheet the route reads door and window counts, room counts and areas by "
                          "the room names printed on it, and the total floor area; this question names none of them"
                   + (f" (room names read: {', '.join(names[:25])})" if names else " (no room names read)"))


OUTER_WALL_M = 0.6        # a box whose centre lies this close to the traced building outline is in an outer wall
# checked by eye on one sheet (the Stoa sample, a UK special school plan at 1:200, 2026-11-14): of 125 door boxes
# 3 were not doors (two glazed panels, one cubicle partition) and 7 doors were missed (sliding doors, double doors
# drawn without swing arcs, double doors in the thin walls of a covered walkway); all 68 window boxes on the outer
# walls were windows with none missed there, and the other 12 window boxes were glazed panels in internal walls
EYE_CHECK = {"door": "on the one drawing sheet checked by eye, 122 of 125 door boxes were doors and 7 doors were "
                     "missed (sliding doors, double doors drawn without swing arcs)",
             "window": "on the one drawing sheet checked by eye, every window box on the outer walls was a window and "
                       "none there was missed; boxes in internal walls were glazed panels, not windows"}


def outer_wall(d: dict, xyxy) -> bool | None:
    """True when the box centre is within OUTER_WALL_M of the traced building outline; None when no outline."""
    ppm, polys = d["scale"]["ppm"], d.get("footprint") or []
    if not ppm or not polys:
        return None
    x0, y0, x1, y1 = xyxy
    c = (float((x0 + x1) / 2), float((y0 + y1) / 2))
    return min(abs(cv2.pointPolygonTest(np.array(p, np.float32).reshape(-1, 1, 2), c, True)) for p in polys) \
        <= OUTER_WALL_M * ppm


def objects(ctx, obj: str) -> Answer:
    """Doors: every door box (internal and external doors). Windows: the window boxes in the outer walls of the
    traced building outline; the detector also boxes glazed panels in internal walls (vision panels, glazed
    screens), which a window count leaves out and the answer lists as uncertain. Without an outline every box counts."""
    from . import detector as D
    d = S.analyse(ctx.file, regions=(obj == "window"))
    sc = S.scale_of(d)
    boxes, f = detect(ctx.file, sc.ppm)
    c = D.config(obj) or {"conf": 0.5}
    t = c["conf"]
    hits = sorted((b for b in boxes if b["cls"] == obj and b["conf"] >= t), key=lambda b: -b["conf"])
    near = [b for b in boxes if b["cls"] == obj and t - 0.1 <= b["conf"] < t + 0.1]
    inner = []
    if obj == "window":
        side = [(b, outer_wall(d, b["xyxy"])) for b in hits]
        if any(o for _, o in side):
            inner = [b for b, o in side if o is False]
            hits = [b for b, o in side if o is not False]
    n = len(hits)
    det_ppm = sc.ppm * f if sc.ppm else None
    why = (f"the detector was trained on CubiCasa apartment scans and has no measured precision on drawing sheets in "
           f"general; {EYE_CHECK[obj]}; {len(near)} {obj} box(es) scored within 0.10 of the threshold")
    if obj == "door":
        n_out = sum(1 for b in hits if outer_wall(d, b["xyxy"]))
        split = (f" ({n_out} in the outer walls, {n - n_out} inside)" if d.get("footprint") else "")
        what = f"{n} door box(es){split}, each double door one box,"
    else:
        what = (f"{n} window box(es) in the outer walls" + (f" ({len(inner)} more boxes in internal walls are glazed "
                f"panels or screens, not counted)" if inner else "") + ",")
    ans = Answer(value=n, unit="count", route=ROUTE, confidence=0.5, tier="estimate", tier_reason=why,
                 items=[{"n": i + 1, "class": b["cls"], "score": b["conf"], "xyxy": b["xyxy"]} for i, b in enumerate(hits)],
                 uncertain=([{"class": b["cls"], "score": b["conf"], "xyxy": b["xyxy"], "counted": b["conf"] >= t}
                             for b in near if b not in inner]
                            + [{"class": b["cls"], "score": b["conf"], "xyxy": b["xyxy"], "counted": False,
                                "why": "in an internal wall: a glazed panel, not a window"} for b in inner]),
                 explanation=(f"{what} scored >= {t:.2f} by the scan detector, run on tiles of the sheet scaled "
                              f"by {f:.2f}" + (f" to {det_ppm:.0f} px per m (scale: {short_scale(sc)})" if det_ppm else
                                               " (no scale read, so the scale is a guess)")
                              + "; one view, no rotations"
                              + (f"; {D.DOOR_CONVENTION}" if obj == "door" else "")))
    if ctx.evidence:
        cv = Canvas(ctx.file)
        for i, b in enumerate(hits):
            cv.cv.box(cv.r(b["xyxy"]), n=i + 1, color=AMBER, width=2)
        for b in near + inner:
            if b["conf"] < t or b in inner:
                cv.cv.box(cv.r(b["xyxy"]), color=GREY, width=1)
        ans.evidence = _finish(ctx, cv, f"Answer: {n}", f"amber numbered boxes: each counted {obj}; grey: scored just below "
                                                       f"the threshold" + (" or a glazed panel in an internal wall" if inner
                                                                           else "") + f". Estimate because: {why}")
    return ans


def _label_list(hits: list[dict], k: int = 12) -> str:
    from collections import Counter
    c = Counter(h["text"] for h in hits)
    parts = [f"{t} x{n}" if n > 1 else t for t, n in c.most_common(k)]
    if len(c) > k:
        parts.append(f"{len(c) - k} more names")
    return ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else (parts[0] if parts else "none")


def named_count(ctx, d: dict, ph: list[str], hits: list[dict], labels: list[dict], every: bool = False) -> Answer:
    n = len(hits)
    name = " ".join(ph)
    noun = "rooms" if every else (name if n == 1 else name + "s")
    expl = (f"{n} {noun}: {_label_list(hits)}, names read on the sheet"
            + ("" if every else f" (every name containing '{name}')")
            + "; a name printed over two or three lines is joined first")
    why = ("room names read by OCR on a drawing sheet; no measured precision for this drawing style, and a room "
           "printed without a name, or a name the OCR missed, is not counted")
    plural = sorted({h["text"] for h in hits if label_words(h["text"]) and label_words(h["text"])[-1] != re.findall(r"[a-z]+", re.sub(r"\(.*?\)", " ", h["text"]).lower())[-1]})
    if plural:
        why += f"; a name in the plural ({', '.join(plural[:3])}) is counted as one room, which may hold several"
    if every:
        why += "; corridors, stairs, lifts and lobbies are counted as rooms when the sheet names them"
    ans = Answer(value=n, unit="count", route=ROUTE, confidence=0.6, tier="estimate", tier_reason=why,
                 items=[{"n": i + 1, "label": h["text"], "ocr_conf": h["conf"], "xyxy": [round(v) for v in h["box"]]}
                        for i, h in enumerate(hits)],
                 explanation=expl)
    if ctx.evidence:
        cv = Canvas(ctx.file)
        hit_ids = {id(h) for h in hits}
        for l in labels:
            if id(l) not in hit_ids:
                cv.cv.box(cv.r(l["box"]), color=GREY, width=1)
        for i, h in enumerate(hits):
            cv.cv.box(cv.r(h["box"]), n=i + 1, color=AMBER, width=2)
            record_steps(cv.cv.img, i + 1, [{"kind": "label", "title": "Label read", "text": h["text"],
                                             "note": " / ".join(h["lines"]) if len(h["lines"]) > 1 else "one line",
                                             "rect": cv.r(h["box"])}])
        ans.evidence = _finish(ctx, cv, f"Answer: {n}", f"amber numbered boxes: room names matching '{name}'; grey: other "
                                                       f"room names read. Estimate because: {why}")
    return ans


def exists(ctx, d: dict, ph: list[str], hits: list[dict], labels: list[dict]) -> Answer:
    """"Is there a lift?": yes, with the names read. A name not read is not taken as a no (the OCR may have missed
    it, or the room may be drawn unnamed), so a question naming no room read on the sheet abstains instead."""
    a = named_count(ctx, d, ph, hits, labels)
    a.value, a.unit = "yes", ""
    a.explanation = (f"yes: {len(hits)} room name(s) containing '{' '.join(ph)}' read on the sheet "
                     f"({_label_list(hits)})")
    return a


def _area_why(sc: S.SheetScale) -> str:
    return ("room outline traced from the walls on the image, not printed on the drawing"
            + ("; the scale is well supported (printed ratio and scale bar agree)" if sc.strong else
               "; the scale rests on one source only" if sc.ppm else ""))


def _tiling(ctx, d: dict, sc, tk: dict, ok: list[dict]) -> dict:
    """A takeoff (tiles, flooring) on the rooms measured on the sheet, read the way every other route reads it
    (bpqa/walls.py surfaces): "floor" floor only, "walls" walls only, both when both are named. Plain "tiling for the
    toilets" keeps this route's floor area as the value, and the text states floor only and floor plus walls, each
    with the waste. A waste said in the question is always applied; otherwise waste is added only to an explicit
    surface asked to order (the rule walls.summary uses). Walls: traced outline length x ceiling height (given, or
    assumed) minus the door boxes in the room's walls at 2.0 m high."""
    from .. import walls as W
    _, want_walls, how = W.surfaces(tk)
    inp = W.parse_inputs(ctx.q.question, tk)
    stated = inp.get("waste_pct") is not None
    waste = float(inp["waste_pct"]) if stated else W.DEFAULT_WASTE_PCT
    k = 1 + waste / 100.0
    asked = tk.get("surface") or "floor"
    plain = asked == "floor" and not tk.get("floor_explicit")
    floor = sum(h["area_m2"] for h in ok)
    walls_t, rows, hsrc = None, [], ""
    if all(h.get("perimeter_m") for h in ok):
        boxes, _ = detect(ctx.file, S.read_scale(d).ppm)
        for h in ok:
            ops = door_openings(ctx.file, h, boxes=boxes, ppm=sc.ppm)
            w = W.room_walls({"label": h["text"], "perimeter": h["perimeter_m"],
                              "openings": [{"kind": "door", "width": o["width_m"] or 0.0, "z0": 0.0, "z1": W.DOOR_H_M}
                                           for o in ops]}, inp)
            rows.append((h, w, len(ops)))
            hsrc = w["height_src"]
        walls_t = sum(w["net"] for _, w, _ in rows)
    if asked in ("walls", "floor+walls") and walls_t is None:
        return {"abstain": "wall area needs each room's traced outline length, and "
                           + ", ".join(h["text"] for h in ok if not h.get("perimeter_m")) + " has none"}
    base, what = ((walls_t, "walls only") if asked == "walls" else
                  (floor + walls_t, "floor plus walls") if asked == "floor+walls" else (floor, "floor only"))
    add = stated or (not plain and bool(inp.get("order")))
    value = round(base * (k if add else 1.0), 1)
    src = "given in the question" if stated else f"default {W.DEFAULT_WASTE_PCT:g}% cutting allowance"
    read = how if not plain else ("floor only: this route answers plain tiling with the floor area" if want_walls else how)
    head = (f"about {value:g} m2 = {what} {base:.1f} m2" + (f" + {waste:g}% waste ({src})" if add else ", no waste added")
            + f". Surfaces: {read}.")
    both = f" Floor only: {floor:.1f} m2, with {waste:g}% waste {floor * k:.1f} m2."
    if walls_t is not None:
        both += (f" Floor plus walls: {floor + walls_t:.1f} m2, with {waste:g}% waste {(floor + walls_t) * k:.1f} m2 (walls "
                 f"{walls_t:.1f} m2 = outline length x {rows[0][1]['band']:.2f} m minus door openings: "
                 + ", ".join(f"{h['text']} {h['perimeter_m']:.1f} m, {n} door(s)" for h, _, n in rows[:8])
                 + (", ..." if len(rows) > 8 else "") + (f"; {hsrc}" if "ASSUMED" in hsrc else "") + ").")
    why = f"; walls use an {hsrc.split(' (')[0]}" if asked in ("walls", "floor+walls") and "ASSUMED" in hsrc else ""
    return {"value": value, "text": head + both, "why": why}


def measured(ctx, d: dict, ph: list[str], hits: list[dict], labels: list[dict], perimeter: bool = False,
             tk: dict | None = None) -> Answer:
    sc = S.scale_of(d)
    if not sc.ppm:
        return abstain(ROUTE, f"no scale on the sheet: {sc.detail}")
    if not ph:
        names = sorted({l["text"] for l in labels})
        return abstain(ROUTE, "the question names no room printed on the sheet (room names read: "
                       + ", ".join(names[:30]) + ")")
    name = " ".join(ph)
    key = "perimeter_m" if perimeter else "area_m2"
    unit = "m" if perimeter else "m2"
    ok = [h for h in hits if h.get(key) is not None]
    bad = [h for h in hits if h.get(key) is None]
    if not ok:
        return abstain(ROUTE, f"'{name}' read {len(hits)} time(s) but no closed outline traced: "
                       + "; ".join(f"{h['text']}: {h.get('why', 'no region')}" for h in hits))
    if bad:
        return abstain(ROUTE, f"{len(bad)} of {len(hits)} rooms named '{name}' have no closed outline "
                       f"({'; '.join(h['text'] + ': ' + h.get('why', 'no region') for h in bad[:4])}), and a partial sum "
                       f"would be a wrong quantity; measured: "
                       + ", ".join(f"{h['text']} {h[key]:.1f} {unit}" for h in ok))
    split = [h for h in ok if h.get("split")]
    why = _area_why(sc) + (f"; {len(split)} outline(s) held several room names and were divided between them at the "
                           f"narrowest passage ({', '.join(h['text'] for h in split[:4])})" if split else "")
    baseboard = perimeter and bool(re.search(r"\b(skirting|baseboard|base board)\b", ctx.q.question, re.I))
    doors_m = {}
    if baseboard:
        # a baseboard stops at a door: the door boxes in each room's walls are deducted (bpqa/baseboard.py's rule)
        boxes, _ = detect(ctx.file, S.read_scale(d).ppm)     # scale-assumption: the detector's cached resolution
        for h in ok:
            doors_m[id(h)] = [o["width_m"] or 0.0 for o in door_openings(ctx.file, h, boxes=boxes, ppm=sc.ppm)]
        why += ("; door openings deducted are door detector boxes in the room's walls (the detector, checked by eye "
                "on one sheet, misses sliding doors and double doors drawn without swing arcs)")
    elif perimeter:
        why += "; the traced outline runs straight across door openings, nothing is deducted for them"
    val = {id(h): (h[key] - sum(doors_m.get(id(h), []))) for h in ok}
    total = round(sum(val.values()), 1)
    if baseboard:
        per = ", ".join(f"{h['text']} {h[key]:.1f} - {len(doors_m[id(h)])} door(s) {sum(doors_m[id(h)]):.1f}"
                        for h in ok[:12])
        what = "baseboard length (outline length minus door openings)"
    else:
        per = ", ".join(f"{h['text']} {h[key]:.1f}" for h in ok[:12])
        what = "outline length" if perimeter else "area"
    expl = (f"about {total:g} {unit}: {what} of {len(ok)} room(s) "
            f"named '{name}' ({per}{', ...' if len(ok) > 12 else ''}), traced between the walls and converted with "
            f"the sheet scale: {short_scale(sc)}. Scale evidence: {sc.detail}")
    tko = _tiling(ctx, d, sc, tk, ok) if tk and not perimeter else None
    if tko and tko.get("abstain"):
        return abstain(ROUTE, tko["abstain"])
    if tko:
        total = tko["value"]
        expl = tko["text"] + " Floor area " + expl[len("about "):]
        why += tko["why"]
    ans = Answer(value=total, unit=unit, route=ROUTE, confidence=0.55 if sc.strong else 0.4, tier="estimate",
                 tier_reason=why, explanation=expl,
                 items=[{"n": i + 1, "label": h["text"], "area_m2": h.get("area_m2"), "perimeter_m": h.get("perimeter_m"),
                         **({"doors_m": round(sum(doors_m[id(h)]), 2), "doors": len(doors_m[id(h)]),
                             "baseboard_m": round(val[id(h)], 2)} if baseboard else {}),
                         "xyxy": [round(v) for v in h["box"]], "split": bool(h.get("split"))} for i, h in enumerate(ok)],
                 approx={"sentence": expl.split(". Scale evidence")[0], "source": "sheet_scale", "scale": sc.source,
                         "px_per_m": round(sc.ppm, 2), "strong": sc.strong, "detail": sc.detail,
                         "rooms": [{"label": h["text"], key: h[key]} for h in ok],
                         "assume": S.assume_payload(d)})      # scale-assumption
    if ctx.evidence:
        cv = Canvas(ctx.file)
        st = scale_steps(cv, sc)
        for i, h in enumerate(ok):
            pts = cv.p(h["contour"])
            cv.cv.d.polygon(pts, fill=(230, 160, 30, 70), outline=AMBER, width=2)
            cv.cv.box(cv.r(h["box"]), n=i + 1, color=AMBER, width=2)
            record_steps(cv.cv.img, i + 1, [
                {"kind": "label", "title": "Room found", "text": h["text"],
                 "note": " / ".join(h["lines"]) if len(h["lines"]) > 1 else f"name contains '{name}'", "rect": cv.r(h["box"])},
                st,
                {"kind": "region", "title": "Room outline", "text": f"{h[key]:.1f} {unit}",
                 "note": ("divided from " + ", ".join(h["shared_with"]) if h.get("split") else "traced between the walls"),
                 "cap": f"{h[key]:.1f} {unit}", "pts": pts}])
        ans.evidence = _finish(ctx, cv, f"Answer: about {total:g} {unit}",
                               f"amber outlines: the traced rooms named '{name}'; boxed in blue/amber: the scale evidence "
                               f"({short_scale(sc)}). Estimate because: {why}")
    return ans


def total_area(ctx, d: dict, labels: list[dict]) -> Answer:
    sc = S.scale_of(d)
    if not sc.ppm:
        return abstain(ROUTE, f"no scale on the sheet: {sc.detail}")
    gross = d.get("gross_m2")
    seen, net, n = set(), 0.0, 0
    for l in labels:
        if l.get("area_m2") is None:
            continue
        k = (round(l["area_m2"], 2), tuple(l["contour"][0]))
        if k in seen:
            continue
        seen.add(k)
        net += l["area_m2"]
        n += 1
    missing = [l["text"] for l in labels if l.get("area_m2") is None]
    if gross is None:
        return abstain(ROUTE, "no closed building outline traced on the sheet")
    why = (_area_why(sc) + "; gross footprint traced from the outer walls (walls included, open canopies and "
           "walkways that are not enclosed are left out)")
    expl = (f"about {gross:g} m2 gross floor area: the building footprint traced from its outer walls, walls included, "
            f"converted with the sheet scale ({short_scale(sc)}). For comparison the {n} named rooms with a closed outline "
            f"add up to {net:.0f} m2 net (corridors included where named"
            + (f"; {len(missing)} names had no closed outline: {', '.join(missing[:6])}" if missing else "")
            + f"). Scale evidence: {sc.detail}")
    ans = Answer(value=gross, unit="m2", route=ROUTE, confidence=0.45 if sc.strong else 0.35, tier="estimate",
                 tier_reason=why, explanation=expl,
                 items=[{"gross_m2": gross, "net_named_rooms_m2": round(net, 1), "rooms_measured": n}],
                 approx={"sentence": expl.split(". Scale evidence")[0], "source": "sheet_scale", "scale": sc.source,
                         "px_per_m": round(sc.ppm, 2), "strong": sc.strong, "detail": sc.detail,
                         "assume": S.assume_payload(d)})      # scale-assumption
    if ctx.evidence:
        cv = Canvas(ctx.file)
        st = scale_steps(cv, sc)
        for poly in d.get("footprint") or []:
            cv.cv.d.polygon(cv.p(poly), fill=(230, 160, 30, 50), outline=AMBER, width=2)
        record_steps(cv.cv.img, 1, [st, {"kind": "region", "title": "Building footprint", "text": f"{gross:g} m2",
                                         "note": "outer walls traced", "rect": [0, 0, 1, 1]}])
        ans.evidence = _finish(ctx, cv, f"Answer: about {gross:g} m2 gross",
                               f"amber: the traced building footprint. Estimate because: {why}")
    return ans
