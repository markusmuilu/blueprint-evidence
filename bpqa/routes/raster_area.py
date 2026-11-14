"""Room areas on raster plans (CubiCasa scans): printed areas, a pixel scale, measured regions.

Three signals, each drawn on the evidence image:

  printed  the plan's own area labels ("12,1 m2", comma or point decimals) read by the local OCR,
           each attached to the room label printed just above or beside it ("MH1" over "15,3 m2").
           On a plan drawn rotated or mirrored this is done in the OCR pass the text reads in
           (plan_tokens), with the boxes mapped back to the original pixels.
  region   the white region enclosed by walls around a label: text is erased, dark linework is the
           barrier (thickened by a few pixels so hairline gaps in walls close), the region is flood
           filled from the label and grown back by the same few pixels; furniture inside a room is
           part of the room (holes are filled). A region is unusable when it reaches the image edge,
           holds another room's label or area (an open doorway merged two rooms), or is tiny.
           analyse() uses the walls-only barrier (wall_barrier): either the legacy mask (closing, then
           a fixed opening, then refine()) or, on plans drawn with solid walls, the stroke mask (thick
           strokes only, doors and windows closed along the wall line), whichever region set agrees
           better on a scale (hypotheses / choose).
  scale    square metres per pixel, from rooms that have both a printed area and a usable region:
           s_i = printed_i / pixels_i. A scale is set only when 2+ rooms agree within SCALE_TOL of
           their median; otherwise no scale and measured areas abstain.

Area of a room for a takeoff: the printed value when the plan prints one (it is the designer's
number), else, when a scale is established, region pixels x scale as tier "estimate" with the
measured leave-one-out error band stated. If any asked room has neither, the sum abstains, because a
partial sum would be a wrong order quantity. Wall questions: floor and walls are separate lines; walls
are perimeter of the region x an assumed height, openings not deducted, estimate, scale required.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from .labels import ROOM_PATTERNS, is_word, parts, quad_box, PLAN_WORDS
from .ocr import _run, cache_path
from .. import scale_objects as OS

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


# ---------------------------------------------------------------- text orientation
#
# Some scans are drawn rotated or mirrored, so their labels read sideways, upside down or mirrored
# ("MH 1" over "11,0 m2" upside down and mirrored). The OCR cache (ocr.py) holds one pass per rotation
# (0/90/180/270) of the image and of its left-right mirror, with token quads already mapped back to the
# original pixels. The plan's text orientation is the pass whose reads pair the most room labels with
# printed areas; its tokens are moved into that pass's reading frame (text upright, left to right), the
# areas are attached to labels there exactly as on an upright plan, and the boxes go back to the original
# pixels for the regions and the evidence image. An upright plan stays on the r0 pass unless another pass
# pairs clearly more rooms.

UPRIGHT = (False, 0)       # (mirror, rot) of the plain upright pass
ORIENT_MIN_GAIN = 2        # another pass must pair at least this many more rooms than the upright one


def frame_fns(mirror: bool, rot: int, w: float, h: float):
    """(to_frame, to_image) point maps between original pixels and the reading frame of one OCR pass:
    the image left-right mirrored when `mirror`, then rotated clockwise by `rot` degrees (as in ocr._run)."""
    def fwd(x, y):
        if mirror:
            x = w - x
        if rot == 90:
            x, y = h - y, x
        elif rot == 180:
            x, y = w - x, h - y
        elif rot == 270:
            x, y = y, w - x
        return x, y

    def inv(x, y):
        if rot == 90:
            x, y = y, h - x
        elif rot == 180:
            x, y = w - x, h - y
        elif rot == 270:
            x, y = w - y, x
        if mirror:
            x = w - x
        return x, y
    return fwd, inv


def _map_box(b, fn):
    pts = [fn(b[0], b[1]), fn(b[2], b[1]), fn(b[2], b[3]), fn(b[0], b[3])]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def read_rooms_oriented(toks: list[dict], mirror: bool, rot: int, w: float, h: float, min_conf: float = 0.5):
    """read_rooms on one pass's tokens in that pass's reading frame; boxes and seeds in original pixels."""
    fwd, inv = frame_fns(mirror, rot, w, h)
    ft = [{**t, "quad": [list(fwd(*p)) for p in t["quad"]], "mirror": False} for t in toks]
    rooms, free = read_rooms(ft, min_conf)
    for r in rooms:
        r.label_box = _map_box(r.label_box, inv) if r.label_box is not None else None
        r.area_box = _map_box(r.area_box, inv) if r.area_box is not None else None
        r.seed = inv(*r.seed)
    free = [{**l, "box": _map_box(l["box"], inv)} for l in free]
    return rooms, free


def orient_name(mirror: bool, rot: int) -> str:
    if (mirror, rot) == UPRIGHT:
        return "upright"
    return (f"rotated {rot} degrees" if rot else "") + (" and " if rot and mirror else "") + ("mirrored" if mirror else "")


def plan_tokens(f: Path, allow_run: bool = True, min_conf: float = 0.5):
    """((mirror, rot), tokens, extra rooms) for the plan's text: the pass the text reads in, that pass's tokens in
    original pixels, and the rooms whose label and printed area read together only in another rotation of the
    same (mirrored or not) image, such as a room labelled along a vertical wall. An extra room is kept only where
    the main pass read no area or room word over its label or area (a sideways re-read of upright text).
    Without the full OCR cache (allow_run False and some pass missing): the upright pass only."""
    from .labels import _overlap
    from .ocr import PASSES, cached, ocr_image
    if not allow_run and not cached(f):
        t = tokens_r0(f, allow_run=False)
        return (UPRIGHT, [x for x in t if x["conf"] >= min_conf] if t is not None else None, [])
    if allow_run:
        tokens_r0(f, allow_run=True)          # r0 first, in the format the tools read
    allt = ocr_image(f, allow_run=allow_run)
    img = cv2.imread(str(f))
    if allt is None or img is None:
        return UPRIGHT, None, []
    h, w = img.shape[:2]
    by = {p: [] for p in PASSES}
    for t in allt:
        if t["conf"] >= min_conf:
            by[(bool(t.get("mirror")), t["rot"])].append(t)
    read = {p: read_rooms_oriented(by[p], *p, w, h, min_conf)[0] for p in PASSES}

    def score(p):
        rs = read[p]
        return (sum(r.label is not None and r.printed is not None for r in rs), sum(r.printed is not None for r in rs))
    s0 = score(UPRIGHT)
    best, bs = UPRIGHT, s0
    for p in PASSES:
        if p != UPRIGHT and score(p)[0] >= s0[0] + ORIENT_MIN_GAIN and score(p) > bs:
            best, bs = p, score(p)
    taken = [quad_box(t["quad"]) for t in by[best] if parse_area(t["text"]) is not None or room_word(t["text"])]
    extra = []
    for p in PASSES:
        if p[0] != best[0] or p == best:
            continue
        for r in read[p]:
            if r.label is None or r.printed is None:
                continue
            bx = [b for b in (r.label_box, r.area_box) if b is not None]
            if any(_overlap(b, c, 0.3) for b in bx for c in taken):
                continue
            r.extra["orient"] = p
            extra.append(r)
            taken += bx
    return best, by[best], extra


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
            rooms.append(Room(a["rest"], a["box"], a["v"], a["box"], _center(a["box"]), extra={"area_text": a["text"]}))
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
            rooms.append(Room(labels[best]["text"], lb, a["v"], b, seed, extra={"area_text": a["text"]}))
        else:
            rooms.append(Room(None, None, a["v"], b, _center(b), extra={"area_text": a["text"]}))
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


def _dark_textless(img_bgr, toks):
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    dark = (g < DARK).astype(np.uint8)
    for t in toks:
        x0, y0, x1, y1 = (int(round(v)) for v in quad_box(t["quad"]))
        dark[max(0, y0 - 1):y1 + 2, max(0, x0 - 1):x1 + 2] = 0
    return dark


# ---- stroke walls
#
# On plans drawn with solid walls (CubiCasa "colorful" and many others) the walls are the thick dark strokes
# and everything else (furniture, dashed fixture outlines, door swings, hatching, colour fill edges) is thin.
# The wall mask is then the dark pixels opened at a size set from the plan's own stroke widths, with no
# closing first: closing merges a dashed double outline (a bed head, a wardrobe) into a band that survives
# the opening and cuts the room. Plans whose walls are outlines or hatching have no thick strokes; they keep
# the legacy mask (closing, then a fixed opening, then refine()).

# "stroke": thick-stroke walls when the plan has them, else legacy; "legacy": always legacy (STOA_WALL_MODE=legacy)
WALL_MODE = os.environ.get("STOA_WALL_MODE", "stroke")
THIN_MAX = 3               # strokes up to this wide (px) are linework, not walls
THICK_MIN_FRAC = 0.25      # share of the dark stroke length that must be thicker than THIN_MAX for stroke walls
OPEN_FRAC = 0.6            # opening size = this x the plan's thinnest wall width


def stroke_widths(dark) -> np.ndarray:
    """Histogram of stroke widths (px) of a binary linework mask: at each ridge pixel of the distance
    transform the stroke is 2 x distance wide; each ridge pixel weighs its width (about the dark pixels
    it stands for). Index = width, rounded."""
    dt = cv2.distanceTransform(dark, cv2.DIST_L2, 3)
    mx = cv2.dilate(dt, np.ones((3, 3), np.uint8))
    ridge = (dt > 0) & (dt >= mx - 1e-6)
    w = np.round(2 * dt[ridge]).astype(int)
    return np.bincount(w, weights=w, minlength=64).astype(float)


def wall_width(dark):
    """The plan's thinnest wall stroke width in px, or None when the plan has no thick strokes (walls drawn
    as outlines or hatching). The thinnest wall is the first width above THIN_MAX that holds at least 15%
    of the thick stroke weight, counted from the thin end."""
    h = stroke_widths(dark)
    tot = h.sum()
    thick = h[THIN_MAX + 1:].sum()
    if tot <= 0 or thick / tot < THICK_MIN_FRAC:
        return None
    for w in range(THIN_MAX + 1, len(h)):
        if h[w] >= 0.15 * thick:
            return w
    return None


def stroke_walls(dark):
    """(walls mask, opening size px) from thick strokes, or (None, None) when the plan has none."""
    w = wall_width(dark)
    if w is None:
        return None, None
    s = max(THIN_MAX + 1, int(round(OPEN_FRAC * w)))
    k = np.ones((s, s), np.uint8)       # square: axis-aligned wall ends stay square, so doorway gaps stay clean bands
    return cv2.morphologyEx(dark, cv2.MORPH_OPEN, k), s


def bridge(walls, door_px: int, band_px: int = 0):
    """Wall gaps up to `door_px` bridged along the wall direction (rows and columns). With band_px, a
    bridge must be a thin band across the gap (at most band_px deep): the closing also fills a room or
    closet narrower than door_px wall to wall, and that fill is deep, not a doorway."""
    if door_px <= 0:
        return walls
    h = cv2.morphologyEx(walls, cv2.MORPH_CLOSE, np.ones((1, door_px), np.uint8))
    v = cv2.morphologyEx(walls, cv2.MORPH_CLOSE, np.ones((door_px, 1), np.uint8))
    if band_px > 0:
        h = h & (1 - walls)
        v = v & (1 - walls)
        h = h & (1 - cv2.morphologyEx(h, cv2.MORPH_OPEN, np.ones((band_px + 1, 1), np.uint8)))
        v = v & (1 - cv2.morphologyEx(v, cv2.MORPH_OPEN, np.ones((1, band_px + 1), np.uint8)))
    return walls | h | v


WIN_M = 2.6        # gaps in a wall line up to this long (windows, wide doors) are closed along the wall line
BAND_M = 0.55      # ... when the gap is a band no deeper than this (a wall is never thicker)
GAP_TOL = 2        # px: a wall face drawn ragged by antialiasing still ends the same gap


def _gap_runs(walls, min_depth: int = 2):
    """For every free pixel, the row gap it lies in (between the nearest wall pixels left and right): its
    length, and the depth of the stack of rows above and below that have the same gap (both ends within
    GAP_TOL px of the row before). A window or doorway in a horizontal wall is such a stack, as deep as the
    wall is thick; the inside of a room is a stack as deep as the room, or no stack at all. Only stacks that
    pass the checks below count. Returns (length, depth), depth 0 where none."""
    hh, ww = walls.shape
    idx = np.broadcast_to(np.arange(ww), (hh, ww))
    wl = walls > 0
    left = np.maximum.accumulate(np.where(wl, idx, -1), axis=1)
    right = np.minimum.accumulate(np.where(wl, idx, ww)[:, ::-1], axis=1)[:, ::-1]
    ok = (~wl) & (left >= 0) & (right < ww)
    length = np.where(ok, right - left - 1, 0)
    same = np.zeros_like(ok)                       # same gap as the row above: both ends within GAP_TOL px
    same[1:] = (ok[1:] & ok[:-1] & (np.abs(left[1:] - left[:-1]) <= GAP_TOL)
                & (np.abs(right[1:] - right[:-1]) <= GAP_TOL))
    start = ~same                                  # a new vertical run of equal gaps starts here
    rid = np.cumsum(start, axis=0) + np.arange(ww)[None, :] * (hh + 1)
    cnt = np.bincount(rid.ravel())
    depth = np.where(ok, cnt[rid], 0)
    # a gap in a wall line has open floor on both sides of the stack (outside and room, or room and room);
    # a slot between two strokes (a cupboard, a duct) is closed off by a stroke at one end
    free = ~wl
    above = np.zeros_like(ok)
    above[1:] = free[:-1]
    end = np.ones_like(ok)
    end[:-1] = ~same[1:]
    below = np.zeros_like(ok)
    below[:-1] = free[1:]
    top_ok = np.zeros(cnt.shape[0], bool)
    bot_ok = np.zeros(cnt.shape[0], bool)
    top_ok[rid[start & ok]] = above[start & ok]
    bot_ok[rid[end & ok]] = below[end & ok]
    depth = np.where(top_ok[rid] & bot_ok[rid], depth, 0)
    # and the wall goes on beyond both ends of the gap, in the gap's direction (a stub of the same wall, or
    # a wall meeting it at a corner): its run there is at least half the gap stack's depth. The floor between
    # a room wall and a cabinet has a wall end only as long as the wall is thick, the cabinet's stroke even less.
    lastfree = np.maximum.accumulate(np.where(wl, -1, idx), axis=1)
    nextfree = np.minimum.accumulate(np.where(wl, ww, idx)[:, ::-1], axis=1)[:, ::-1]
    rows = np.broadcast_to(np.arange(hh)[:, None], (hh, ww))
    lc, rc = np.clip(left, 0, ww - 1), np.clip(right, 0, ww - 1)
    stub = np.minimum(lc - lastfree[rows, lc], nextfree[rows, rc] - rc)
    depth = np.where(stub >= np.maximum(2, 0.5 * depth), depth, 0)
    # at least one end is a stub of the same wall: across the gap direction it is about as thick as the
    # gap stack is deep. A column through a window into the room ends at long walls at both ends.
    wsame = np.zeros_like(wl)
    wsame[1:] = wl[1:] & wl[:-1]
    wid = np.cumsum(~wsame, axis=0) + np.arange(ww)[None, :] * (hh + 1)
    across = np.bincount(wid.ravel(), weights=wl.ravel().astype(float))[wid]   # wall run across, through each pixel
    ext = np.minimum(across[rows, lc], across[rows, rc])
    depth = np.where(ext <= 1.5 * depth + 2, depth, 0)
    # a gap through a wall is as deep as the wall is thick, and no wall in the mask is thinner than the
    # opening that made it; a shallower stack is a nub on a wall face, and bridging it would cut the room
    depth = np.where(depth >= min_depth, depth, 0)
    return length, depth


def wall_line_gaps(walls, min_depth: int = 2):
    """(length, depth) gap maps for rows and for columns (columns transposed back), computed once per plan."""
    lh, dh = _gap_runs(walls, min_depth)
    lv, dv = _gap_runs(np.ascontiguousarray(walls.T), min_depth)
    return lh, dh, lv.T, dv.T


def bridge_lines(walls, gaps, win_px: int, band_px: int):
    """Close gaps in a wall line: free pixels whose row (column) gap is at most win_px long and whose stack
    of identical gaps is at most band_px deep."""
    lh, dh, lv, dv = gaps
    b = ((lh > 0) & (lh <= win_px) & (dh > 0) & (dh <= band_px)) | ((lv > 0) & (lv <= win_px) & (dv > 0) & (dv <= band_px))
    return walls | b.astype(walls.dtype)


LINE_BRIDGE = True
LEGACY_LINE_BRIDGE = False     # wall-line bridges on the legacy mask: tried, its ragged blobs give many false stacks
THIN_DOOR_BRIDGE = os.environ.get("STOA_THIN_DOOR_BRIDGE", "0") == "1"   # off: worse on dev (CHANGELOG)


def walls_base(img_bgr, toks, mode: str | None = None):
    """(walls mask before doorway bridging, kind, wall-line gaps or None): kind "stroke" (thick-stroke walls)
    when the mode allows it and the plan has thick strokes, else "legacy" (closing by CLOSE_K, then opening
    by OPEN_K)."""
    dark = _dark_textless(img_bgr, toks)
    if (mode or WALL_MODE) == "stroke":
        walls, s = stroke_walls(dark)
        if walls is not None:
            return walls, "stroke", (wall_line_gaps(walls, s) if LINE_BRIDGE else None)
    d = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((CLOSE_K, CLOSE_K), np.uint8))
    walls = cv2.morphologyEx(d, cv2.MORPH_OPEN, np.ones((OPEN_K, OPEN_K), np.uint8))
    return walls, "legacy", (wall_line_gaps(walls, OPEN_K) if LEGACY_LINE_BRIDGE else None)


def wall_barrier(img_bgr, toks, door_px: int, mode: str | None = None, base=None):
    """Walls only: thin linework (furniture, door swings, stairs, dimension lines) is removed so it
    cannot cut a room into pieces, and wall gaps up to `door_px` (doorways) are bridged along the
    wall direction. `door_px` is the doorway width in pixels for an assumed scale; analyse() tries
    several and keeps the one that agrees with the scale it produces. Returns (mask, kind), kind as
    walls_base(); `base` is a walls_base() result to reuse."""
    walls, kind, gaps = base if base is not None else walls_base(img_bgr, toks, mode)
    if gaps is None:
        band = max(3, int(round(BAND_M * door_px / DOOR_M))) if THIN_DOOR_BRIDGE else 0
        return bridge(walls, door_px, band), kind
    ppm = door_px / DOOR_M
    return bridge_lines(walls, gaps, int(round(WIN_M * ppm)), max(3, int(round(BAND_M * ppm)))), kind


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
                    door_px: int | None = None, bar=None, base=None):
    """Flood-fill each room's region; mark unusable ones with the reason. `others` are the centres
    of every other label and area on the plan (a region holding one of them merged two rooms).
    door_px None: every dark line is a barrier (thickened by k); else walls only (wall_barrier, with
    `base` a walls_base() result to reuse). Stroke walls need no refine(): furniture is not a barrier."""
    h, w = img_bgr.shape[:2]
    if k is None:
        k = max(1, round(min(h, w) / 450))
    kind = "lines"
    if bar is None:
        if door_px is None:
            bar = barrier(img_bgr, toks, k)
        else:
            bar, kind = wall_barrier(img_bgr, toks, door_px, base=base)
    elif door_px is not None:
        kind = "legacy"
    if door_px is not None:
        k = 0
    free = (bar == 0).astype(np.uint8)
    n, lab = cv2.connectedComponents(free, connectivity=4)
    thick = None
    if door_px is not None and REFINE and kind == "legacy":
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
        r.extra["walls"] = kind
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


def plan_rooms(toks, orient=UPRIGHT, wh=None, extra=()):
    """Rooms (printed areas attached to labels, then the free labels) and the centres of every label and
    area. `orient` (mirror, rot) and `wh` (image width, height): the pass the tokens come from; `extra`:
    rooms read in another rotation (plan_tokens)."""
    rooms, free = read_rooms(toks) if orient == UPRIGHT else read_rooms_oriented(toks, *orient, *wh)
    rooms = rooms + list(extra)
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
    walls only: "walls@ppm" with the legacy wall mask, and, when the plan has thick wall strokes,
    "stroke@ppm" with the stroke wall mask. choose() keeps the set whose rooms agree best on a scale,
    so the stroke mask is used where it closes more rooms consistently and the legacy one elsewhere
    (outlined or hatched exterior walls vanish from the stroke mask and its rooms leak).
    Returns [(name, ppm or None, rooms)]."""
    import copy
    out = []
    if mode in ("auto", "lines"):
        rs = copy.deepcopy(proto)
        measure_regions(img, rs, others, toks)
        out.append(("lines", None, rs))
    if mode in ("auto", "walls"):
        bases = [("walls", walls_base(img, toks, mode="legacy"))]
        if WALL_MODE == "stroke":
            sb = walls_base(img, toks, mode="stroke")
            if sb[1] == "stroke":
                bases.append(("stroke", sb))
        for tag, base in bases:
            for ppm in PPM_GRID:
                rs = copy.deepcopy(proto)
                measure_regions(img, rs, others, toks, door_px=max(3, round(DOOR_M * ppm)), base=base)
                out.append((f"{tag}@{ppm}", ppm, rs))
    return out


STROKE_MIN_ROOMS = 3   # a stroke set with no legacy scale to beat needs this many agreeing rooms (twice the sets to pick from)


def choose(hyps, exclude: int | None = None):
    """The region set whose scale has the most agreeing rooms. A walls hypothesis counts only when
    the scale it produces matches the pixels-per-metre it assumed for doorways (within 20%).
    A stroke set (hypotheses) replaces the best legacy set only with strictly more agreeing rooms,
    or, when no legacy set gives a scale, with at least STROKE_MIN_ROOMS: the incumbent wins ties,
    and twice as many sets to pick from would otherwise make a chance agreement of 2 rooms likelier."""
    best = {}
    for name, ppm, rs in hyps:
        sc = estimate_scale(rs, exclude=exclude)
        if not sc.known:
            continue
        if ppm is not None and abs((1 / np.sqrt(sc.m2_per_px)) / ppm - 1) > PPM_AGREE:
            continue
        fam = "stroke" if name.startswith("stroke") else "legacy"
        key = (len(sc.rooms), -sc.spread)
        if fam not in best or key > best[fam][0]:
            best[fam] = (key, name, rs, sc)
    pick = best.get("legacy")
    st = best.get("stroke")
    if st is not None and (st[0][0] > pick[0][0] if pick is not None else st[0][0] >= STROKE_MIN_ROOMS):
        pick = st
    if pick is None:
        return None, None, Scale(None, [], 0, "none", "no region set gives 2 rooms that agree on a scale")
    _, name, rs, sc = pick
    sc.method = f"printed-areas ({name})"
    return name, rs, sc


@dataclass
class PlanAreas:
    rooms: list
    free_labels: list
    scale: Scale
    regions: str
    orient: tuple = UPRIGHT      # (mirror, rot) of the OCR pass the labels and areas were read in


@lru_cache(maxsize=4)
def analyse(path: str, allow_run: bool = True) -> PlanAreas | None:
    f = Path(path)
    orient, toks, extra = plan_tokens(f, allow_run=allow_run)
    img = cv2.imread(str(f))
    if toks is None or img is None:
        return None
    proto, free, others = plan_rooms(toks, orient, (img.shape[1], img.shape[0]), extra)
    for r in extra:                      # their text is erased from the linework like the main pass's
        for b in (r.label_box, r.area_box):
            toks = toks + [{"quad": [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]], "text": "", "conf": 1.0}]
    hyps = hypotheses(img, toks, proto, others, mode="walls")   # walls beat lines and auto on dev (CHANGELOG)
    name, rooms, sc = choose(hyps)
    if not sc.known and OS.ENABLED:     # no printed-area scale: standard objects, weaker, estimate only (scale_objects.py)
        on, orooms, osc = OS.object_scale(f, hyps, allow_run)
        name, rooms, sc = (on, orooms, osc) if orooms is not None else (name, rooms, sc)
        sc.detail += "" if orooms is not None else f"; no object scale either: {osc}"
    if rooms is None:
        rooms = hyps[0][2]
    if sc.known:
        for r in rooms:
            if r.region_ok:
                r.measured = round(r.pixels * sc.m2_per_px, 2)
    return PlanAreas(rooms, free, sc, name or "none", orient)


# ---------------------------------------------------------------- takeoff answer

ROUTE = "raster-area"
# Measured areas (region x scale) answer rooms that print no area, as tier "estimate", when a scale is
# established (2+ printed rooms agree). The error band is the leave-one-out measurement on the dev split
# (tools/area_eval.py, CHANGELOG "raster area estimates"): each printed room measured with a scale from
# the OTHER printed rooms only. Update BAND with the numbers of the latest run. Set False to abstain again.
MEASURED_ANSWERS = True
# Pooled leave-one-out rows of dev (193 rooms, 52 plans) and test (73 rooms, 22 plans): `python tools/area_band.py
# loo_dev.jsonl loo_test.jsonl`. Classes are by the MEASURED area. Under 3 m2 the region is usually a fragment
# of the room (median error 46%, nearly always too small, dev and test alike), so such a room is not answered.
BANDS = {"n": 266, "plans": 74,
         "under3": {"n": 73, "median": 0.463, "p80": 0.773, "within5": 0.137},
         "3to6": {"n": 50, "median": 0.13, "p80": 0.384, "within5": 0.28},
         "6up": {"n": 143, "median": 0.065, "p80": 0.2, "within5": 0.448}}
MIN_MEASURED_M2 = 3.0
# Region perimeter (convex hull) against the annotated room outlines of CubiCasa's model.svg, scale-free
# (tools/perim_eval.py): dev 303 rooms of 55 plans (median 7.8%), test 116 rooms of 21 plans (median 10.8%), pooled.
PERIM_BAND = {"n": 419, "plans": 76, "median": 0.087, "p80": 0.268, "within10": 0.525, "signed": -0.04}


def _band_cls(area: float) -> str:
    return "under3" if area < MIN_MEASURED_M2 else ("3to6" if area < 6.0 else "6up")


def band_text(areas=None) -> str:
    """The measured error band for the classes of the measured rooms (all classes answered when areas is None)."""
    classes = {_band_cls(a) for a in areas} if areas else {"3to6", "6up"}
    parts = []
    for c, name in (("3to6", "measured rooms of 3 to 6 m2"), ("6up", "measured rooms of 6 m2 and over")):
        if c in classes:
            b = BANDS[c]
            parts.append(f"{name}: typical error about {b['median']:.0%}, 8 in 10 within {b['p80']:.0%}, "
                         f"only {b['within5']:.0%} within 5%")
    return "; ".join(parts) + "; check before ordering"


def _inside(contour, pt) -> bool:
    return contour is not None and cv2.pointPolygonTest(contour, (float(pt[0]), float(pt[1])), False) >= 0


def region_perimeter_px(contour, eps: float | None = None) -> float:
    """Perimeter of a room region in pixels. Default: the convex hull's perimeter. The traced contour of a
    flood-filled region is ragged (furniture, door arcs, doorway bridges), and its own length overstates
    the room's walls; on dev against the annotated room outlines the hull's median error is 7.8% against
    17.2% for the simplified contour (CHANGELOG). A concave room (L shape) comes out a little short.
    `eps` given: the simplified contour's length instead (tools/perim_eval.py compares the variants)."""
    if eps is not None:
        return float(cv2.arcLength(cv2.approxPolyDP(contour, eps, True), True))
    return float(cv2.arcLength(cv2.convexHull(contour), True))


def _name(r) -> str:
    return r.label or (f"unlabelled {r.printed:g} m2" if r.printed is not None else "unlabelled room")


def _room_steps(row, r, want) -> list[dict]:
    """The reasoning for one room of the answer, in the order a person checks it: the label that was read and
    the room type it was read as, the printed area, and the traced region. Pixel boxes of the source image."""
    from .. import takeoff
    types = sorted(takeoff.room_types(r.label or "") & set(want)) or sorted(takeoff.room_types(r.label or ""))
    steps = []
    if r.label_box is not None and r.label:
        steps.append({"kind": "label", "title": "Label read", "text": r.label,
                      "note": f"read as {', '.join(types)}" if types else "room label", "rect": r.label_box})
    if row["kind"] == "printed" and r.area_box is not None:
        raw = (r.extra or {}).get("area_text") or f"{r.printed:g} m2"
        steps.append({"kind": "value", "title": "Size printed", "text": raw, "note": f"size {r.printed:.1f} m2",
                      "rect": r.area_box})
    if r.contour is not None and r.region_ok:
        pts = [(float(p[0][0]), float(p[0][1])) for p in r.contour]
        if row["kind"] == "printed":
            note = (f"measures {r.measured:.2f} m2, printed {r.printed:g}" if r.measured is not None
                    else "no pixel scale to measure it")
        else:
            note = f"no printed size: measured {row['area']:.2f} m2 (region x scale), an estimate"
        cap = f"region {r.measured:.1f} m2" if r.measured is not None else "region"
        steps.append({"kind": "region", "title": "Room region", "text": "outlined", "note": note, "cap": cap, "pts": pts})
    return steps


def takeoff_answer(ctx, q, img):
    from .. import takeoff
    from .. import walls as wallmod
    from ..answer import Answer, abstain, estimate
    from ..evidence import AMBER, BLUE, GREY, ImageCanvas, dashed_rect, evidence_path, finish, font, record_context, record_steps
    from PIL import Image

    tk = q.extra["takeoff"]
    pa = analyse(str(ctx.file))
    if pa is None:
        return abstain(ROUTE, "image or OCR unavailable")
    from .labels import find_rooms
    from .ocr import ocr_image
    full = ocr_image(ctx.file, allow_run=False, partial=True) or []
    lab_types = {"bathroom": "bathroom", "wc": "wc_room", "sauna": "sauna", "kitchen": "kitchen",
                 "bedroom": "bedroom", "balcony": "balcony"}
    # one-letter labels (S, K) only when printed upper case: OCR noise reads stray strokes as "s"
    labelled = [{"name": r.label, "room": r} for r in pa.rooms if r.label
                and not (len(r.label.strip(" .")) == 1 and not r.label.strip(" .").isupper())]
    if "wc" in tk["types"] and full and find_rooms(full, "wc_room"):
        used = takeoff.select(labelled, tk)[0]     # a WC label exists (maybe only read sideways): WC rooms only
    else:
        # "toilets" and no WC label: the rooms that contain a toilet, by their bathroom labels (a scan
        # carries no toilet symbols this route reads, so the answer says the toilet is not confirmed)
        used, _, tk = takeoff.toilet_fallback(labelled, tk, has_toilet=None)
    fb = takeoff.fallback_reason(tk)
    want = set(tk["types"])
    typed = [w["room"] for w in used]
    if not typed:
        read = sorted({r.label for r in pa.rooms if r.label})
        why = (tk.get("toilet_rooms") or {}).get("why")
        on = orient_name(*pa.orient)
        return abstain(ROUTE, (f"{why} (labels read {on}: {', '.join(read[:15]) or 'none'})" if why else
                               f"no room label of type {', '.join(tk['types'])} read {on} on the plan "
                               f"(labels read: {', '.join(read[:15]) or 'none'})"))
    # labels read in other orientations (full OCR cache) that the plan's text pass missed would be
    # rooms left out of the sum: abstain rather than under-order
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
                r.extra["area_text"] = inn[0].extra.get("area_text")
                loose.remove(inn[0])
    sc = pa.scale
    want_floor, want_walls, how = wallmod.surfaces(tk)
    rows, missing = [], []
    for r in typed:
        if r.printed is not None:
            src = "printed on plan"
            if r.measured is not None:
                src += f" (measured {r.measured:.2f})"
            rows.append({"label": r.label, "area": r.printed, "source": src, "room": r, "kind": "printed"})
        elif r.measured is not None and sc.known and MEASURED_ANSWERS and r.measured < MIN_MEASURED_M2:
            missing.append(f"{r.label}: no printed area; its measured region is only {r.measured:.1f} m2, and regions under "
                           f"{MIN_MEASURED_M2:g} m2 are too unreliable to answer (median error {BANDS['under3']['median']:.0%}, "
                           f"nearly always too small)")
        elif r.measured is not None and sc.known and MEASURED_ANSWERS:
            rows.append({"label": r.label, "area": r.measured, "kind": "measured", "room": r,
                         "source": f"MEASURED, not printed: region x scale, ESTIMATE ({(OS.band_text if OS.is_object(sc) else band_text)([r.measured])})"})
        else:
            missing.append(f"{r.label}: no printed area and " + ("no pixel scale" if not sc.known else (r.why or "no closed region")))
    if missing:
        return abstain(ROUTE, "room(s) without an area: " + "; ".join(missing) + f"; scale: {sc.detail}")
    m_per_px = float(np.sqrt(sc.m2_per_px)) if sc.known else None
    if want_walls:
        if not sc.known:
            return abstain(ROUTE, f"walls on a scan need a pixel scale from printed room areas; {sc.detail}")
        bad = [f"{r['label']}: {r['room'].why or 'no closed region'}" for r in rows if r["room"].contour is None]
        if bad:
            return abstain(ROUTE, "walls need each room's closed region for its perimeter; " + "; ".join(bad))

    printed_rows = [r for r in rows if r["kind"] == "printed"]
    meas_rows = [r for r in rows if r["kind"] == "measured"]
    scale_names = [_name(pa.rooms[j]) + f" ({pa.rooms[j].printed:g})" for j in sc.rooms] if sc.known else []
    scale_txt = (f"Scale from printed rooms {', '.join(scale_names)}: {sc.detail}" if sc.known
                 else f"No pixel scale ({sc.detail})")
    basis = []
    if printed_rows:
        basis.append("printed on the plan: " + ", ".join(f"{r['label']} {r['area']:.2f}" for r in printed_rows[:12]))
    if meas_rows:
        basis.append("MEASURED (pixel region x scale, not printed): " + ", ".join(f"{r['label']} {r['area']:.2f}" for r in meas_rows[:12]))
    basis_txt = "Floor areas " + "; ".join(basis) + "."
    if pa.orient != UPRIGHT:
        basis_txt += f" The plan's text reads {orient_name(*pa.orient)}; labels and areas were read in that orientation."
    bt = band_text([r["area"] for r in meas_rows]) if meas_rows else ""
    band_note = (f" Measured areas are estimates: {bt}. (Leave-one-out on {BANDS['n']} printed rooms of {BANDS['plans']} plans, "
                 f"dev and test, each measured with a scale from the other printed rooms.)") if meas_rows else ""
    if OS.is_object(sc):                # object scale: its own description and leave-one-out band
        scale_txt, bt = sc.detail, OS.band_text([r["area"] for r in meas_rows])
        band_note = f" Measured areas are estimates from an OBJECT scale: {bt}." if meas_rows else ""
    uncertain = [{"room": r["label"], "area_m2": round(r["area"], 2), "why": "measured, not printed"} for r in meas_rows]

    if want_walls:
        wrooms = []
        for r in rows:
            per = region_perimeter_px(r["room"].contour) * m_per_px
            wrooms.append({"label": r["label"], "floor_area": r["area"], "floor_src": r["source"], "perimeter": per,
                           "perimeter_src": f"perimeter of the measured room region x scale ({sc.method})",
                           "height": None, "openings": [], "openings_unknown": True})
        total, items, expl, totals = wallmod.summary(wrooms, tk, ctx.q.question)
        for it, r in zip(items, rows):
            it["area_kind"] = r["kind"]
        items.append({"totals": totals})
        expl += (f" Walls on a scan are an ESTIMATE: perimeter of the measured room region x an ASSUMED ceiling height "
                 f"({wallmod.DEFAULT_CEILING_M:.2f} m unless the question gives one); door and window openings are NOT deducted "
                 f"(not detectable here), so the wall area is gross and an upper bound. "
                 f"The room perimeters are typically off by about {PERIM_BAND['median']:.0%} (8 in 10 within {PERIM_BAND['p80']:.0%}; "
                 f"region against the annotated room outlines of {PERIM_BAND['n']} dev and test rooms), before the height assumption. ") + basis_txt + " " + scale_txt + "." + band_note
        est = "walls: perimeter x assumed height, openings not deducted" + ("; some rooms measured" if meas_rows else "")
        ans = Answer(value=total, unit="m2", route=ROUTE, confidence=0.45, items=items, explanation=expl)
        ans.tier, ans.tier_reason = "estimate", est + ("; " + bt if meas_rows else "") + ("; " + fb if fb else "")
        head = (f"Answer [ESTIMATE]: {total:.2f} m2 = floor {totals['floor_m2'] or 0:.2f} + walls {totals['walls_m2']:.2f}"
                f" + {totals['waste_pct']:g}% waste {totals['waste_m2']:.2f}   route: {ROUTE}") if want_floor else                (f"Answer [ESTIMATE]: {total:.2f} m2 = walls {totals['walls_m2']:.2f} + {totals['waste_pct']:g}% waste "
                f"{totals['waste_m2']:.2f}   route: {ROUTE}")
    else:
        total, items, expl = takeoff.summary([{k: v for k, v in r.items() if k not in ("room", "kind")} for r in rows], tk)
        for it, r in zip(items, rows):
            it["area_kind"] = r["kind"]
        conflict = [r for r in printed_rows if r["room"].measured is not None
                    and abs(r["room"].measured - r["area"]) > 0.1 * r["area"]]
        conf = 0.5 if conflict else (0.55 if meas_rows else 0.8)
        expl += " " + basis_txt + " " + scale_txt + "." + band_note
        ans = Answer(value=total, unit="m2", route=ROUTE, confidence=conf, items=items, explanation=expl)
        if meas_rows:
            ans.tier, ans.tier_reason = "estimate", f"{len(meas_rows)} room(s) have no printed area: measured region x scale; {bt}"
        if fb:
            estimate(ans, fb)
        head = (f"Answer [ESTIMATE]: {total:.2f} m2 over {len(rows)} room(s), floor only   route: {ROUTE}" if meas_rows
                else takeoff.answer_line(total, len(rows), ROUTE, "rooms with a toilet, no WC room" if fb else ""))
    ans.uncertain = uncertain

    if ctx.evidence:
        cv = ImageCanvas(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
        fs = round(14 * cv.k)
        f = font(fs)
        for i, row in enumerate(rows):
            r = row["room"]
            col = BLUE if row["kind"] == "printed" else AMBER
            if r.contour is not None and r.region_ok:
                pts = [tuple(int(v) for v in p[0]) for p in r.contour]
                if len(pts) >= 3:
                    cv.d.polygon(pts, fill=col + (70,), outline=col)
            lb = r.label_box or r.area_box
            if r.area_box is not None and row["kind"] == "printed":
                b = r.area_box                                  # the printed value, boxed
                cv.d.rectangle([b[0] - 4, b[1] - 4, b[2] + 4, b[3] + 4], outline=col, width=max(2, round(2 * cv.k)))
            if lb is not None:
                cv.box((lb[0] - 3, lb[1] - 3, lb[2] + 3, lb[3] + 3), n=i + 1, color=col, width=2)
            if row["kind"] == "measured":
                x, y = r.seed
                txt = f"measured {row['area']:.1f} m2"
                tb = cv.d.textbbox((x, y + fs), txt, font=f, anchor="mm")
                cv.d.rectangle([tb[0] - 3, tb[1] - 2, tb[2] + 3, tb[3] + 2], fill=AMBER)
                cv.d.text((x, y + fs), txt, fill=(28, 48, 65), font=f, anchor="mm")
            record_steps(cv.img, i + 1, _room_steps(row, r, want))
        used_ids = {id(row["room"]) for row in rows}
        for j in sc.rooms if sc.known else []:            # printed rooms that only set the scale: grey, dashed, no fill
            b = pa.rooms[j].area_box
            if b is not None and id(pa.rooms[j]) not in used_ids:
                rect = (b[0] - 7, b[1] - 7, b[2] + 7, b[3] + 7)
                dashed_rect(cv.d, rect, GREY, max(1, round(cv.k)), max(4, round(5 * cv.k)))
                record_context(cv.img, rect, "scale")
        lines = "; ".join(f"{i + 1}. {row['label']} {row['area']:.2f} m2 ({row['kind']})" for i, row in enumerate(rows))
        if want_walls:
            extra = f" Walls {totals['walls_m2']:.2f} m2 gross: perimeter x assumed {wallmod.DEFAULT_CEILING_M:.2f} m, openings not deducted."
        else:
            extra = " Floor area only, no waste allowance."
        sc_note = f"Scale from printed rooms {', '.join(scale_names[:5])}" if sc.known else "No pixel scale"
        if OS.is_object(sc):
            sc_note = f"OBJECT scale from {OS.summary(sc.objects)}, boxed amber"; OS.draw(cv, sc, AMBER)
        if meas_rows:
            sc_note += f"; measured rooms amber, {bt}"
        interp = (takeoff.interpretation(tk, [(r["label"], r["area"]) for r in rows]) + " ") if fb else ""
        ans.evidence = finish(cv.img, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question,
                              head, note=f"{interp}{sc_note}. {lines}.{extra} Rooms in the answer blue, printed value boxed; rooms that only set the scale dashed grey.")
    return ans
