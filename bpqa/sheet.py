"""Large raster sheets: a whole drawing sheet exported as one big image (an A2 plan at 14000 x 9902 px).

The scan pipeline (routes/ocr.py, routes/detector.py, routes/raster_area.py) was built for CubiCasa scans of
240 to 2000 px. Run as is on a sheet it reads text at 2x (a 28000 px image), runs the detector at a scale far
from its training scale, and traces walls at a pixel size its constants were not set for. This module reads a
large sheet at the resolution each step needs, once, and caches the reading per drawing (out/sheet/):

  text     OCR on tiles of a copy scaled so the long side is OCR_SIDE px (text about 20 px high on an A2 sheet
           at 1:200); if the median text height read is below TEXT_MIN_PX the tiles are read again larger.
           Blank tiles are skipped. Upright pass, plus a 90-degree pass for labels written along a wall.
           Lines of one label ("Secondary" over "Classroom") are joined when they are centred over each other,
           of the same height, close, and no wall line runs between them.
  scale    (a) a printed ratio ("1:200", "Scale 1:100") with the sheet size printed in the title block (A0 to
           A4), when the image's aspect matches that ISO sheet: px per m = image px per paper mm x 1000 / ratio;
           (b) the scale bar: number labels on one baseline that step evenly from 0 ("0", "10m", "20m"), the
           bar drawn above them measured end to end; px per m from the bar length over its largest label.
           Each is checked against the other; agreement within SCALE_AGREE is reported as such.
  regions  wall-bounded regions traced with the scan route's wall code (raster_area.walls_base and
           wall_barrier) on a copy scaled to WALL_PPM px per m, so its pixel constants mean what they meant
           on CubiCasa; each label's region is flood filled from the label; area = pixels / px per m squared.
  objects  the door / window detector run on tiles of a copy scaled to DET_PPM px per m (inside the range of
           its training scales), one view, blank tiles skipped (routes/sheet_plan.py).

Nothing here depends on one drawing: thresholds are in metres or text heights, not in this sheet's pixels.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "out" / "sheet"
VERSION = 3

LARGE_SIDE = 5000          # images with a long side at least this are sheets, not CubiCasa scans
OCR_SIDE = 5000            # long side of the copy the OCR reads
TEXT_MIN_PX = 13           # median text height (px in the OCR copy) below which the tiles are read again larger
OCR_TILE, OCR_OVERLAP = 2000, 200
OCR_THREADS = 2             # tiles read concurrently (the recogniser's small batches leave cores idle)
BLANK_DARK_FRAC = 0.002    # a tile with fewer dark pixels than this is blank
SCALE_AGREE = 0.03         # printed ratio and scale bar agree within 3%
FOOTPRINT_MIN_M2 = 30.0    # parts of the traced footprint smaller than this are symbols, not building
SHEET_WIN_M = 4.5          # wall-line gaps up to this long are closed when tracing rooms (hatches, screens)
WALL_PPM = 50.0            # px per m of the copy the walls are traced on (CubiCasa scans are about 40 to 60)
ISO_MM = {"A0": (1189, 841), "A1": (841, 594), "A2": (594, 420), "A3": (420, 297), "A4": (297, 210)}

_LOCKS: dict[str, threading.Lock] = {}
_MEM: dict[str, dict] = {}


def image_size(file: Path) -> tuple[int, int] | None:
    from PIL import Image
    try:
        with Image.open(file) as im:
            return im.size
    except Exception:
        return None


def is_large(file: Path) -> bool:
    s = image_size(file)
    return bool(s) and max(s) >= LARGE_SIDE


@lru_cache(maxsize=2)
def _gray(path: str, mtime: int) -> np.ndarray:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    g = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if g is None:
        with Image.open(path) as im:
            g = np.asarray(im.convert("L"))
    return g


def gray(file: Path) -> np.ndarray:
    return _gray(str(file), int(Path(file).stat().st_mtime))


def scaled(g: np.ndarray, f: float) -> np.ndarray:
    if abs(f - 1.0) < 1e-3:
        return g
    return cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC)


# ---------------------------------------------------------------- OCR on tiles

def _tiles(w: int, h: int, t: int, ov: int):
    def starts(n):
        if n <= t:
            return [0]
        xs = list(range(0, n - t + 1, t - ov))
        if xs[-1] != n - t:
            xs.append(n - t)
        return xs
    return [(x, y) for y in starts(h) for x in starts(w)]


def _box(q):
    xs, ys = [p[0] for p in q], [p[1] for p in q]
    return [min(xs), min(ys), max(xs), max(ys)]


def _inter(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def ocr_pass(im: np.ndarray, rot: int, engine, progress=None) -> list[dict]:
    """Tokens of one pass over `im` (grey) in tiles; quads in `im` pixels. rot 90: the image is turned
    clockwise first (text running bottom-to-top reads upright) and the quads mapped back."""
    H0, W0 = im.shape[:2]
    work = cv2.rotate(im, cv2.ROTATE_90_CLOCKWISE) if rot == 90 else im
    h, w = work.shape[:2]
    dark = work < 170
    jobs = [(x0, y0) for x0, y0 in _tiles(w, h, OCR_TILE, OCR_OVERLAP)
            if dark[y0:y0 + OCR_TILE, x0:x0 + OCR_TILE].mean() >= BLANK_DARK_FRAC]

    done = [0]
    lock = threading.Lock()

    def one(xy):
        x0, y0 = xy
        res, _ = engine(cv2.cvtColor(work[y0:y0 + OCR_TILE, x0:x0 + OCR_TILE], cv2.COLOR_GRAY2BGR))
        out = []
        for quad, txt, conf in res or []:
            pts = []
            for x, y in quad:
                x, y = x + x0, y + y0
                if rot == 90:                 # turned clockwise: (x, y) came from (y, H0 - 1 - x)
                    x, y = y, H0 - 1 - x
                pts.append([float(x), float(y)])
            out.append({"text": txt, "conf": round(float(conf), 3), "quad": pts, "rot": rot, "mirror": False})
        if progress:
            with lock:
                done[0] += 1
                progress("text", done[0], len(jobs))
        return out
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(OCR_THREADS) as ex:
        toks = [t for part in ex.map(one, jobs) for t in part]
    # one text read twice where tiles overlap: keep the longer (a tile edge cuts the other), then the surer
    out = []
    for t in sorted(toks, key=lambda t: (-len(t["text"]), -t["conf"])):
        b = _box(t["quad"])
        if any(_inter(b, _box(o["quad"])) > 0.5 * min(_area(b), _area(_box(o["quad"]))) for o in out):
            continue
        out.append(t)
    return out


def text_height(t: dict) -> float:
    b = _box(t["quad"])
    return min(b[3] - b[1], b[2] - b[0])


def read_text(g: np.ndarray, progress=None) -> tuple[list[dict], float]:
    """(tokens in original pixels, OCR scale used). One upright pass: the OCR turns a text box that is clearly
    taller than wide by 90 degrees before reading it, and its classifier flips upside-down text, so labels written
    along a wall are read too (a separate 90-degree pass read nothing more on a test sheet and cost 30 s)."""
    from .routes.ocr import _engine
    eng = _engine()
    H, W = g.shape[:2]
    s = min(1.0, OCR_SIDE / max(H, W))
    im = scaled(g, s)
    up = ocr_pass(im, 0, eng, progress)
    hs = sorted(text_height(t) for t in up if t["conf"] >= 0.8 and len(t["text"]) >= 3)
    if hs and hs[len(hs) // 2] < TEXT_MIN_PX and s < 1.0:
        s = min(1.0, s * (TEXT_MIN_PX * 1.5) / hs[len(hs) // 2])
        im = scaled(g, s)
        up = ocr_pass(im, 0, eng, progress)
    for t in up:
        t["quad"] = [[round(x / s, 1), round(y / s, 1)] for x, y in t["quad"]]
    return up, s


# ---------------------------------------------------------------- labels: lines joined

def _wall_between(g: np.ndarray, a, b) -> bool:
    """True when a long dark line runs across the gap between two stacked text lines (a wall between rooms)."""
    y0, y1 = int(min(a[3], b[3])), int(max(a[1], b[1]))
    x0, x1 = int(max(0, min(a[0], b[0]))), int(min(g.shape[1], max(a[2], b[2])))
    if y1 <= y0 + 1 or x1 <= x0:
        return False
    band = g[y0 + 1:y1, x0:x1] < 120
    return bool(band.size) and band.mean(axis=1).max() > 0.6


def join_lines(toks: list[dict], g: np.ndarray | None = None, min_conf: float = 0.5) -> list[dict]:
    """Labels from upright tokens: lines of one label stacked and centred ("Secondary" / "Classroom" /
    "(Generic Needs)") joined top to bottom. Each label: {"text", "lines", "box", "conf", "h"}."""
    rows = []
    for t in toks:
        if t["conf"] < min_conf or not t["text"].strip():
            continue
        b = _box(t["quad"])
        if t.get("rot", 0) != 0:
            rows.append({"text": t["text"].strip(), "lines": [t["text"].strip()], "box": b, "conf": t["conf"],
                         "h": b[2] - b[0], "rot": t.get("rot", 0)})
            continue
        rows.append({"text": t["text"].strip(), "lines": [t["text"].strip()], "box": b, "conf": t["conf"],
                     "h": b[3] - b[1], "rot": 0})
    rows.sort(key=lambda r: (r["box"][1], r["box"][0]))
    used = [False] * len(rows)
    out = []
    for i, r in enumerate(rows):
        if used[i]:
            continue
        used[i] = True
        cur = dict(r, lines=list(r["lines"]), box=list(r["box"]))
        last = r
        while cur["rot"] == 0:
            nxt = None
            for j in range(i + 1, len(rows)):
                if used[j] or rows[j]["rot"] != 0:
                    continue
                c = rows[j]
                lb, cb = last["box"], c["box"]
                h = max(last["h"], c["h"])
                gap = cb[1] - lb[3]
                if gap > 0.9 * h:
                    if cb[1] > lb[3] + 2 * h:
                        break
                    continue
                if gap < -0.5 * h or not (0.6 <= c["h"] / max(last["h"], 1e-6) <= 1.6):
                    continue
                cx, lx = (cb[0] + cb[2]) / 2, (lb[0] + lb[2]) / 2
                if abs(cx - lx) > 0.25 * max(cb[2] - cb[0], lb[2] - lb[0]) + 0.5 * h:
                    continue
                if g is not None and _wall_between(g, lb, cb):
                    continue
                nxt = j
                break
            if nxt is None:
                break
            used[nxt] = True
            c = rows[nxt]
            cur["lines"].append(c["text"])
            cur["box"] = [min(cur["box"][0], c["box"][0]), cur["box"][1], max(cur["box"][2], c["box"][2]), c["box"][3]]
            cur["conf"] = min(cur["conf"], c["conf"])
            last = c
        cur["text"] = " ".join(cur["lines"])
        out.append(cur)
    return out


# ---------------------------------------------------------------- scale

SCALE_RE = re.compile(r"(?<![\d.:])1\s*:\s*(\d{1,4})(?![\d.:])")
SHEET_RE = re.compile(r"(?<![A-Z0-9])(A[0-4])(?![0-9])")
BAR_NUM_RE = re.compile(r"^\s*(\d{1,4})\s*(m|M|metres|meters)?\s*$")


def sheet_size(toks: list[dict], W: int, H: int) -> dict | None:
    """The ISO sheet size printed on the drawing (a lone "A2", preferably next to a "SIZE" heading) whose aspect
    matches the image's within 2%. {"name", "mm": (long, short), "px_per_mm", "token"}."""
    asp = max(W, H) / min(W, H)
    cands = []
    heads = [_box(t["quad"]) for t in toks if re.search(r"\b(SIZE|SHEET|FORMAT|PAPER)\b", t["text"].upper())]
    for t in toks:
        txt = t["text"].strip().upper()
        m = SHEET_RE.search(txt)
        if not m or len(txt) > 12:
            continue
        name = m.group(1)
        L, S = ISO_MM[name]
        if abs(asp - L / S) / (L / S) > 0.02:
            continue
        b = _box(t["quad"])
        near = any(abs((h[0] + h[2]) / 2 - (b[0] + b[2]) / 2) < 3 * (h[2] - h[0]) and 0 < b[1] - h[3] < 6 * (h[3] - h[1])
                   for h in heads)
        cands.append((near, txt == name, t["conf"], name, t))
    if not cands:
        return None
    near, alone, conf, name, t = max(cands, key=lambda c: c[:3])
    L, S = ISO_MM[name]
    return {"name": name, "mm": (L, S), "px_per_mm": max(W, H) / L, "token": t["text"], "box": _box(t["quad"]),
            "next_to_heading": near}


def printed_ratio(toks: list[dict]) -> dict | None:
    from .pdfvec import SCALE_WORDS
    hits = []
    for t in toks:
        for m in SCALE_RE.finditer(t["text"]):
            d = int(m.group(1))
            if 5 <= d <= 5000:
                hits.append({"denom": d, "word": bool(re.search(SCALE_WORDS, t["text"].lower())), "text": t["text"],
                             "conf": t["conf"], "box": _box(t["quad"])})
    if not hits:
        return None
    ds = sorted({h["denom"] for h in hits})
    if len(ds) > 1:
        kw = sorted({h["denom"] for h in hits if h["word"]})
        if len(kw) != 1:
            return {"denom": None, "several": ds, "hits": hits}
        ds = kw
    d = ds[0]
    return {"denom": d, "hits": [h for h in hits if h["denom"] == d]}


def scale_bar(toks: list[dict], g: np.ndarray) -> dict | None:
    """A scale bar: 3+ number labels on one baseline, evenly spaced in x and in value, the first 0 and at least one
    with a metre unit; the bar is the dark band just above them, measured end to end. {"ppm", "labels", ...}."""
    nums = []
    for t in toks:
        if t.get("rot", 0) != 0 or t["conf"] < 0.6:
            continue
        m = BAR_NUM_RE.match(t["text"])
        if not m:
            continue
        b = _box(t["quad"])
        txt = t["text"].strip()
        k = len(m.group(1))
        cx = b[0] + (b[2] - b[0]) * (k / max(len(txt), 1)) / 2        # centre of the digits, not of "10m"
        nums.append({"v": int(m.group(1)), "unit": bool(m.group(2)), "cx": cx, "box": b, "h": b[3] - b[1], "text": txt})
    best = None
    for z in sorted(nums, key=lambda n: n["cx"]):
        # a row of numbers on one baseline, increasing left to right; the "0" is often too small to be read
        row = [n for n in nums if abs((n["box"][1] + n["box"][3]) / 2 - (z["box"][1] + z["box"][3]) / 2) < 0.5 * z["h"]
               and n["cx"] > z["cx"] and 0.6 < n["h"] / z["h"] < 1.6]
        row.sort(key=lambda n: n["cx"])
        pts = [z]
        for n in row:
            if n["v"] > pts[-1]["v"]:
                pts.append(n)
        if len(pts) < 2 or not any(n["unit"] for n in pts) or (len(pts) == 2 and pts[0]["v"] != 0 and not all(n["unit"] for n in pts)):
            continue
        v = np.array([p["v"] for p in pts], float)
        x = np.array([p["cx"] for p in pts], float)
        k, c = np.polyfit(v, x, 1)
        if k <= 0 or (len(pts) > 2 and np.abs(x - (k * v + c)).max() > 0.02 * (x[-1] - x[0])):
            continue
        if best is None or len(pts) > len(best["labels"]):
            best = {"labels": pts, "ppm_labels": float(k), "x_zero": float(c)}
    if best is None:
        return None
    pts = best["labels"]
    h = max(p["h"] for p in pts)
    top = min(p["box"][1] for p in pts)
    x0, x1 = best["x_zero"], pts[-1]["cx"]
    # the bar: dark columns in a band up to 4 text heights above the labels, around the labelled span
    ya, yb = int(max(0, top - 4 * h)), int(top - 0.1 * h)
    xa, xb = int(max(0, x0 - 2 * h)), int(min(g.shape[1], x1 + 2 * h))
    band = g[ya:yb, xa:xb] < 120 if yb > ya and xb > xa else None
    bar = None
    if band is not None and band.size:
        cols = band.any(axis=0)
        # the longest run of dark columns (gaps of up to a text height are bar subdivisions drawn open)
        runs, s, gap = [], None, 0
        for i, on in enumerate(cols):
            if on:
                s = i if s is None else s
                gap = 0
                e = i
            elif s is not None:
                gap += 1
                if gap > h:
                    runs.append((s, e))
                    s, gap = None, 0
        if s is not None:
            runs.append((s, e))
        if runs:
            a, b = max(runs, key=lambda r: r[1] - r[0])
            L = (b - a)
            span = x1 - x0
            if abs(L - span) <= 0.03 * span + h:       # the bar runs from the 0 label to the last label
                bar = {"x0": xa + a, "x1": xa + b, "y0": ya, "y1": yb, "ppm": L / pts[-1]["v"]}
    if bar is None and len(pts) < 3:
        return None                          # two labels and no bar drawn between them: too weak
    ppm = bar["ppm"] if bar else best["ppm_labels"]
    return {"ppm": float(ppm), "ppm_labels": best["ppm_labels"], "bar": bar,
            "labels": [{"text": p["text"], "v": p["v"], "box": p["box"]} for p in pts],
            "detail": (f"scale bar {pts[0]['text']} to {pts[-1]['text']}: "
                       + (f"bar {bar['x1'] - bar['x0']:.0f} px long" if bar else "labels")
                       + f" -> {ppm:.1f} px per m")}


@dataclass
class SheetScale:
    ppm: float | None                 # pixels of the original image per metre
    source: str                       # "ratio+sheet", "bar", "ratio+sheet, bar agrees", "" (none)
    strong: bool                      # printed ratio and scale bar agree
    detail: str
    ratio: dict | None
    sheet: dict | None
    bar: dict | None

    def as_dict(self):
        return {"ppm": self.ppm, "source": self.source, "strong": self.strong, "detail": self.detail,
                "ratio": self.ratio, "sheet": self.sheet, "bar": self.bar}


def find_scale(toks: list[dict], g: np.ndarray) -> SheetScale:
    H, W = g.shape[:2]
    ratio = printed_ratio(toks)
    sheet = sheet_size(toks, W, H)
    bar = scale_bar(toks, g)
    ppm_r = None
    if ratio and ratio.get("denom") and sheet:
        ppm_r = sheet["px_per_mm"] * 1000 / ratio["denom"]
    parts = []
    if ratio and ratio.get("denom"):
        parts.append(f"printed ratio 1:{ratio['denom']}")
    elif ratio and ratio.get("several"):
        parts.append(f"several printed ratios {ratio['several']} (not used)")
    if sheet:
        parts.append(f"sheet size {sheet['name']} ({sheet['mm'][0]} x {sheet['mm'][1]} mm) printed, "
                     f"image {max(W, H)} px long -> {sheet['px_per_mm']:.2f} px per paper mm")
    if ppm_r:
        parts.append(f"ratio x sheet -> {ppm_r:.1f} px per m")
    if bar:
        parts.append(bar["detail"])
    if ppm_r and bar:
        diff = abs(ppm_r - bar["ppm"]) / bar["ppm"]
        if diff <= SCALE_AGREE:
            return SheetScale(ppm_r, "printed ratio and sheet size, scale bar agrees", True,
                              "; ".join(parts) + f"; they agree within {diff:.1%}", ratio, sheet, bar)
        return SheetScale(bar["ppm"], "scale bar (printed ratio and sheet size disagree)", False,
                          "; ".join(parts) + f"; they differ by {diff:.1%}, so the bar measured on the image is used",
                          ratio, sheet, bar)
    if bar:
        return SheetScale(bar["ppm"], "scale bar", False, "; ".join(parts), ratio, sheet, bar)
    if ppm_r:
        return SheetScale(ppm_r, "printed ratio and sheet size", False,
                          "; ".join(parts) + "; no scale bar read to check it", ratio, sheet, bar)
    return SheetScale(None, "", False, "; ".join(parts) or "no printed ratio with a sheet size, and no scale bar read",
                      ratio, sheet, bar)


# ---------------------------------------------------------------- wall-bounded regions

# English room-name nouns: a label holding one of these is a room name (title-block text and stray reads are not)
ROOM_NOUNS = ("ROOM", "CLASSROOM", "HALL", "KITCHEN", "STORE", "STORAGE", "TOILET", "TOILETS", "WC", "CORRIDOR",
              "OFFICE", "LOBBY", "STAIR", "STAIRS", "LIFT", "LIBRARY", "PLANT", "CHANGING", "SHOWER", "SHOWERS",
              "RECEPTION", "WORKROOM", "LANDING", "LAUNDRY", "SERVERY", "WALKWAY", "REPROGRAPHICS", "STUDIO",
              "LAB", "LABORATORY", "WORKSHOP", "GYM", "CAFE", "CANTEEN", "DINING", "BEDROOM", "BATHROOM", "LOUNGE",
              "ENTRANCE", "FOYER", "CLOAKROOM", "CLOAKS", "LAVATORY", "UTILITY", "CLEANER", "CLEANERS", "PANTRY",
              "SUITE", "AREA", "SPACE", "BAY", "RISER", "CUPBOARD", "LOCKERS", "PORCH", "HALLWAY", "VESTIBULE")


def words(text: str) -> list[str]:
    import re as _re
    return _re.findall(r"[A-Z]+", text.upper().replace("'", ""))


def is_room_name(text: str) -> bool:
    return any(w in ROOM_NOUNS for w in words(text))


def _split(m: np.ndarray, seeds: list[tuple[int, int]]) -> np.ndarray:
    """Split one region mask among several label seeds by watershed on the distance to the walls, so the cut
    falls at the narrowest passage between them. Returns a label image (1..len(seeds), 0 outside)."""
    ys, xs = np.nonzero(m)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    mm = m[y0:y1, x0:x1].astype(np.uint8)
    dist = cv2.distanceTransform(mm, cv2.DIST_L2, 3)
    land = (255 - np.clip(dist / max(dist.max(), 1e-6) * 254, 0, 254)).astype(np.uint8)
    markers = np.zeros(mm.shape, np.int32)
    markers[mm == 0] = len(seeds) + 1                       # outside: its own basin
    for i, (sx, sy) in enumerate(seeds):
        cv2.circle(markers, (int(sx - x0), int(sy - y0)), 2, i + 1, -1)
    cv2.watershed(cv2.cvtColor(land, cv2.COLOR_GRAY2BGR), markers)
    out = np.zeros(m.shape, np.int32)
    sub = markers.copy()
    sub[(sub < 1) | (sub > len(seeds))] = 0
    sub[mm == 0] = 0
    out[y0:y1, x0:x1] = sub
    return out


GAP_TILE, GAP_MARGIN = 1536, 256


def tiled_gaps(walls: np.ndarray, op: int):
    """raster_area.wall_line_gaps computed on overlapping tiles: the same maps, at a fraction of the memory (the
    whole-image version holds about 15 int64 copies of a 25 Mpx sheet, 4 GB). A margin of GAP_MARGIN px is far
    beyond the longest gap bridged and the deepest wall, so the kept centre of each tile is unchanged."""
    from .routes import raster_area as RA
    H, W = walls.shape
    out = [np.zeros((H, W), np.int32) for _ in range(4)]
    for y0 in range(0, H, GAP_TILE):
        for x0 in range(0, W, GAP_TILE):
            ya, xa = max(0, y0 - GAP_MARGIN), max(0, x0 - GAP_MARGIN)
            yb, xb = min(H, y0 + GAP_TILE + GAP_MARGIN), min(W, x0 + GAP_TILE + GAP_MARGIN)
            sub = walls[ya:yb, xa:xb]
            if not sub.any():
                continue
            parts = RA.wall_line_gaps(np.ascontiguousarray(sub), op)
            cy0, cx0 = y0 - ya, x0 - xa
            cy1, cx1 = cy0 + min(GAP_TILE, H - y0), cx0 + min(GAP_TILE, W - x0)
            for o, p in zip(out, parts):
                o[y0:y0 + (cy1 - cy0), x0:x0 + (cx1 - cx0)] = p[cy0:cy1, cx0:cx1]
    return tuple(out)


def trace_regions(g: np.ndarray, toks: list[dict], labels: list[dict], ppm: float) -> list[dict]:
    """Region of each room-name label on a copy at WALL_PPM px per m. Adds to each label "area_m2", "perimeter_m",
    "contour" (original px), "shared_with" (other room names in the same traced region) and "split" (True when
    the region held several room names and was divided among them at its narrowest passages), or "why" when
    the region is unusable."""
    from .routes import raster_area as RA
    f = min(1.0, WALL_PPM / ppm)
    small = scaled(g, f)
    bgr = cv2.cvtColor(small, cv2.COLOR_GRAY2BGR)
    stoks = [{**t, "quad": [[x * f, y * f] for x, y in t["quad"]]} for t in toks]
    wppm = ppm * f
    dark = RA._dark_textless(bgr, stoks)
    walls, op = RA.stroke_walls(dark) if RA.WALL_MODE == "stroke" else (None, None)
    del dark
    kind, gaps, base = "stroke", None, None
    if walls is not None and RA.LINE_BRIDGE:
        gaps = tiled_gaps(walls, op)
    elif walls is None:
        base = RA.walls_base(bgr, stoks)
        walls, kind, gaps = base
    if gaps is not None:
        # sheets draw serving hatches, glazed screens and wide openings as thin lines in a wall line; close
        # wall-line gaps up to SHEET_WIN_M (the scan route's WIN_M is 2.6 m, set on flats)
        bar = RA.bridge_lines(walls, gaps, int(round(SHEET_WIN_M * wppm)), max(3, int(round(RA.BAND_M * wppm))))
    else:
        bar, kind = RA.wall_barrier(bgr, stoks, int(round(RA.DOOR_M * wppm)), base=base)
    del bgr
    free = (bar == 0).astype(np.uint8)
    n, lab = cv2.connectedComponents(free, connectivity=4)
    h, w = free.shape
    seeds, comp_of = [], []
    for l in labels:
        cx, cy = (l["box"][0] + l["box"][2]) / 2 * f, (l["box"][1] + l["box"][3]) / 2 * f
        sp = RA._seed_pixel(free, cx, cy, r=max(12, int(0.6 * wppm))) if is_room_name(l["text"]) else None
        seeds.append(sp)
        comp_of.append(int(lab[sp[1], sp[0]]) if sp else -1)
    by_comp: dict[int, list[int]] = {}
    for i, c in enumerate(comp_of):
        if c >= 0:
            by_comp.setdefault(c, []).append(i)
    for l in labels:
        for k in ("why", "area_m2", "perimeter_m", "contour", "shared_with", "split"):
            l.pop(k, None)
    for cid, idx in by_comp.items():
        m = (lab == cid).astype(np.uint8)
        ys, xs = np.nonzero(m)
        if xs.min() == 0 or ys.min() == 0 or xs.max() == w - 1 or ys.max() == h - 1 or len(xs) > 0.35 * h * w:
            for i in idx:
                labels[i]["why"] = "region reaches the edge of the sheet (room not closed)"
            continue
        cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        c = max(cs, key=cv2.contourArea)
        filled = np.zeros_like(m)
        cv2.drawContours(filled, [c], -1, 1, thickness=-1)            # furniture inside the room counts
        parts = {i: filled for i in idx}
        if len(idx) > 1:
            sp = _split(filled, [seeds[i] for i in idx])
            parts = {i: (sp == k + 1).astype(np.uint8) for k, i in enumerate(idx)}
        for i in idx:
            pm = parts[i]
            px = int(pm.sum())
            l = labels[i]
            if px / (wppm * wppm) < 0.5:
                l["why"] = "region too small"
                continue
            cs2, _ = cv2.findContours(pm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            c2 = max(cs2, key=cv2.contourArea)
            l["area_m2"] = round(px / (wppm * wppm), 2)
            l["perimeter_m"] = round(cv2.arcLength(c2, True) / wppm, 2)
            l["contour"] = [[round(float(p[0][0]) / f, 1), round(float(p[0][1]) / f, 1)] for p in c2]
            l["shared_with"] = [labels[j]["text"] for j in idx if j != i]
            l["split"] = len(idx) > 1
            l["walls"] = kind
    # gross footprint: everything the free space outside cannot reach (rooms, walls, enclosed spaces), in parts
    # of at least FOOTPRINT_MIN_M2 (north arrows, logos and scale-bar blocks are smaller)
    outside = np.zeros(lab.shape, bool)
    edge_ids = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    for cid in edge_ids:
        if free[lab == cid].any():
            outside |= lab == cid
    inside = (~outside).astype(np.uint8)
    cs, _ = cv2.findContours(inside, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polys, gross = [], 0.0
    for c in cs:
        a = cv2.contourArea(c) / (wppm * wppm)
        if a < FOOTPRINT_MIN_M2:
            continue
        mm = np.zeros(inside.shape, np.uint8)
        cv2.drawContours(mm, [c], -1, 1, thickness=-1)
        gross += float((mm & inside).sum()) / (wppm * wppm)
        ap = cv2.approxPolyDP(c, 1.0, True)
        polys.append([[round(float(p[0][0]) / f, 1), round(float(p[0][1]) / f, 1)] for p in ap])
    return labels, {"gross_m2": round(gross, 1) if polys else None, "footprint": polys}


# ---------------------------------------------------------------- the cached reading

def cache_file(file: Path) -> Path:
    st = Path(file).stat()
    return CACHE / f"{Path(file).stem}.v{VERSION}.{st.st_size}.json"


def analyse(file: Path, regions: bool = True, progress=None) -> dict:
    """The sheet's text, labels, scale and (with `regions`) wall-bounded regions; cached on disk and in memory.
    One drawing is analysed by one thread at a time (a second question waits for the first's reading).
    `progress(stage, done, total)`, when given, is called as OCR tiles finish (app/precompute.py shows it)."""
    file = Path(file)
    key = str(cache_file(file))
    d = _MEM.get(key)
    if d is not None and (d["regions"] or not regions or not d["scale"]["ppm"]):
        return d                 # already read: no lock, so a text question never waits behind a wall tracing
    lock = _LOCKS.setdefault(key, threading.Lock())
    with lock:
        d = _MEM.get(key)
        if d is None and Path(key).exists():
            d = json.loads(Path(key).read_text(encoding="utf-8"))
        if d is None:
            g = gray(file)
            toks, s = read_text(g, progress)
            labels = join_lines(toks, g)
            sc = find_scale(toks, g)
            d = {"size": [int(g.shape[1]), int(g.shape[0])], "ocr_scale": s, "tokens": toks, "labels": labels,
                 "scale": sc.as_dict(), "regions": False}
        if regions and not d["regions"] and d["scale"]["ppm"]:
            # traced on a copy and swapped in when done: readers of the text-only reading never see half-traced labels
            d = {**d, "labels": [dict(l) for l in d["labels"]]}
            _, extra = trace_regions(gray(file), d["tokens"], d["labels"], d["scale"]["ppm"])
            d.update(extra)
            d["regions"] = True
        Path(key).parent.mkdir(parents=True, exist_ok=True)
        Path(key).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        _MEM[key] = d
        return d


def scale_of(d: dict) -> SheetScale:
    s = d["scale"]
    return SheetScale(s["ppm"], s["source"], s["strong"], s["detail"], s["ratio"], s["sheet"], s["bar"])


# ================================================================ BEGIN scale-assumption (builder BC)
# The person can change the printed ratio and sheet size behind a big sheet's scale after an answer ("1:[200] on
# [A2]" in the Approximation box, or "scale 1:100 on A3" in the question). The rooms stay as traced at the scale
# read from the sheet (tracing again at another scale would take minutes); their areas and lengths are converted
# with the person's scale: lengths x k, areas x k^2, k = read px per m / your px per m.

def editable_ratio(sc: SheetScale) -> bool:
    """A ratio control makes sense where the scale came from a printed ratio and a sheet size."""
    return bool(sc.ppm and sc.ratio and sc.ratio.get("denom") and sc.sheet and sc.source.startswith("printed ratio"))


def assume_ppm(d: dict, sc: SheetScale, assume: dict | None) -> float | None:
    """Pixels per metre for the person's ratio and sheet ({"ratio": 100, "sheet": "A3"}; either may be missing and
    then the one read is kept), or None when the assumption does not apply to this sheet."""
    if not assume or not editable_ratio(sc) or not ({"ratio", "sheet"} & set(assume)):
        return None
    denom = int(assume.get("ratio") or sc.ratio["denom"])
    name = assume.get("sheet") or sc.sheet["name"]
    return max(d["size"]) / ISO_MM[name][0] * 1000 / denom


def assumed(d: dict, assume: dict | None) -> dict:
    """The reading with the person's ratio and sheet applied: a copy whose room areas, perimeters and gross area are
    converted and whose "scale" is the person's (the one read kept under "read_scale"). `d` itself when nothing
    applies."""
    sc = scale_of(d)
    ppm = assume_ppm(d, sc, assume)
    if ppm is None or abs(ppm - sc.ppm) < 1e-9:
        return d
    k = sc.ppm / ppm
    denom = int(assume.get("ratio") or sc.ratio["denom"])
    name = assume.get("sheet") or sc.sheet["name"]
    labels = []
    for l in d["labels"]:
        l = dict(l)
        if l.get("area_m2") is not None:
            l["area_m2"] = round(l["area_m2"] * k * k, 2)
        if l.get("perimeter_m") is not None:
            l["perimeter_m"] = round(l["perimeter_m"] * k, 2)
        labels.append(l)
    d2 = {**d, "labels": labels}
    if d.get("gross_m2") is not None:
        d2["gross_m2"] = round(d["gross_m2"] * k * k, 1)
    L, S = ISO_MM[name]
    read = f"1:{sc.ratio['denom']} on {sc.sheet['name']}"
    sheet = {**sc.sheet, "name": name, "mm": (L, S), "px_per_mm": max(d["size"]) / L}
    detail = (f"your scale 1:{denom} on {name} (read on the sheet: {read}, {sc.ppm:.1f} px per m) -> {ppm:.1f} px per m; "
              f"rooms traced at the read scale, their areas x {k * k:.3f} and lengths x {k:.3f}")
    sc2 = SheetScale(ppm, "your ratio and sheet size", False, detail, {**sc.ratio, "denom": denom}, sheet, sc.bar)
    d2["scale"], d2["read_scale"] = sc2.as_dict(), d["scale"]
    return d2


def read_scale(d: dict) -> SheetScale:
    """The scale read on the sheet, also when the person set another (the detector runs at its resolution)."""
    return scale_of({"scale": d.get("read_scale") or d["scale"]})


def assume_payload(d: dict) -> dict | None:
    """What the app needs to show "1:[200] on [A2]" and rescale at once: the ratio and sheet in use, the ones read,
    and the image's long side (px per paper mm of any sheet size = long side / sheet long side)."""
    sc, read = scale_of(d), read_scale(d)
    if not editable_ratio(read):
        return None
    return {"kind": "ratio", "ratio": sc.ratio["denom"], "sheet": sc.sheet["name"],
            "read_ratio": read.ratio["denom"], "read_sheet": read.sheet["name"], "px_per_m": sc.ppm,
            "read_px_per_m": read.ppm, "long_px": max(d["size"]), "sheets": {n: ISO_MM[n][0] for n in ISO_MM},
            "assumed": bool(d.get("read_scale"))}
# ================================================================ END scale-assumption (builder BC)
