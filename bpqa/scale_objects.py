"""Pixel scale of a scan from standard-size objects: the weaker scale source for plans that print no room areas.

raster_area sets a scale from 2+ printed room areas that agree. A plan that prints none (or whose printed rooms
do not agree) has no scale, and every measured area abstains. This module gives a second, clearly weaker scale:
objects the raster detector already finds and whose real size varies little.

  door     long side of the box (the opening in the wall), Finnish door modules 8M / 9M / 10M (0.8 to 1.0 m)
  toilet   long side of the WC pan box (pan and cistern, about 0.7 m)
  bathtub  long side of the tub box (about 1.7 m)

Beds and kitchen counters would also do, but the detector has no class for them. Showers and sinks vary too
much in size to measure anything.

Each detected object at or above its scoring floor gives one metres-per-pixel candidate: its size in metres /
its size in pixels. The scale is the median of the candidates, and it is set only when they agree: at least
MIN_CANDIDATES candidates and an interquartile range under MAX_IQR of the median. Otherwise there is no scale and
measured areas keep abstaining. The metres per object are the box sizes measured against the printed-area scale
on CubiCasa plans of our dev split that are not in the CubiCasa5K test split (tools/objscale_eval.py, tuning
half); the nominal sizes above are the sanity check.

Before the standard objects, printed door sizes are tried (module labels such as "9x21" matched to the door box
they label, see "printed door modules" below): a scale that rests on numbers printed on the drawing.

An area measured with either scale is ALWAYS tier "estimate" and is called an approximation; the answer names
the objects (tagged D1, D2 ... on the evidence), the scale, its spread and the leave-one-out error band (BANDS,
MODULE_BANDS). On by default since branch scale-approximation (CHANGELOG "area approximation"); STOA_OBJECT_SCALE=0
turns it off.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# On by default (his decision, 14 Nov: ship the approximation as long as the answer says where the number comes
# from). It was off on branch object-scale: 3 of 53 takeoff answers within 2% (CHANGELOG). STOA_OBJECT_SCALE=0 off.
DEFAULT_ON = True
ENABLED = {"1": True, "0": False}.get(os.environ.get("STOA_OBJECT_SCALE", ""), DEFAULT_ON)

# class -> detector policy whose boxes are read, metres of the box's long side, scoring floor, and the least
# long/short ratio of the box (a door box that is not elongated holds the swing or two leaves, not the opening).
# Metres: median box long side against the printed-area scale on the tuning half (door 0.843 m on 439 boxes,
# WC pan 0.73 m on 56, bathtub 1.69 m on 4; tools/objscale_eval.py calib). The door matches the 8M/9M modules.
OBJECTS = {
    "door": {"policy": "door", "metres": 0.84, "min_conf": 0.5, "min_aspect": 3.0, "name": "door opening"},
    "toilet": {"policy": "toilet", "metres": 0.73, "min_conf": 0.55, "min_aspect": 1.2, "name": "WC pan"},
    "bathtub": {"policy": "shower_or_bath", "metres": 1.7, "min_conf": 0.3, "min_aspect": 1.8, "name": "bathtub"},
}
# Agreement rule, chosen on the tuning half (tools/objscale_eval.py tune). The room error hardly moves across the
# grid (6 m2 and over: median 9.5% to 11.7%); a tighter IQR only abstains on more plans.
MIN_CANDIDATES = 4
MAX_IQR = 0.25          # interquartile range of the candidates / their median
# Leave-one-out error of rooms measured with this scale (printed areas hidden from the scale and the region choice),
# by measured area; tuning and validation halves pooled: python tools/area_band.py out/objscale/loo_tune.jsonl
# out/objscale/loo_val.jsonl. Update when the objects, the rule or the region code change.
BANDS = {"n": 929, "plans": 253,
         "under3": {"n": 274, "median": 0.426, "p80": 0.685, "within5": 0.102},
         "3to6": {"n": 162, "median": 0.196, "p80": 0.407, "within5": 0.117},
         "6up": {"n": 493, "median": 0.116, "p80": 0.304, "within5": 0.243}}


def candidates(dets_by_policy: dict, objects: dict | None = None) -> list[dict]:
    """One metres-per-pixel candidate per detected standard object. `dets_by_policy`: policy name -> fused boxes
    [{"cls", "conf", "xyxy"}] (detector.combine of its views)."""
    objects = OBJECTS if objects is None else objects
    out = []
    for cls, o in objects.items():
        for d in dets_by_policy.get(o["policy"]) or []:
            if d["cls"] != cls or d["conf"] < o["min_conf"]:
                continue
            x0, y0, x1, y1 = d["xyxy"]
            w, h = x1 - x0, y1 - y0
            lo, hi = min(w, h), max(w, h)
            if lo <= 0 or hi < 4 or hi / lo < o["min_aspect"]:
                continue
            out.append({"cls": cls, "conf": d["conf"], "xyxy": list(d["xyxy"]), "px": float(hi),
                        "m_per_px": o["metres"] / float(hi)})
    return out


@dataclass
class Combined:
    m_per_px: float | None
    n: int
    iqr: float
    used: list = field(default_factory=list)
    why: str = ""

    @property
    def known(self):
        return self.m_per_px is not None


def combine(cands: list[dict], min_n: int | None = None, max_iqr: float | None = None) -> Combined:
    """Median of the candidates when at least min_n agree within max_iqr (IQR / median); else no scale."""
    min_n = MIN_CANDIDATES if min_n is None else min_n
    max_iqr = MAX_IQR if max_iqr is None else max_iqr
    if len(cands) < min_n:
        return Combined(None, len(cands), 0.0, cands, f"{len(cands)} standard object(s) measured, {min_n} needed")
    v = np.array([c["m_per_px"] for c in cands])
    med = float(np.median(v))
    q1, q3 = np.percentile(v, [25, 75])
    iqr = float((q3 - q1) / med)
    if iqr > max_iqr:
        return Combined(None, len(cands), iqr, cands,
                        f"{len(cands)} standard objects disagree on the scale (spread {iqr:.0%} of the median, "
                        f"at most {max_iqr:.0%} allowed)")
    return Combined(med, len(cands), iqr, cands)


# ---------------------------------------------------------------- printed door modules (scale source b)
#
# Finnish plans print the door module next to each door: "9x21" (also "09x21", "O 9x21", "9*21") is a door
# opening of module 9M = 0.9 m wide and 2.1 m high. The width is a printed number, so a printed module matched
# to the detected door box it labels gives a scale that rests on the drawing's own text, not on an assumed
# size. Only door modules are read (width 7M to 12M, height 19M to 23M); window modules ("3-15x14") are not
# used, and no window is ever measured by its drawn size.
#
# The door box's long side is BOX_PER_MODULE x the printed module (the box covers the opening between the
# frames, not the whole module): median of matched boxes against the printed-area scale (tools/modscale_eval.py
# calib; the halves give 0.945 and 0.938, and the error bands below are cross-fitted). A module scale is set
# only when 2+ matched doors agree within MODULE_TOL.

MODULE_RE = re.compile(r"(?<![\d.,])0?([7-9]|1[0-2])\s?[xX×*�]\s?(19|2[0-3])(?![\d.,])")
# A door labelled with its width module only, written with the leading zero ("010", "09"): the whole token. On the
# dev pool it adds 27 plans with a module scale (71 to 98 of 370) at the same room error; the two halves' BOX_PER_MODULE
# also agree better with it (0.945 / 0.938 against 0.98 / 0.948). STOA_MODULE_WIDTH_ONLY=0 turns it off.
WIDTH_ONLY_RE = re.compile(r"0(7|8|9|10|11|12)$")
WIDTH_ONLY = os.environ.get("STOA_MODULE_WIDTH_ONLY", "1") == "1"
BOX_PER_MODULE = 0.94      # tools/modscale_eval.py calib: tune half 0.945 (82 doors), val half 0.938 (134); pooled 0.941
MODULE_MAX_GAP = 1.0       # token to door box gap, in door box long sides
MODULE_DOOR_CONF = 0.3
MODULE_DOOR_ASPECT = 2.0
MODULE_MIN = 2
MODULE_TOL = 0.08          # matched doors must agree within this of their median
# Leave-one-out of rooms measured with a module scale (printed areas hidden), cross-fitted: each half measured with
# BOX_PER_MODULE from the other half (val measured with 0.945, tune with 0.938); by measured area.
# python tools/area_band.py on the two rows files of tools/modscale_eval.py loo --half val --k 0.945 and --half tune --k 0.938.
MODULE_BANDS = {"n": 455, "plans": 87,
                "under3": {"n": 147, "median": 0.367, "p80": 0.697, "within5": 0.129},
                "3to6": {"n": 91, "median": 0.186, "p80": 0.369, "within5": 0.165},
                "6up": {"n": 217, "median": 0.124, "p80": 0.259, "within5": 0.217}}


def module_tokens(toks: list[dict], min_conf: float = 0.5) -> list[dict]:
    """Door module labels read by OCR: [{"w_m", "h_m", "text", "box"}], one per place on the plan (a label read
    in two rotations is kept once, the higher OCR confidence)."""
    from .routes.labels import quad_box
    found = []
    for t in toks or []:
        if t.get("conf", 0) < min_conf:
            continue
        m = MODULE_RE.search(t["text"])
        w = WIDTH_ONLY_RE.match(t["text"].strip()) if (WIDTH_ONLY and not m) else None
        if not m and not w:
            continue
        b = quad_box(t["quad"])
        found.append({"w_m": int((m or w).group(1)) / 10, "h_m": int(m.group(2)) / 10 if m else None,
                      "text": t["text"].strip(),
                      "box": tuple(float(v) for v in b), "conf": t["conf"]})
    found.sort(key=lambda x: -x["conf"])
    out = []
    for f in found:
        cx, cy = (f["box"][0] + f["box"][2]) / 2, (f["box"][1] + f["box"][3]) / 2
        if any(o["box"][0] - 2 <= cx <= o["box"][2] + 2 and o["box"][1] - 2 <= cy <= o["box"][3] + 2 for o in out):
            continue
        out.append(f)
    return out


def module_candidates(mods: list[dict], doors: list[dict]) -> list[dict]:
    """Each printed module matched to the nearest detected door box (one to one, nearest pairs first; the gap
    between the label and the box at most MODULE_MAX_GAP box long sides). One metres-per-pixel candidate per pair."""
    boxes = []
    for d in doors or []:
        if d.get("cls") != "door" or d["conf"] < MODULE_DOOR_CONF:
            continue
        x0, y0, x1, y1 = d["xyxy"]
        L, S = max(x1 - x0, y1 - y0), min(x1 - x0, y1 - y0)
        if S <= 0 or L < 4 or L / S < MODULE_DOOR_ASPECT:
            continue
        boxes.append((d, float(L)))
    pairs = []
    for i, m in enumerate(mods):
        cx, cy = (m["box"][0] + m["box"][2]) / 2, (m["box"][1] + m["box"][3]) / 2
        for j, (d, L) in enumerate(boxes):
            x0, y0, x1, y1 = d["xyxy"]
            gap = float(np.hypot(max(x0 - cx, 0, cx - x1), max(y0 - cy, 0, cy - y1))) / L
            if gap <= MODULE_MAX_GAP:
                pairs.append((gap, i, j))
    pairs.sort()
    used_m, used_d, out = set(), set(), []
    for gap, i, j in pairs:
        if i in used_m or j in used_d:
            continue
        used_m.add(i)
        used_d.add(j)
        d, L = boxes[j]
        m = mods[i]
        out.append({"cls": "door_module", "conf": d["conf"], "xyxy": list(d["xyxy"]), "px": L, "text": m["text"],
                    "text_box": list(m["box"]), "w_m": m["w_m"], "metres": BOX_PER_MODULE * m["w_m"],
                    "m_per_px": BOX_PER_MODULE * m["w_m"] / L, "gap": round(gap, 2)})
    return out


def combine_modules(cands: list[dict], min_n: int | None = None, tol: float | None = None) -> Combined:
    """The largest group of matched doors that agree within tol of a candidate, when it has min_n or more and
    is at least half of all matched doors; the scale is the group's median. Combined.iqr holds the group's
    largest deviation from the median (the spread)."""
    min_n = MODULE_MIN if min_n is None else min_n
    tol = MODULE_TOL if tol is None else tol
    if len(cands) < min_n:
        return Combined(None, len(cands), 0.0, cands, f"{len(cands)} printed door size(s) matched to a door, {min_n} needed")
    best = []
    for c0 in cands:
        g = [c for c in cands if abs(c["m_per_px"] - c0["m_per_px"]) <= tol * c0["m_per_px"]]
        if len(g) > len(best):
            best = g
    med = float(np.median([c["m_per_px"] for c in best])) if best else 0.0
    best = [c for c in best if abs(c["m_per_px"] - med) <= tol * med]
    if len(best) < min_n or len(best) < 0.5 * len(cands):
        return Combined(None, len(cands), 0.0, cands,
                        f"{len(cands)} printed door sizes matched to doors, but no {min_n} agree within {tol:.0%}")
    med = float(np.median([c["m_per_px"] for c in best]))
    spread = max(abs(c["m_per_px"] - med) / med for c in best)
    return Combined(med, len(best), spread, best)


def plan_module_tokens(file, allow_run: bool = True, mirror: bool = False) -> list[dict]:
    """Module labels of a plan from every cached OCR rotation of the image (mirrored passes only for a mirrored
    plan): module labels along a vertical wall read only sideways."""
    from .routes.ocr import ocr_image
    toks = ocr_image(Path(file), allow_run=False, partial=True) or []
    return module_tokens([t for t in toks if bool(t.get("mirror")) == bool(mirror)])


# ---------------------------------------------------------------- detections for a file

def _cached_only(file: Path, c: dict) -> bool:
    from .routes import detector as D
    for m in D.model_files(c):
        tag = D.model_tag(m)
        if tag is None:
            return False
        for t in D.tile_modes(c):
            mode = f"{c['imgsz']}" + (f"-t{t['tile']}s{t['scale']}o{t['overlap']}m{t['max_side']}" if t else "") \
                   + ("-d4" if c["tta"] else "")
            if not (D.REPO / "out" / "det" / f"{tag}-{mode}" / f"{Path(file).stem}.json").exists():
                return False
    return True


def detections(file: Path, allow_run: bool = True) -> dict:
    """policy -> fused boxes for every policy OBJECTS reads; a policy without a model (or, with allow_run False,
    without cached detections) is left out."""
    from .routes import detector as D
    out = {}
    for pol in {o["policy"] for o in OBJECTS.values()}:
        c = D.config(pol)
        if not c or not all(m.exists() for m in D.model_files(c)):
            continue
        if not allow_run and not _cached_only(file, c):
            continue
        v = D.views_for(Path(file), c)
        if v is not None:
            out[pol] = D.combine(v)
    return out


# ---------------------------------------------------------------- hook for raster_area

def pick_regions(hyps, ppm: float):
    """The walls@ppm region set whose assumed doorway scale is nearest (in ratio) to the object scale."""
    best = None
    for name, p, rs in hyps:
        if p is None:
            continue
        d = abs(np.log(p / ppm))
        if best is None or d < best[0]:
            best = (d, name, rs)
    return (best[1], best[2]) if best else (None, None)


TAG = {"door": "D", "door_module": "D", "toilet": "WC", "bathtub": "B"}


def tag_objects(used: list[dict]) -> list[dict]:
    """Give each scale object a short tag (D1, D2, WC1, B1), in reading order, written into the dict."""
    k = {}
    for c in sorted(used, key=lambda c: (round(c["xyxy"][1] / 40), c["xyxy"][0])):
        t = TAG.get(c["cls"], "S")
        k[t] = k.get(t, 0) + 1
        c["tag"] = f"{t}{k[t]}"
    return used


def obj_label(c: dict) -> str:
    """'door 0.84 m' (assumed size) or 'door 9x21 = 0.86 m' (printed module, box width it implies)."""
    if c["cls"] == "door_module":
        return f"door {c['text']} = {c['metres']:.2f} m"
    return f"{OBJECTS[c['cls']]['name'].replace(' opening', '')} {OBJECTS[c['cls']]['metres']:.2f} m"


def tag_range(used: list[dict], cls: str) -> str:
    """'D1 to D4', 'D1 and D2', 'WC1'."""
    tags = sorted((c["tag"] for c in used if c["cls"] == cls and c.get("tag")), key=lambda t: int(re.sub(r"\D", "", t) or 0))
    if not tags:
        return ""
    if len(tags) == 1:
        return tags[0]
    return f"{tags[0]} and {tags[1]}" if len(tags) == 2 else f"{tags[0]} to {tags[-1]}"


def summary(used: list[dict]) -> str:
    """'7 door openings (0.84 m), 1 WC pan (0.73 m)', or for printed modules '3 printed door sizes (9x21, 10x21)'."""
    parts = []
    mods = [c for c in used if c["cls"] == "door_module"]
    if mods:
        texts = sorted({c["text"] for c in mods})
        parts.append(f"{len(mods)} printed door size{'s' if len(mods) > 1 else ''} ({', '.join(texts[:4])})")
    for cls, o in OBJECTS.items():
        n = sum(1 for c in used if c["cls"] == cls)
        if n:
            parts.append(f"{n} {o['name']}{'s' if n > 1 else ''} ({o['metres']:.2f} m)")
    return ", ".join(parts)


# class -> (noun, plural, dimension, short plural for the Steps card, word before the tags)
NOUN = {"door": ("door opening", "door openings", "wide", "doors", "doors "),
        "toilet": ("WC pan", "WC pans", "long", "WC pans", ""),
        "bathtub": ("bathtub", "bathtubs", "long", "bathtubs", "")}


def scale_short(sc) -> str:
    """'4 doors x 0.84 m' / '3 printed door sizes (9x21, 10x21)' for the Steps card."""
    used = sc.objects
    if getattr(sc, "source", "") == "modules":
        return f"{len(used)} printed door sizes ({', '.join(sorted({c['text'] for c in used})[:3])})"
    bits = []
    for cls, o in OBJECTS.items():
        n = sum(1 for c in used if c["cls"] == cls)
        if n:
            bits.append(f"{n} {NOUN[cls][3] if n > 1 else NOUN[cls][0].split()[0]} x {o['metres']:.2f} m")
    return " + ".join(bits)


def scale_sentence(sc) -> str:
    """'Scale from 4 door openings assumed 0.84 m wide (doors D1 to D4), 52 px per m, spread 9%.'"""
    used = sc.objects
    ppm = 1.0 / np.sqrt(sc.m2_per_px)
    if getattr(sc, "source", "") == "modules":
        ws = sorted({c["w_m"] for c in used})
        wtxt = f"{ws[0]:.1f} m" if len(ws) == 1 else f"{ws[0]:.1f} to {ws[-1]:.1f} m"
        texts = ", ".join(sorted({c["text"] for c in used})[:4])
        what = (f"{len(used)} door sizes printed on the drawing ({texts}, openings {wtxt}), each matched to the door "
                f"it labels (doors {tag_range(used, 'door_module')})")
    else:
        bits = []
        for cls, o in OBJECTS.items():
            n = sum(1 for c in used if c["cls"] == cls)
            if n:
                noun, plural, dim, _, ref = NOUN[cls]
                bits.append(f"{n} {plural if n > 1 else noun} assumed {o['metres']:.2f} m {dim} "
                            f"({(ref if n > 1 else ref.rstrip('s ') + ' ') if ref else ''}{tag_range(used, cls)})")
        what = ", ".join(bits)
    return f"Scale from {what}, {ppm:.0f} px per m, spread {sc.spread:.0%}."


def object_scale(file, hyps, allow_run: bool = True, mirror: bool = False):
    """(region set name, rooms, Scale) from printed door modules (source b) or, failing that, standard objects
    (source c); (None, None, why) when neither agrees. Called by raster_area.analyse only when the printed areas
    gave no scale. Scale.source is "modules" or "objects", Scale.objects the tagged objects that set it."""
    from .routes.raster_area import Scale
    dets = detections(Path(file), allow_run=allow_run)
    mods = plan_module_tokens(file, allow_run=allow_run, mirror=mirror)
    why = []
    for source in ("modules", "objects"):
        comb = (combine_modules(module_candidates(mods, dets.get("door") or [])) if source == "modules"
                else combine(candidates(dets)))
        if not comb.known:
            why.append(("printed door sizes: " if source == "modules" else "standard objects: ") + comb.why)
            continue
        ppm = 1.0 / comb.m_per_px
        name, rooms = pick_regions(hyps, ppm)
        if rooms is None:
            return None, None, "no walls region set"
        used = tag_objects(comb.used)
        if source == "modules":
            detail = (f"PRINTED DOOR SIZES (weaker than printed areas): median of {comb.n} doors matched to their "
                      f"printed size, {summary(used)}; {ppm:.1f} px per m, largest deviation from the median {comb.iqr:.0%}")
        else:
            detail = (f"OBJECT SCALE (weaker than printed areas): median of {comb.n} standard objects, {summary(used)}; "
                      f"{ppm:.1f} px per m, objects agree within an interquartile range of {comb.iqr:.0%}")
        if why:
            detail += f" ({'; '.join(why)})"
        sc = Scale(comb.m_per_px ** 2, [], comb.iqr, f"{source} ({name})", detail)
        sc.objects, sc.source = used, source
        return name, rooms, sc
    return None, None, "; ".join(why)


def is_object(sc) -> bool:
    return bool(getattr(sc, "objects", None))


def bands(sc=None) -> dict:
    return MODULE_BANDS if (sc is not None and getattr(sc, "source", "") == "modules" and MODULE_BANDS) else BANDS


def typical_error(area: float, sc=None) -> float:
    from .routes.raster_area import _band_cls
    return bands(sc)[_band_cls(area)]["median"]


def band_text(areas=None, sc=None) -> str:
    from .routes.raster_area import _band_cls
    B = bands(sc)
    src = "printed door sizes" if B is MODULE_BANDS else "an object scale"
    classes = {_band_cls(a) for a in areas} if areas else {"3to6", "6up"}
    parts = []
    for c, name in (("3to6", "rooms of 3 to 6 m2"), ("6up", "rooms of 6 m2 and over")):
        if c in classes:
            b = B[c]
            parts.append(f"{name} measured with {src}: typical error about {b['median']:.0%}, 8 in 10 within "
                         f"{b['p80']:.0%}, only {b['within5']:.0%} within 5%")
    return ("; ".join(parts) + f" (leave-one-out on {B['n']} printed rooms of {B['plans']} plans, printed areas "
            f"hidden); check before ordering")


def about(v: float) -> str:
    """Rounded for the sentence: 12 m2, 4.5 m2."""
    return f"{v:.0f}" if v >= 10 else f"{v:.1f}"


# Room types not approximated: none now. End to end (tools/objscale_e2e.py --half all, printed areas hidden) kitchens
# were off by a median of 87% (15 answers; an open kitchen merges with the living room in the traced outline), against
# 10% for bedrooms. Markus decided on 2026-11-14 to answer them anyway, as an estimate with KITCHEN_NOTE in the sentence
# and the traced outline drawn on the evidence. Put "kitchen": "<why>" back here to make them abstain again.
NO_APPROX_TYPES: dict = {}
KITCHEN_NOTE = ("Check: this outline may include the open living area next to the kitchen (kitchens measured this way "
                "were off by a median of 35% on 12 labelled kitchens, and by 87% in the end-to-end test where open "
                "kitchens merged with the living room).")


def approx_text(total: float, areas: list, sc, no_scale_why: str, types=(), net: float | None = None) -> str:
    """The answer sentence of an approximation: what it is, why, where the scale comes from, how wrong it may be."""
    errs = sorted({round(typical_error(a, sc), 2) for a in areas})
    err = f"about {errs[0]:.0%}" if len(errs) == 1 else f"about {errs[0]:.0%} to {errs[-1]:.0%}"
    size = "a room this size" if len(areas) == 1 else "rooms of these sizes"
    head = f"About {about(total)} m2 (approximation)"
    if net is not None and total > net + 0.005:          # a takeoff to order: net floor area plus waste
        head = (f"About {about(total)} m2 to order (approximation: net floor about {about(net)} m2 plus "
                f"{(total / net - 1) * 100:.0f}% waste)")
    return (f"{head}: {no_scale_why}. {scale_sentence(sc)} "
            f"Typical error for {size}: {err}." + (f" {KITCHEN_NOTE}" if "kitchen" in types else "") + " Check before ordering.")


def draw(cv, sc, color, record=True):
    """Box the objects that set the scale, dashed, each labelled with its tag and size; a printed door size also
    gets its label boxed. Recorded in the marks sidecar as context kind "scale_object" (not pinned or numbered)."""
    from .evidence import dashed_rect, font, record_context
    f = font(max(10, round(11 * cv.k)))
    w = max(2, round(2 * cv.k))
    placed = []

    def free(b):
        return all(b[2] < p[0] or b[0] > p[2] or b[3] < p[1] or b[1] > p[3] for p in placed)
    for c in getattr(sc, "objects", None) or []:
        x0, y0, x1, y1 = c["xyxy"]
        rect = (x0 - 2, y0 - 2, x1 + 2, y1 + 2)
        dashed_rect(cv.d, rect, color, w, max(3, round(4 * cv.k)))
        placed.append(rect)
    for c in getattr(sc, "objects", None) or []:
        x0, y0, x1, y1 = c["xyxy"]
        rect = (x0 - 2, y0 - 2, x1 + 2, y1 + 2)
        # short label on the drawing (the tag and the size); the full one is in the marks and the Steps card
        txt = (f"{c.get('tag', '')} {c['text']} = {c['metres']:.2f} m" if c["cls"] == "door_module" else
               f"{c.get('tag', '')} {OBJECTS[c['cls']]['name'].replace(' opening', '')} {OBJECTS[c['cls']]['metres']:.2f} m").strip()
        tw, th = cv.d.textbbox((0, 0), txt, font=f, anchor="lt")[2:]
        spots = [(rect[0], rect[1] - th - 3), (rect[0], rect[3] + 3), (rect[2] + 3, rect[1]), (rect[0] - tw - 3, rect[1]),
                 (rect[2] + 3, rect[3] - th), (rect[0], rect[1] - 2 * th - 6)]
        pos = next((p for p in spots if free((p[0] - 2, p[1] - 1, p[0] + tw + 2, p[1] + th + 1))), spots[0])
        b = (pos[0] - 2, pos[1] - 1, pos[0] + tw + 2, pos[1] + th + 1)
        placed.append(b)
        cv.d.rectangle(list(b), fill=(255, 255, 255, 225))
        cv.d.text(pos, txt, fill=(150, 85, 0), font=f, anchor="lt")
        txt = f"{c.get('tag', '')} {obj_label(c)}".strip()
        if c.get("text_box"):
            b = c["text_box"]
            dashed_rect(cv.d, (b[0] - 3, b[1] - 3, b[2] + 3, b[3] + 3), color, max(1, round(cv.k)), max(3, round(3 * cv.k)))
        if record:
            record_context(cv.img, rect, "scale_object", tone="amber", label=txt)
