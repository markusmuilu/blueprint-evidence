"""Room areas on raster plans (CubiCasa scans): printed areas, a pixel scale, measured regions.

Three signals, each drawn on the evidence image:

  printed  the plan's own area labels ("12,1 m2", comma or point decimals) read by the local OCR,
           each attached to the room label printed just above or beside it ("MH1" over "15,3 m2").
  region   the white region enclosed by walls around a label: text is erased, dark linework is the
           barrier (thickened by a few pixels so hairline gaps in walls close), the region is flood
           filled from the label and grown back by the same few pixels; furniture inside a room is
           part of the room (holes are filled). A region is unusable when it reaches the image edge,
           holds another room's label or area (an open doorway merged two rooms), or is tiny.
  scale    square metres per pixel, from rooms that have both a printed area and a usable region:
           s_i = printed_i / pixels_i. A scale is set only when 2+ rooms agree within SCALE_TOL of
           their median; otherwise no scale and measured areas abstain.

Area of a room for a takeoff: the printed value when the plan prints one (it is the designer's
number), else region pixels x scale. If any asked room has neither, the sum abstains, because a
partial sum would be a wrong order quantity.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from .labels import ROOM_PATTERNS, is_word, parts, quad_box, PLAN_WORDS
from .ocr import _run, cache_path

SCALE_TOL = 0.04          # rooms must agree within 4% of the median scale
MIN_SCALE_ROOMS = 2
DARK = 170                # grey level below which a pixel is linework
MIN_REGION_FRAC = 0.0008  # of the image area; smaller regions are a glyph or a symbol
MAX_REGION_FRAC = 0.35

AREA_RE = re.compile(r"(?<![\d+\-±])(\d{1,3})\s?[,.]\s?(\d{1,2})\s?\.?\s?(m|M)(?:\s?[2²?*'!azZ]|(?=\s|$))(?![\dxX])")
TOTAL_RE = re.compile(r"KERROSALA|HUONEISTOALA|ASUINALA|ALA\s*YHT|YHTEENSA|HTM|KEM|TOTAL|PINTA-?ALA", re.I)


@dataclass
class Room:
    label: str | None           # room label text ("MH1"), None when an area stands alone
    label_box: tuple | None
    printed: float | None       # m2 printed on the plan
    area_box: tuple | None
    seed: tuple                  # (x, y) pixel to flood fill from
    pixels: int = 0              # region size, 0 when unusable
    region_ok: bool = False
    why: str = ""                # why the region is unusable
    mask: object = None
    contour: object = None
    measured: float | None = None
    extra: dict = field(default_factory=dict)


def tokens_r0(f: Path, allow_run: bool = True) -> list[dict] | None:
    p = cache_path(f, 0, False)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    if not allow_run:
        return None
    img = cv2.imread(str(f))
    if img is None:
        return None
    t = _run(img, 0, False)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    return t


def parse_area(text: str):
    """'15,3 m2' -> (15.3, rest-of-token). None for levels (+22,65), totals and dimensions."""
    if TOTAL_RE.search(text):
        return None
    m = AREA_RE.search(text)
    if not m:
        return None
    v = float(f"{m.group(1)}.{m.group(2)}")
    if not 0.8 <= v <= 300:
        return None
    rest = (text[:m.start()] + " " + text[m.end():]).strip(" .:-")
    return v, rest


def room_word(text: str) -> bool:
    """A room label token: a room abbreviation or a common plan word (MH1, KH, OH, ET, VAR ...)."""
    if not text or len(text) > 24:
        return False
    return is_word(text)


def _center(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def read_rooms(toks: list[dict], min_conf: float = 0.5) -> tuple[list[Room], list[dict]]:
    """Printed areas attached to room labels, and room labels without a printed area."""
    toks = [t for t in toks if t["conf"] >= min_conf and not t.get("mirror")]
    areas, labels = [], []
    for t in toks:
        b = quad_box(t["quad"])
        pa = parse_area(t["text"])
        if pa is not None:
            v, rest = pa
            areas.append({"v": v, "box": b, "rest": rest if room_word(rest) else None, "text": t["text"]})
        elif room_word(t["text"]):
            labels.append({"text": t["text"], "box": b})
    used = set()
    rooms = []
    for a in areas:
        if a["rest"]:                       # "MH1 15.3m2" in one token
            rooms.append(Room(a["rest"], a["box"], a["v"], a["box"], _center(a["box"])))
            continue
        b = a["box"]
        h = max(b[3] - b[1], 6)
        best, bd = None, None
        for i, l in enumerate(labels):
            lb = l["box"]
            dx = abs(_center(lb)[0] - _center(b)[0])
            dy = _center(b)[1] - _center(lb)[1]          # label above the area: dy > 0
            if 0 < dy < 2.6 * h and dx < max(b[2] - b[0], lb[2] - lb[0]):
                d = dy + dx
            elif abs(dy) < 0.6 * h and 0 < b[0] - lb[2] < 3 * h:      # label left of the area
                d = 2 * h + (b[0] - lb[2])
            else:
                continue
            if bd is None or d < bd:
                best, bd = i, d
        if best is not None and best not in used:
            used.add(best)
            lb = labels[best]["box"]
            seed = ((lb[0] + lb[2]) / 2, (lb[3] + b[1]) / 2)
            rooms.append(Room(labels[best]["text"], lb, a["v"], b, seed))
        else:
            rooms.append(Room(None, None, a["v"], b, _center(b)))
    free = [l for i, l in enumerate(labels) if i not in used]
    return rooms, free


# ---------------------------------------------------------------- regions

def barrier(img_bgr, toks, k: int):
    """Linework mask with all text erased, thickened by k px so hairline gaps close."""
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    dark = (g < DARK).astype(np.uint8)
    for t in toks:
        x0, y0, x1, y1 = (int(round(v)) for v in quad_box(t["quad"]))
        dark[max(0, y0 - 1):y1 + 2, max(0, x0 - 1):x1 + 2] = 0
    if k > 0:
        dark = cv2.dilate(dark, np.ones((2 * k + 1, 2 * k + 1), np.uint8))
    return dark


CLOSE_K = 5      # walls drawn as two lines or hatched: close gaps up to this many px first
OPEN_K = 4       # then keep only strokes at least this thick: walls stay, furniture and arcs go
DOOR_M = 1.0     # wall gaps up to this many metres are doorways and get bridged
FURN_M = 0.7     # fixed furniture along a wall (counters, wardrobes) up to this deep is room floor
THICK_ADD = 3    # walls are strokes OPEN_K + THICK_ADD px thick; thinner strokes are furniture fronts
REFINE = True


def refine(m, bar, thick, ppm):
    """Give a room back the floor under fixed furniture along its walls. The flood-filled region
    stops at a counter or wardrobe front; closing the region together with the linework next to it
    (by FURN_M) fills the strip behind that front, and only real walls (thick strokes) are taken
    away again. Returns the new region mask (same shape)."""
    K = max(3, int(round(FURN_M * ppm)))
    ys, xs = np.nonzero(m)
    y0, y1 = max(0, ys.min() - K - 2), min(m.shape[0], ys.max() + K + 3)
    x0, x1 = max(0, xs.min() - K - 2), min(m.shape[1], xs.max() + K + 3)
    mc, bc, tc = m[y0:y1, x0:x1], bar[y0:y1, x0:x1], thick[y0:y1, x0:x1]
    near = bc & cv2.dilate(mc, np.ones((2 * K + 1, 2 * K + 1), np.uint8))
    rc = cv2.morphologyEx(mc | near, cv2.MORPH_CLOSE, np.ones((K, K), np.uint8))
    rc = rc & (1 - tc)
    n, lab = cv2.connectedComponents(rc.astype(np.uint8), connectivity=4)
    ids = np.unique(lab[mc > 0])
    ids = ids[ids > 0]
    out = np.zeros_like(m)
    if len(ids):
        out[y0:y1, x0:x1] = np.isin(lab, ids).astype(np.uint8)
    out |= m
    return out


def wall_barrier(img_bgr, toks, door_px: int):
    """Walls only: thin linework (furniture, door swings, stairs, dimension lines) is removed so it
    cannot cut a room into pieces, and wall gaps up to `door_px` (doorways) are bridged along the
    wall direction. `door_px` is the doorway width in pixels for an assumed scale; analyse() tries
    several and keeps the one that agrees with the scale it produces."""
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    dark = (g < DARK).astype(np.uint8)
    for t in toks:
        x0, y0, x1, y1 = (int(round(v)) for v in quad_box(t["quad"]))
        dark[max(0, y0 - 1):y1 + 2, max(0, x0 - 1):x1 + 2] = 0
    d = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((CLOSE_K, CLOSE_K), np.uint8))
    walls = cv2.morphologyEx(d, cv2.MORPH_OPEN, np.ones((OPEN_K, OPEN_K), np.uint8))
    if door_px > 0:
        h = cv2.morphologyEx(walls, cv2.MORPH_CLOSE, np.ones((1, door_px), np.uint8))
        v = cv2.morphologyEx(walls, cv2.MORPH_CLOSE, np.ones((door_px, 1), np.uint8))
        walls = walls | h | v
    return walls


def _seed_pixel(free, x, y, r=12):
    h, w = free.shape
    x, y = int(round(x)), int(round(y))
    if 0 <= x < w and 0 <= y < h and free[y, x]:
        return x, y
    for rr in range(1, r + 1):
        for dx, dy in ((rr, 0), (-rr, 0), (0, rr), (0, -rr), (rr, rr), (-rr, -rr), (rr, -rr), (-rr, rr)):
            xx, yy = x + dx, y + dy
            if 0 <= xx < w and 0 <= yy < h and free[yy, xx]:
                return xx, yy
    return None


def measure_regions(img_bgr, rooms: list[Room], others: list[tuple], toks, k: int | None = None,
                    door_px: int | None = None, bar=None):
    """Flood-fill each room's region; mark unusable ones with the reason. `others` are the centres
    of every other label and area on the plan (a region holding one of them merged two rooms).
    door_px None: every dark line is a barrier (thickened by k); else walls only (wall_barrier)."""
    h, w = img_bgr.shape[:2]
    if k is None:
        k = max(1, round(min(h, w) / 450))
    if bar is None:
        bar = barrier(img_bgr, toks, k) if door_px is None else wall_barrier(img_bgr, toks, door_px)
    if door_px is not None:
        k = 0
    free = (bar == 0).astype(np.uint8)
    n, lab = cv2.connectedComponents(free, connectivity=4)
    thick = None
    if door_px is not None and REFINE:
        tk_ = OPEN_K + THICK_ADD
        thick = cv2.morphologyEx(bar, cv2.MORPH_OPEN, np.ones((tk_, tk_), np.uint8))
    total = h * w
    for r in rooms:
        sp = _seed_pixel(free, *r.seed)
        if sp is None:
            r.why = "label sits on linework"
            continue
        cid = lab[sp[1], sp[0]]
        m = (lab == cid).astype(np.uint8)
        ys, xs = np.nonzero(m)
        if xs.min() == 0 or ys.min() == 0 or xs.max() == w - 1 or ys.max() == h - 1:
            r.why = "region reaches the image edge (room not closed)"
            continue
        if k > 0:
            m = cv2.dilate(m, np.ones((2 * k + 1, 2 * k + 1), np.uint8))   # give back the thickening
        if thick is not None:
            m = refine(m, bar, thick, door_px / DOOR_M)
        cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        c = max(cs, key=cv2.contourArea)
        filled = np.zeros_like(m)
        cv2.drawContours(filled, [c], -1, 1, thickness=-1)                  # furniture inside counts
        px = int(filled.sum())
        if px < MIN_REGION_FRAC * total:
            r.why = "region too small"
            continue
        if px > MAX_REGION_FRAC * total:
            r.why = "region too large (leaks)"
            continue
        own = {r.label_box, r.area_box}
        inside = [o for o, b in others if b not in own and 0 <= int(o[1]) < h and 0 <= int(o[0]) < w
                  and filled[int(o[1]), int(o[0])]]
        if inside:
            r.why = f"region also holds {len(inside)} other label(s) (open doorway or merged rooms)"
            r.extra["merged"] = len(inside)
            continue
        r.pixels, r.region_ok, r.contour = px, True, c
    return rooms


def rectangularity(r) -> float:
    """Region pixels / area of its minimum rotated rectangle: 1.0 for a clean rectangular room."""
    if r.contour is None or not r.pixels:
        return 0.0
    (_, _), (w, h), _ = cv2.minAreaRect(r.contour)
    return float(r.pixels / max(w * h, 1.0))


# ---------------------------------------------------------------- scale

@dataclass
class Scale:
    m2_per_px: float | None
    rooms: list            # indices of the rooms that agreed
    spread: float          # max relative deviation from the median among the agreeing rooms
    method: str
    detail: str

    @property
    def known(self):
        return self.m2_per_px is not None


def estimate_scale(rooms: list[Room], exclude: int | None = None, tol: float | None = None) -> Scale:
    tol = SCALE_TOL if tol is None else tol
    cand = [(i, r.printed / r.pixels) for i, r in enumerate(rooms)
            if i != exclude and r.region_ok and r.printed is not None and r.pixels > 0]
    if len(cand) < MIN_SCALE_ROOMS:
        return Scale(None, [], 0, "none", f"{len(cand)} room(s) with both a printed area and a closed region")
    best = []
    for _, s0 in cand:                  # largest group within tol of one of the candidates
        grp = [(i, s) for i, s in cand if abs(s - s0) <= tol * s0]
        if len(grp) > len(best):
            best = grp
    if len(best) < MIN_SCALE_ROOMS:
        return Scale(None, [], 0, "none", f"{len(cand)} rooms with printed area and region, no 2 agree within {tol:.0%}")
    med = float(np.median([s for _, s in best]))
    best = [(i, s) for i, s in best if abs(s - med) <= tol * med]
    if len(best) < MIN_SCALE_ROOMS:
        return Scale(None, [], 0, "none", "no stable group")
    spread = max(abs(s - med) / med for _, s in best)
    # the agreeing group must be the clear majority of the rooms that could vote
    if len(best) < 0.5 * len(cand):
        return Scale(None, [], spread, "none", f"only {len(best)} of {len(cand)} rooms agree on a scale")
    return Scale(med, [i for i, _ in best], spread, "printed-areas",
                 f"{len(best)} of {len(cand)} rooms agree within {spread:.1%}; "
                 f"{1 / np.sqrt(med):.1f} px per m")


# ---------------------------------------------------------------- one plan

PPM_GRID = (24, 28, 32, 37, 43, 50, 58, 67, 77, 90)   # pixels per metre hypotheses for the doorway width
PPM_AGREE = 0.2


def plan_rooms(toks):
    rooms, free = read_rooms(toks)
    rooms_all = rooms + [Room(l["text"], l["box"], None, None, _center(l["box"])) for l in free]
    others = []
    for r in rooms_all:
        b = r.label_box or r.area_box
        others.append((_center(b), b))
        if r.area_box is not None and r.label_box is not None:
            others.append((_center(r.area_box), r.area_box))
    return rooms_all, free, others


def hypotheses(img, toks, proto, others, mode="auto"):
    """Region sets: one with every line as a barrier, and one per doorway-width hypothesis with
    walls only. Returns [(name, ppm or None, rooms)]."""
    import copy
    out = []
    if mode in ("auto", "lines"):
        rs = copy.deepcopy(proto)
        measure_regions(img, rs, others, toks)
        out.append(("lines", None, rs))
    if mode in ("auto", "walls"):
        for ppm in PPM_GRID:
            rs = copy.deepcopy(proto)
            measure_regions(img, rs, others, toks, door_px=max(3, round(DOOR_M * ppm)))
            out.append((f"walls@{ppm}", ppm, rs))
    return out


def choose(hyps, exclude: int | None = None):
    """The region set whose scale has the most agreeing rooms. A walls hypothesis counts only when
    the scale it produces matches the pixels-per-metre it assumed for doorways (within 20%)."""
    best = None
    for name, ppm, rs in hyps:
        sc = estimate_scale(rs, exclude=exclude)
        if not sc.known:
            continue
        if ppm is not None and abs((1 / np.sqrt(sc.m2_per_px)) / ppm - 1) > PPM_AGREE:
            continue
        key = (len(sc.rooms), -sc.spread)
        if best is None or key > best[0]:
            best = (key, name, rs, sc)
    if best is None:
        return None, None, Scale(None, [], 0, "none", "no region set gives 2 rooms that agree on a scale")
    _, name, rs, sc = best
    sc.method = f"printed-areas ({name})"
    return name, rs, sc


@dataclass
class PlanAreas:
    rooms: list
    free_labels: list
    scale: Scale
    regions: str


@lru_cache(maxsize=4)
def analyse(path: str, allow_run: bool = True) -> PlanAreas | None:
    f = Path(path)
    toks = tokens_r0(f, allow_run=allow_run)
    img = cv2.imread(str(f))
    if toks is None or img is None:
        return None
    toks = [t for t in toks if t["conf"] >= 0.5]
    proto, free, others = plan_rooms(toks)
    hyps = hypotheses(img, toks, proto, others, mode="walls")   # walls beat lines and auto on dev (CHANGELOG)
    name, rooms, sc = choose(hyps)
    if rooms is None:
        rooms = hyps[0][2]
    if sc.known:
        for r in rooms:
            if r.region_ok:
                r.measured = round(r.pixels * sc.m2_per_px, 2)
    return PlanAreas(rooms, free, sc, name or "none")


# ---------------------------------------------------------------- takeoff answer

ROUTE = "raster-area"
# Measured areas (region x scale) are not used as answers: on dev, leave-one-out against printed
# areas puts only 37% of measured rooms within 5% (18.7% within 2%, Stoa's tolerance). They are
# reported as an estimate in the abstention instead. Set True to answer with them anyway.
MEASURED_ANSWERS = False


def _inside(contour, pt) -> bool:
    return contour is not None and cv2.pointPolygonTest(contour, (float(pt[0]), float(pt[1])), False) >= 0


def takeoff_answer(ctx, q, img):
    from .. import takeoff
    from ..answer import Answer, abstain
    from ..evidence import BLUE, PALETTE, ImageCanvas, evidence_path, finish
    from PIL import Image

    tk = q.extra["takeoff"]
    pa = analyse(str(ctx.file))
    if pa is None:
        return abstain(ROUTE, "image or OCR unavailable")
    want = set(tk["types"])
    # one-letter labels (S, K) only when printed upper case: OCR noise reads stray strokes as "s"
    typed = [r for r in pa.rooms if r.label and takeoff.room_types(r.label) & want
             and not (len(r.label.strip(" .")) == 1 and not r.label.strip(" .").isupper())]
    if not typed:
        read = sorted({r.label for r in pa.rooms if r.label})
        return abstain(ROUTE, f"no room label of type {', '.join(tk['types'])} read upright on the plan "
                              f"(labels read: {', '.join(read[:15]) or 'none'})")
    # labels read in other orientations (full OCR cache) that the upright pass missed would be
    # rooms left out of the sum: abstain rather than under-order
    from .labels import find_rooms
    from .ocr import ocr_image
    full = ocr_image(ctx.file, allow_run=False, partial=True) or []
    lab_types = {"bathroom": "bathroom", "wc": "wc_room", "sauna": "sauna", "kitchen": "kitchen",
                 "bedroom": "bedroom", "balcony": "balcony"}
    for t in want & set(lab_types):
        n_full = len(find_rooms(full, lab_types[t])) if full else 0
        n_up = sum(1 for r in typed if t in takeoff.room_types(r.label))
        if n_full > n_up:
            return abstain(ROUTE, f"{n_full} {t} label(s) read in all orientations but only {n_up} located with an area")
    # an unlabelled printed area inside the region of a typed label belongs to it
    loose = [r for r in pa.rooms if r.label is None and r.printed is not None]
    for r in typed:
        if r.printed is None and r.region_ok:
            inn = [a for a in loose if _inside(r.contour, _center(a.area_box))]
            if len(inn) == 1:
                r.printed, r.area_box = inn[0].printed, inn[0].area_box
                loose.remove(inn[0])
    rows, missing = [], []
    sc = pa.scale
    for r in typed:
        if r.printed is not None:
            src = "printed on plan"
            if r.measured is not None:
                src += f" (measured {r.measured:.2f})"
            rows.append({"label": r.label, "area": r.printed, "source": src, "room": r})
        elif r.measured is not None and MEASURED_ANSWERS:
            rows.append({"label": r.label, "area": r.measured, "source": f"measured region x scale ({sc.method})", "room": r})
        elif r.measured is not None:
            missing.append(f"{r.label}: no printed area; measured region x scale gives about {r.measured:.1f} m2, "
                           f"an estimate only (dev leave-one-out: median error 9%, within 5% for 37% of rooms)")
        else:
            missing.append(f"{r.label}: " + ("no printed area and no scale" if not sc.known else (r.why or "no region")))
    if missing:
        return abstain(ROUTE, "room(s) without an area: " + "; ".join(missing) + f"; scale: {sc.detail}")
    total, items, expl = takeoff.summary([{k: v for k, v in r.items() if k != "room"} for r in rows], tk)
    measured_any = any(r["room"].printed is None for r in rows)
    conflict = [r for r in rows if r["room"].printed is not None and r["room"].measured is not None
                and abs(r["room"].measured - r["room"].printed) > 0.1 * r["room"].printed]
    conf = 0.6 if measured_any else 0.8
    if conflict:
        conf = 0.5
    scale_note = (f"scale {sc.method}: {sc.detail}" if sc.known else f"no pixel scale ({sc.detail})")
    ans = Answer(value=total, unit="m2", route=ROUTE, confidence=conf, items=items,
                 explanation=expl + f" Areas: printed values read by local OCR where printed, else region x scale; {scale_note}.")
    if ctx.evidence:
        cv = ImageCanvas(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
        for i, row in enumerate(rows):
            r = row["room"]
            col = PALETTE[i % len(PALETTE)]
            if r.contour is not None and r.region_ok:
                pts = [tuple(int(v) for v in p[0]) for p in r.contour]
                if len(pts) >= 3:
                    cv.d.polygon(pts, fill=col + (70,), outline=col)
            if r.area_box is not None:
                b = r.area_box
                cv.box((b[0] - 3, b[1] - 3, b[2] + 3, b[3] + 3), color=col, width=2)
            lb = r.label_box or r.area_box
            cv.box((lb[0] - 3, lb[1] - 3, lb[2] + 3, lb[3] + 3), n=i + 1, color=col, width=2)
        for j in sc.rooms if sc.known else []:            # rooms that set the scale, thin blue
            b = pa.rooms[j].area_box
            if b is not None:
                cv.d.rectangle([b[0] - 5, b[1] - 5, b[2] + 5, b[3] + 5], outline=BLUE, width=max(1, round(cv.k)))
        lines = "; ".join(f"{i + 1}. {row['label']} {row['area']:.2f} m2 ({'printed' if row['room'].printed is not None else 'measured'})"
                          for i, row in enumerate(rows))
        ans.evidence = finish(cv.img, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question,
                              takeoff.answer_line(total, len(rows), ROUTE),
                              note=f"{lines}. Floor area only, no waste allowance. {scale_note} (scale rooms boxed blue)")
    return ans
