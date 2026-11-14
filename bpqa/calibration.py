"""Calibrated "proven" tier for scan routes.

Each scan route (raster-detector, raster-ocr-labels, raster-cv, raster-area ...) has its own tuned
conditions for a proven answer. Those were tuned to accept an answer when it is right more often than
wrong, so a proven door or window count on a scan was right only about 70% of the time. Proven has to mean
"you can rely on it", so on top of the route's own conditions a scan answer stays proven only where dev
measured that the route's proven answers for this object (or for this object and this count) are right at
least MIN_PRECISION of the time over at least MIN_N distinct drawings. A route can also name a condition it
met (Answer.tier_cell, e.g. "clear" for a detector count whose views agree with no box near the threshold),
which dev measured as its own cell. Every other scan answer becomes an estimate whose reason quotes the
measured precision.

The table (scan_calibration.json, next to this file) is written by tools/calibrate_tiers.py from dev answers
only. This step can only lower a tier, never raise one; neither can the consistency checks (bpqa/checks.py).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .answer import estimate

TABLE = Path(__file__).with_name("scan_calibration.json")
MIN_PRECISION = 0.98
MIN_N = 100
ENABLED = True       # eval.py --no-calibration turns it off (to measure the routes' own tiers)

NAMES = {"toilet": "WC seat counts", "sink": "sink counts", "shower_or_bath": "shower or bath counts",
         "door": "door counts", "window": "window counts", "bedroom": "bedroom counts", "kitchen": "kitchen counts",
         "sauna": "sauna counts", "wc_room": "WC room counts", "bathroom": "bathroom counts",
         "balcony": "balcony counts", "storey": "storey counts"}
ROUTE_WORDS = {"raster-detector": ("detector", "check the numbered boxes"),
               "raster-ocr-labels": ("room-label", "check the numbered labels"),
               "raster-rooms": ("room detector and label", "check the numbered rooms and labels"),
               "raster-cv": ("panel", "check the numbered panels"),
               "raster-area": ("printed-area", "check the printed areas"),
               "raster-cv-fixtures": ("outline", "check the numbered outlines")}


@lru_cache(maxsize=2)
def _load(path: str) -> dict:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")).get("cells", {}) if p.exists() else {}


def cells() -> dict:
    return _load(str(TABLE))


def key(obj: str | None, measure: str | None) -> str:
    return (obj or "?") if measure in (None, "count") else f"{obj}:{measure}"


def _value_key(value) -> str | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return str(int(f)) if f.is_integer() else None


def _supports(c: dict | None, value) -> bool:
    if not c:
        return False
    if c.get("proven"):
        return True
    vk = _value_key(value)
    cv = (c.get("counts") or {}).get(vk) if vk is not None else None
    return bool(cv and cv.get("proven"))


def reason(route: str, obj: str | None, value, measure: str | None = "count", cell: str = "") -> str | None:
    """Why a proven scan answer must be an estimate; None when dev supports the proven tier, for the route and
    object, for that count, or (with `cell`, a condition the route met) for that condition and count."""
    if not ENABLED or not route.startswith("raster"):
        return None
    if route == "raster-area" and cell in ("printed-agree", "printed-summary"):
        return None                   # printed values cross-checked by an agreeing scale (see raster_area.py)
    base = f"{route}|{key(obj, measure)}"
    if cell and _supports(cells().get(f"{base}|{cell}"), value):
        return None
    c = cells().get(base)
    what, hint = ROUTE_WORDS.get(route, (route, "check the marked items"))
    name = NAMES.get(obj or "", f"{(obj or 'answer').replace('_', ' ')} answers")
    if not c:
        return f"no dev measurement of how often {what} {name} on scans are right; {hint}"
    if _supports(c, value):
        return None
    # wording: this is the precision of the counts the route itself was sure about, not the overall accuracy
    # (QA 14 Nov: "about 78% exact" read as the window accuracy, which is lower)
    return (f"even when sure, {what} {name} on scans were exact in only {c['precision']:.0%} of dev drawings "
            f"({c['correct']} of {c['n']}), below the {MIN_PRECISION:.0%} a proven answer needs; {hint}")


def precision(route: str, obj: str | None, measure: str | None = "count") -> float | None:
    """Dev precision of the route's proven answers for this object, if measured."""
    c = cells().get(f"{route}|{key(obj, measure)}")
    return c["precision"] if c else None


def apply(ans, obj: str | None, measure: str | None = "count"):
    """Lower a proven scan answer to an estimate unless dev supports it. Never raises a tier."""
    if ans.tier != "proven" or not ans.answered:
        return ans
    why = reason(ans.route or "", obj, ans.value, measure, getattr(ans, "tier_cell", ""))
    if why:
        estimate(ans, why)
    return ans


# ---------------------------------------------------------------- free-text / property lookups
# The lookups tried by router._try_text (ifc-props over the IFC model, pdf-text over PDF schedules, the numeric piping
# reader) read every value from the file, and used to be marked proven whenever they answered. On dev that was wrong
# often: ifc-props proven answers were right in 37 of 61 ifc_bench questions and in 0 of 16 ifc_models ones (14 Nov),
# because the value is read correctly but the lookup the question was matched to is not always what was asked. So
# they are held to the same standard as the scan routes: proven only for a kind of lookup (TEXT_TABLE, written by
# tools/calibrate_text_tiers.py from dev answers) measured at MIN_PRECISION over MIN_N distinct drawings. The kind is
# Answer.tier_cell, set by router._try_text: "text:<lookup>|<what was looked up>".
TEXT_TABLE = Path(__file__).with_name("text_calibration.json")
TEXT_WORDS = {"ifc_props": "property lookups in the IFC model", "pdf_text": "lookups in the PDF text layer",
              "piping": "piping lookups in the IFC model"}


def text_cells() -> dict:
    return _load(str(TEXT_TABLE))


def text_key(module: str, ans, query) -> str:
    """The kind of lookup an answer came from: the lookup's own intent and attribute when it names one (ifc-props),
    else the measure and object that were asked."""
    kind = ans.tier_cell or f"{query.measure or '?'}:{query.obj or '-'}"
    return f"text:{module}|{kind}"


def text_reason(cell: str) -> str | None:
    """Why a lookup answer must be an estimate; None when dev measured this kind of lookup as reliable."""
    if not ENABLED or not cell.startswith("text:"):
        return None
    c = text_cells().get(cell)
    module, kind = cell[5:].split("|", 1)
    what = TEXT_WORDS.get(module, "lookups")
    if c and c.get("proven"):
        return None
    if not c:
        return (f"each value is read from the file, but how often {what} of this kind ({kind}) answer the question "
                f"asked has not been measured on {MIN_N} or more dev drawings; check that the answer covers what was asked")
    return (f"each value is read from the file, but {what} of this kind ({kind}) answered the question asked in only "
            f"{c['precision']:.0%} of dev questions ({c['correct']} of {c['n']}, {c['drawings']} drawings), below the "
            f"{MIN_PRECISION:.0%} over {MIN_N}+ drawings a proven answer needs; check that the answer covers what was asked")
