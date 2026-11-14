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

An area measured with this scale is ALWAYS tier "estimate"; the answer names the objects, the scale and the
leave-one-out error band (BANDS). Off unless ENABLED (see CHANGELOG "object scale" for the decision).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Off by default: on validation the estimates were not right more often than wrong (CHANGELOG). STOA_OBJECT_SCALE=1
# switches it on for a run (=0 off).
DEFAULT_ON = False
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


def summary(used: list[dict]) -> str:
    """'7 door openings, 1 WC pan'"""
    parts = []
    for cls, o in OBJECTS.items():
        n = sum(1 for c in used if c["cls"] == cls)
        if n:
            parts.append(f"{n} {o['name']}{'s' if n > 1 else ''} ({o['metres']:.2f} m)")
    return ", ".join(parts)


def object_scale(file, hyps, allow_run: bool = True):
    """(region set name, rooms, Scale) from standard objects, or (None, None, why) when there is no agreeing
    object scale. Called by raster_area.analyse only when the printed areas gave no scale."""
    from .routes.raster_area import Scale
    comb = combine(candidates(detections(Path(file), allow_run=allow_run)))
    if not comb.known:
        return None, None, comb.why
    ppm = 1.0 / comb.m_per_px
    name, rooms = pick_regions(hyps, ppm)
    if rooms is None:
        return None, None, "no walls region set"
    detail = (f"OBJECT SCALE (weaker than printed areas): median of {comb.n} standard objects, {summary(comb.used)}; "
              f"{ppm:.1f} px per m, objects agree within an interquartile range of {comb.iqr:.0%}")
    sc = Scale(comb.m_per_px ** 2, [], comb.iqr, f"objects ({name})", detail)
    sc.objects = comb.used
    return name, rooms, sc


def is_object(sc) -> bool:
    return bool(getattr(sc, "objects", None))


def band_text(areas=None) -> str:
    from .routes.raster_area import _band_cls
    classes = {_band_cls(a) for a in areas} if areas else {"3to6", "6up"}
    parts = []
    for c, name in (("3to6", "rooms of 3 to 6 m2"), ("6up", "rooms of 6 m2 and over")):
        if c in classes:
            b = BANDS[c]
            parts.append(f"{name} measured with an object scale: typical error about {b['median']:.0%}, 8 in 10 within "
                         f"{b['p80']:.0%}, only {b['within5']:.0%} within 5%")
    return ("; ".join(parts) + f" (leave-one-out on {BANDS['n']} printed rooms of {BANDS['plans']} plans, printed areas "
            f"hidden); check before ordering")


def draw(cv, sc, color):
    """Box the objects that set the scale, with their assumed size."""
    from .evidence import font
    f = font(max(10, round(11 * cv.k)))
    for c in getattr(sc, "objects", None) or []:
        x0, y0, x1, y1 = c["xyxy"]
        cv.d.rectangle([x0, y0, x1, y1], outline=color, width=max(1, round(cv.k)))
        cv.d.text((x0, y0 - 2), f"{OBJECTS[c['cls']]['metres']:g} m", fill=color, font=f, anchor="lb")
