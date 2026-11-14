"""Checking layer for raster answers: consistency checks across routes before an answer is returned.

Every raster route sees one signal (room labels, drawing panels, a detector). The plan as a whole
carries more: an apartment summary ("3H+K+S"), numbered labels ("MH3"), floor names ("2. KRS"),
labels of rooms that imply fixtures. Before a raster answer leaves the router, the facts every route
can read off the same image are gathered once, and each check that relates the asked object to them
is evaluated. A check is a bound or an expected value, with its own provenance:

    pass   the answer agrees with the other signal
    fail   it does not; the check's action decides: "abstain" (dev showed answers failing this
           check are mostly wrong) or "lower" (confidence is cut, the answer stays)

Checks that have nothing to compare against are not recorded. The fired checks go into the answer
JSON (`checks`) and into the evidence header. Actions were set from the dev split only
(tools/check_eval.py).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

import cv2

from .routes.labels import ROOM_PATTERNS, dedupe, find_rooms, is_word, norm, quad_box
from .routes.ocr import ocr_image

ROOM_TYPES = ("bedroom", "kitchen", "sauna", "wc_room", "bathroom", "balcony")


@dataclass
class Check:
    name: str
    status: str          # pass | fail
    detail: str          # what was compared, in words
    action: str = ""     # on fail: abstain | lower (how the check was set before tiers; see apply)
    suggest: int | None = None   # the value the other signal implies (the bound, or the expected count)

    def as_dict(self):
        return {k: v for k, v in asdict(self).items() if v}


# ---------------------------------------------------------------- facts read off the image

SUMMARY_RE = re.compile(r"(?:^|[^\d.,])(\d)\s*H\s*(?=[+,])")
SUMMARY_BARE_RE = re.compile(r"^\s*H\s*\+")
KITCHEN_PARTS = {"K", "KK", "KT", "TK", "TUPAK", "AVOK", "KEITTIO"}
SAUNA_PARTS = {"S", "SAUNA"}
FLOOR_NO_RE = re.compile(r"(?:^|[^\d])([1-4])\s*\.?\s*(?:KRS|KERROS(?!ALA))")
STAIRS_RE = re.compile(r"PORRAS|PORTAAT|\bYLOS\b|\bALAS\b|TRAPPA")
FLOOR_WORDS = {"ALAKERTA": "lower", "YLAKERTA": "upper", "KELLARI": "basement", "POHJAKERROS": "ground"}


def parse_summary(text: str):
    """'3H+K+S' -> {'rooms': 3, 'kitchen': True, 'sauna': True}; None when the token is not one."""
    t = norm(text)
    m = SUMMARY_RE.search(t)
    if m:
        n, rest = int(m.group(1)), t[m.end():]
    elif SUMMARY_BARE_RE.match(t):
        n, rest = 1, t[SUMMARY_BARE_RE.match(t).end():]
    else:
        return None
    parts = [re.sub(r"[\d.,=M?]+$", "", p) for p in re.split(r"[+,\s()]+", rest) if p]
    return {"rooms": n, "kitchen": any(p in KITCHEN_PARTS for p in parts),
            "sauna": any(p in SAUNA_PARTS for p in parts), "text": text}


@lru_cache(maxsize=64)
def facts(file: str) -> dict:
    """Everything the raster routes can read from one image, computed once per image."""
    f = Path(file)
    toks = ocr_image(f, allow_run=True) or []
    d = dedupe(toks)
    out = {"labels": {}, "numbered": {}, "words": 0, "summaries": [], "floors": set(), "stairs": "", "panels": None}
    for o in ROOM_TYPES:
        hits = find_rooms(toks, o)
        out["labels"][o] = [h["text"] for h in hits]
        nums = [int(m.group(1)) for h in hits for m in [re.fullmatch(r"[A-Z]+(\d)", h["part"])] if m]
        out["numbered"][o] = max(nums) if nums else 0
    out["words"] = sum(is_word(t["text"]) for t in d)
    sums = {}
    for t in d:
        s = parse_summary(t["text"])
        if s:
            sums[(s["rooms"], s["kitchen"], s["sauna"])] = s
        tx = norm(t["text"])
        for m in FLOOR_NO_RE.finditer(tx):
            out["floors"].add("floor " + m.group(1))
        for w, name in FLOOR_WORDS.items():
            if w in tx.replace(" ", ""):
                out["floors"].add(name)
        m = STAIRS_RE.search(tx)
        if m and not out["stairs"]:
            out["stairs"] = m.group(0)
    out["summaries"] = list(sums.values())
    img = cv2.imread(str(f))
    if img is not None:
        from .routes.raster import panels
        out["panels"] = len(panels(img))
    return out


# ---------------------------------------------------------------- the checks

def _labels(F, o):
    return len(F["labels"].get(o, []))


def check_numbered(F, obj, v):
    """A label 'MH3' means the plan numbers at least three of them."""
    k = F["numbered"].get(obj, 0)
    if k >= 2:
        ok = v >= k
        return Check("numbered-labels", "pass" if ok else "fail",
                     f"a label numbered {k} was read, so at least {k}; answer {v}", "abstain", k)


def check_summary_bedrooms(F, obj, v):
    """'nH+K' names n rooms: the living room plus n-1 bedrooms."""
    if obj != "bedroom" or len(F["summaries"]) != 1:
        return None
    s = F["summaries"][0]
    exp = s["rooms"] - 1
    if exp < 1:
        return None
    return Check("summary-rooms", "pass" if v == exp else "fail",
                 f"apartment summary '{s['text']}' implies {exp} bedroom(s); answer {v}", "abstain", exp)


def check_summary_kitchen(F, obj, v):
    if obj != "kitchen" or len(F["summaries"]) != 1 or not F["summaries"][0]["kitchen"]:
        return None
    s = F["summaries"][0]
    return Check("summary-kitchen", "pass" if v >= 1 else "fail",
                 f"apartment summary '{s['text']}' lists a kitchen; answer {v}", "abstain", 1)


def check_summary_sauna(F, obj, v):
    """Only one direction: a summary that lists S means a sauna. Summaries often leave S out
    ('3H+K 80 m2' on a plan with a sauna), so a missing S proves nothing."""
    if obj != "sauna" or len(F["summaries"]) != 1 or not F["summaries"][0]["sauna"]:
        return None
    s = F["summaries"][0]
    return Check("summary-sauna", "pass" if v >= 1 else "fail",
                 f"apartment summary '{s['text']}' lists a sauna; answer {v}", "abstain", 1)


def check_utility_kitchen(F, obj, v):
    """A utility kitchen (APUK) is a second kitchen: reading only that one means the main one was missed."""
    if obj != "kitchen":
        return None
    labs = [norm(t) for t in F["labels"]["kitchen"]]
    if not labs:
        return None
    only_aux = all(re.search(r"APUK|KOKSVRA", t) for t in labs)
    if not only_aux:
        return None
    return Check("utility-kitchen", "pass" if v >= 2 else "fail",
                 f"only a utility kitchen label was read ({', '.join(F['labels']['kitchen'])}); "
                 f"a main kitchen is implied; answer {v}", "abstain", 2)


def check_floor_names(F, obj, v):
    """Distinct floor names on the sheet ('1. KRS', '2. KRS', ALAKERTA ...) imply that many storeys."""
    if obj != "storey" or len(F["floors"]) < 2:
        return None
    k = len(F["floors"])
    return Check("floor-names", "pass" if v >= k else "fail",
                 f"{k} floor names read ({', '.join(sorted(F['floors']))}); answer {v}", "abstain", k)


def check_stairs(F, obj, v):
    """A stair label (PORRAS, YLOS/ALAS arrows) on a single-panel plan means a second level is drawn."""
    if obj != "storey" or not F["stairs"]:
        return None
    return Check("stairs-label", "pass" if v >= 2 else "fail",
                 f"stair label read ({F['stairs']}), so at least 2 storeys; answer {v}", "abstain", 2)


def check_fixture_bounds(F, obj, v):
    """Fixtures implied by room labels: every WC room has a WC seat; every bathroom and every kitchen
    has a basin or sink; every bathroom has a shower or bath; n rooms need at least n-1 doors;
    every bedroom and the living room have a window."""
    out = []
    wc, kh, k, mh = _labels(F, "wc_room"), _labels(F, "bathroom"), min(_labels(F, "kitchen"), 1), _labels(F, "bedroom")
    if obj == "toilet" and wc:
        out.append(Check("toilets>=wc-rooms", "pass" if v >= wc else "fail",
                         f"{wc} WC room label(s) read, each holds a WC seat; answer {v}", "abstain", wc))
    if obj == "sink" and (kh or k):
        lo = kh + k
        out.append(Check("sinks>=bathrooms+kitchen", "pass" if v >= lo else "fail",
                         f"{kh} bathroom and {k} kitchen label(s) read, each implies a sink; answer {v}", "lower", lo))
    if obj == "shower_or_bath" and kh:
        out.append(Check("showers>=bathrooms", "pass" if v >= kh else "fail",
                         f"{kh} bathroom label(s) read, each implies a shower or bath; answer {v}", "abstain", kh))
    if obj == "door" and F["words"] >= 3:
        lo = F["words"] - 1
        out.append(Check("doors>=rooms-1", "pass" if v >= lo else "fail",
                         f"{F['words']} room labels read, so at least {lo} doors connect them; answer {v}", "lower", lo))
    if obj == "window" and mh:
        out.append(Check("windows>=bedrooms+1", "pass" if v >= mh + 1 else "fail",
                         f"{mh} bedroom label(s) read, each plus the living room needs a window; answer {v}", "abstain",
                         mh + 1))
    return out


def detected_count(file, obj):
    """The raster-detector's count for obj on this image (its tuned policy), or None without a model."""
    from .routes import detector
    c = detector.config(obj)
    if not c:
        return None
    model = detector.REPO / "models" / c["model"]
    views = detector.detect_views(Path(file), model=model, imgsz=c["imgsz"], tile=c["tile"], tta=c["tta"])
    if views is None:
        return None
    return detector.count_views(views, detector.combine(views), obj, c)[0]


def check_bathroom_from_showers(F, obj, v, file=None):
    """A shower or bathtub the detector finds stands in a bathroom: a zero bathroom count conflicts with it."""
    if obj != "bathroom" or v >= 1 or file is None:
        return None
    n = detected_count(file, "shower_or_bath")
    if not n:
        return None
    return Check("bathroom>=1-if-shower", "fail",
                 f"the detector counts {n} shower(s) or bathtub(s), each in a bathroom; answer {v}", "abstain", 1)


CHECKS = [check_numbered, check_summary_bedrooms, check_summary_kitchen, check_summary_sauna,
          check_utility_kitchen, check_floor_names, check_stairs, check_fixture_bounds]


def run(file: Path, obj: str, value) -> list[Check]:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return []
    F = facts(str(file))
    out = []
    for fn in CHECKS:
        r = fn(F, obj, v)
        out += r if isinstance(r, list) else ([r] if r else [])
    r = check_bathroom_from_showers(F, obj, v, file)
    if r:
        out.append(r)
    return out


LOWER_FACTOR = 0.5
# Checks whose implied value replaces the route's count when the check fails (the answer then becomes
# an estimate either way). Chosen on the dev split (answers.jsonl of a dev run, see CHANGELOG): only where the
# implied value was right more often than the route's own count on dev answers that failed the check.
ADOPT = {"numbered-labels", "summary-kitchen", "summary-sauna", "utility-kitchen", "floor-names", "stairs-label",
         "showers>=bathrooms", "bathroom>=1-if-shower"}
TIERS = True     # False: the pre-tier behaviour (a failed "abstain" check withdraws the answer)


def apply(ctx, query, ans):
    """Run the checks for a raster answer. A failed check makes it an estimate (and, for checks in ADOPT,
    replaces the count with the implied value); with TIERS off, the old abstain/lower actions apply."""
    if not ans.answered or not ans.route.startswith("raster") or query.measure != "count":
        return ans
    cs = run(ctx.file, query.obj, ans.value)
    if not cs:
        return ans
    ans.checks = [c.as_dict() for c in cs]
    fails = [c for c in cs if c.status == "fail"]
    if TIERS:
        if fails:
            ans.confidence = round(ans.confidence * LOWER_FACTOR, 2)
            from .answer import estimate
            estimate(ans, "consistency check failed: " + "; ".join(f"{c.name}: {c.detail}" for c in fails))
            adopt = [c for c in fails if c.name in ADOPT and c.suggest is not None]
            if adopt:
                old = ans.value
                ans.value = max(c.suggest for c in adopt) if all(c.suggest >= old for c in adopt) else adopt[0].suggest
                ans.explanation += f"; count changed from {old} to {ans.value} by check {adopt[0].name}"
                ans.items = [{"route_count": old}] + ans.items
        if ans.evidence:
            from .evidence import add_checks
            add_checks(ans.evidence, cs, value=ans.value, estimate=bool(fails))
        return ans
    if any(c.action == "abstain" for c in fails):
        why = "; ".join(f"{c.name}: {c.detail}" for c in fails)
        ans.abstain = f"checking layer: {why}"
        ans.explanation = f"would answer {ans.value}, but a consistency check failed"
    elif fails:
        ans.confidence = round(ans.confidence * LOWER_FACTOR, 2)
    if ans.evidence:
        from .evidence import add_checks
        add_checks(ans.evidence, cs, abstained=ans.abstain is not None, value=ans.value)
    if ans.abstain is not None:
        ans.items = [{"would_answer": ans.value}] + ans.items
        ans.value, ans.confidence = None, 0.0
    return ans
