"""Never proven when part of the question went unused (judge review 14 Nov, item 1).

A question carries constraints: a room number ("room 3.05", "Kontor 213", "MH 1"), a room name the drawing prints
("rooms named KORIDOR", "corridors" on a sheet whose rooms say KORIDOR), a group ("apartment A", "flat 3"), a storey
phrase on a model with several storeys ("on this floor", "Erdgeschoss"), a size filter ("wider than 0.9 m"), a fire
class ("EI60"), a user group ("for men"), or a room type next to an apartment ("the bedrooms in apartment B").

`detect(question)` lists what the question carries. `check(ctx, query, ans)` runs after a route answered: every
constraint must have been applied by the reading (the Query) or by the route (its explanation and items). One that
was not is first repaired where the data supports it (the room by its number, the spaces by the drawing's own label,
the rooms of one apartment on a Swiss plan); otherwise the answer is withdrawn with the reason, naming what was not
applied and what the unfiltered answer would have been. A wrong number marked Proven costs the trust in every other
Proven, and an Estimate of a number known to cover the wrong rooms is not an estimate.
"""
from __future__ import annotations

import copy
import re

from .answer import Answer, abstain
from .query import fold

UNIT_AFTER = r"\s*(?:mm|cm|m2|m²|m\^2|m3|m³|m\b|metres?|meters?|metri\w*|millimet\w*|senttimet\w*|sq|square|%|percent|prosent|neli\w*|kpl|pcs|x\b)"

GROUP = re.compile(r"\b(apartment|flat|unit|wohnung|whg\.?|huoneisto|asunto|korter|appartement|appartamento|"
                   r"lagenhet|lejlighed)\s+(?:no\.?\s*|nr\.?\s*|number\s+)?([A-Z]|\d{1,3})(?![\w.])")
STOREY = re.compile(r"\b(this|the same) (floor|storey|story|level|sheet|plan)\b|\bground floor\b|\b(basement|attic|"
                    r"top floor|upper floor)\b|\berdgeschoss\b|\bobergeschoss\b|\bdachgeschoss\b|\bkeller\b|"
                    r"\b\d{1,2}(st|nd|rd|th)\.? (floor|storey|level)\b|\b(first|second|third|fourth|fifth) (floor|storey|level)\b|"
                    r"\b(level|storey|floor) \d{1,2}\b|\btassa kerroksessa\b|\bkerroksessa\b|\bkorrusel\b|"
                    r"\bdieses? (geschoss|stockwerk|etage)\b|\bauf dieser etage\b")
COMPARE = re.compile(r"\b(wider|narrower|taller|higher|shorter|longer|larger|bigger|smaller|greater|thicker|thinner|"
                     r"more|less|over|under|above|below|at least|at most|exceeding|breiter|schmaler|hoher|grosser|"
                     r"kleiner|leveampi|leveampia|korkeampi|korkeampia|suurempi|suurempia|pienempi|pienempia|yli|alle)"
                     r"\s+(?:than\s+|als\s+|kuin\s+)?(\d+(?:[.,]\d+)?)" + UNIT_AFTER)
# "wider than 0.9", "leveämpi kuin 0.9": a comparison even without a unit, never a room number (QA 14 Nov, box round 2)
COMPARE_BARE = re.compile(r"\b(wider|narrower|taller|higher|shorter|longer|larger|bigger|smaller|greater|thicker|thinner|"
                          r"more|less|breiter|schmaler|hoher|grosser|kleiner|leveampi|leveampia|kapeampi|kapeampia|"
                          r"korkeampi|korkeampia|matalampi|matalampia|suurempi|suurempia|pienempi|pienempia)"
                          r"\s+(?:than|als|kuin)\s+(\d+(?:[.,]\d+)?)(?![\d.,])")
# steps of a stair are their own object: a count of IfcStair does not answer "how many steps" (QA 14 Nov, box round 2)
STEPS = re.compile(r"\bsteps?\b|\brisers?\b|\btreads?\b|\baskel\w*|\bporrasaskel\w*|\bportaan ?askel\w*|\bstufen?\b|"
                   r"\bsteigungen\b|\bauftritte?\b|\btrappsteg\w*|\bnousu(?:ja|t|a)?\b|\bmarches\b|\bgradini\b")
STEPS_ANSWERED = re.compile(r"riser|tread|NumberOf|steps", re.I)
FIRE_CLASS = re.compile(r"\b(R?EI|EW|E|T|F)\s?-?(\d{2,3})\b")
FIRE_CTX = re.compile(r"fire|palo|brand|feuer|rating|class|luokk|klasse|resist")
WHO = re.compile(r"\bfor (men|women|ladies|gentlemen|males|females|staff|employees|children|the disabled|disabled)\b|"
                 r"\b(miesten|naisten|herren|damen|herre|dame)\b")
WHO_WORDS = {"men": r"herre|herren|\bmen\b|male|miehet|miesten|mand", "women": r"dame|damen|women|female|naiset|naisten|kvinde",
             "ladies": r"dame|damen|ladies|women", "gentlemen": r"herre|herren|gentlemen|\bmen\b"}
# a measure asked for: "how many square meters" starts like a count and is not one (QA 14 Nov: glazing area 11)
MEASURE_RX = [
    ("volume", re.compile(r"cubic met\w*|\bm3\b|m³|kuutio\w*|kubikmet\w*|\bvolume|\bvolumen|\bvolym|tilavuu\w*")),
    ("area", re.compile(r"square met\w*|\bm2\b|m²|\bsq\.? ?m\b|\bsqm\b|nelio\w*|quadratmet\w*|\barea\b|flache|pinta-ala")),
    ("length", re.compile(r"\blength|linear met\w*|running met\w*|\b(metres|meters) of\b|juoksumet\w*|\bpituu\w*")),
]
UNITS_OF = {"area": ("m2", "sq ft"), "length": ("m",), "volume": ("m3",)}
# several things asked at once ("the total number of openings, and how many are doors versus windows", "the total
# tiling, and what is the breakdown by bathroom"): one number answers one part at most (ifc_bench dev, 14 Nov)
PARTS = re.compile(r"(?:,|\band\b)\s+(?:how many|how much|what (?:is|are)|which|where (?:is|are))\b|\bversus\b|\bvs\.? |\bbreakdown\b|"
                   r"\brespectively\b")
PART_NOUN = re.compile(r"\b(?:number of|areas?|lengths?|volumes?|heights?|widths?|counts?)\b")
# "which sanitary furniture is present in room 110": asks for things by name, never for a number
WHICH = re.compile(r"^\s*(?:which|what (?:kind|kinds|type|types|sort) of)\b")
# room numbers: "room 3.05", "kitchen 3.14", "Kontor 213", "room 205", "MH 9.7", "MH 1"
ROOM_NO = re.compile(r"(?<![\w.])(\d{1,2}\.\d{1,2})(?![\w.])(?!" + UNIT_AFTER + r")")
ROOM_WORD_NO = re.compile(r"\b(room|rum|raum|zimmer|huone|ruum|piece|nr\.?|no\.?|number|space)\s+(\d{2,4})(?![\w.])", re.I)
WORD_NO = re.compile(r"\b([A-Za-zÄÖÜäöüÅå]{3,})\s+(\d{3,4})(?![\w.])")
ABBR_NO = re.compile(r"(?<![\w])([A-ZÄÖÜ]{2,4})\s?(\d{1,2}(?:\.\d)?)(?![\w.])")
# "makuuhuone 1", "bedroom 2", "living room 1": a room type with a small number names one room. Finnish scans print it
# as "MH 1" / "OH 1", so those types are looked up by that label (his typo'd question summed every bedroom, 14 Nov)
TYPE_NO = re.compile(r"\b(makuuhuone\w*|bedroom|olohuone\w*|living ?room|schlafzimmer|sovrum|kitchen|keitti[oö]\w*|"
                     r"bathroom|kylpyhuone\w*|office|toimisto\w*)\s+(\d{1,2})(?![\w.,])(?!\s*(?:m\b|m2|m²|%|cm|mm|kpl|x\b))", re.I)
TYPE_LABEL = {"makuuhuone": "MH", "bedroom": "MH", "olohuone": "OH", "living room": "OH", "livingroom": "OH"}
NAMED = re.compile(r"\b(?:named|called|labell?ed|nimelta\w*|genannt|bezeichnet)\s+['\"«„]?([\wÄÖÜäöüÅå][\w\-ÄÖÜäöüÅå ]{1,30}?)"
                   r"['\"»“]?(?=\s*(?:\?|$|,|\.|\band\b|\bon\b|\bin\b))", re.I)
NOT_ROOM_WORDS = {"floor", "level", "storey", "story", "apartment", "flat", "unit", "page", "sheet", "scale", "version",
                  "wohnung", "huoneisto", "asunto", "korter", "kerros", "korrus", "plan", "about", "approximately", "than",
                  "over", "under", "with", "waste", "height", "ceiling", "tiles", "tile"}
STOP = {"room", "rooms", "space", "spaces", "area", "areas", "total", "floor", "floors", "named", "called", "labelled",
        "labeled", "square", "metres", "meters", "tiling", "tiles", "order", "there", "these", "those", "which", "what",
        "where", "drawing", "sheet", "model", "storey", "level", "building", "apartment", "apartments", "doors", "windows",
        "walls", "slabs", "combined", "plan", "about", "should", "need", "needed", "many", "much", "their", "other",
        "areas", "toilet", "toilets", "office", "offices", "bathroom", "bathrooms", "bedroom", "bedrooms", "kitchen",
        "kitchens", "living", "storage", "balcony", "balconies", "corridor_"}


def _cog(w: str) -> str:
    """Spelling-tolerant form of a word for matching a question word with a printed room label across languages:
    'corridors' and 'KORIDOR' both give 'koridor'."""
    w = re.sub(r"[^a-z]", "", fold(w)).replace("c", "k").replace("ph", "f").replace("w", "v")
    w = re.sub(r"(.)\1+", r"\1", w)
    return re.sub(r"(es|s)$", "", w) if len(w) > 5 else w


def _cog_match(a: str, b: str) -> bool:
    a, b = _cog(a), _cog(b)
    if len(a) < 5 or len(b) < 5:
        return False
    return a == b or (a.startswith(b) or b.startswith(a)) and abs(len(a) - len(b)) <= 2


def detect(question: str) -> list[dict]:
    """Constraints the question carries, each {kind, value, phrase}."""
    from .query import strip_quoted
    out = []
    raw = re.sub(r"'[^']*'|\"[^\"]*\"|«[^»]*»|»[^«]*«|„[^“”]*[“”]|“[^”]*”|‘[^’]*’", " ", question)   # quoted names are
    f = fold(raw)                                                     # the rules' labels and levels, read already
    taken = []                          # spans already read as something else (a group, a storey, a size)

    def free(m):
        return not any(m.start() < e and s < m.end() for s, e in taken)
    for m in GROUP.finditer(raw if re.search(r"\b[A-Z]\b|\d", raw) else raw):
        if m.group(2).isalpha() and not m.group(2).isupper():
            continue
        out.append({"kind": "group", "value": m.group(2).upper(), "phrase": m.group(0)})
        taken.append(m.span())
    for m in COMPARE.finditer(f):
        out.append({"kind": "compare", "value": m.group(2).replace(",", "."), "phrase": m.group(0).strip()})
        taken.append(m.span())
    for m in COMPARE_BARE.finditer(f):
        if free(m):
            out.append({"kind": "compare", "value": m.group(2).replace(",", "."), "phrase": m.group(0).strip()})
            taken.append(m.span())
    m = STEPS.search(f)
    if m:
        out.append({"kind": "steps", "value": m.group(0), "phrase": m.group(0)})
    for m in STOREY.finditer(f):
        out.append({"kind": "storey", "value": m.group(0), "phrase": m.group(0)})
        taken.append(m.span())
    if FIRE_CTX.search(f):
        for m in FIRE_CLASS.finditer(raw):
            out.append({"kind": "fire_class", "value": (m.group(1) + m.group(2)).upper(), "phrase": m.group(0)})
            taken.append(m.span())
    m = WHO.search(f)
    if m:
        out.append({"kind": "who", "value": (m.group(1) or m.group(2)).replace("the ", ""), "phrase": m.group(0)})
    nos = []
    stripped = strip_quoted(raw) if "'" in raw or '"' in raw else raw
    off = 0 if stripped == raw else None
    src = raw if off == 0 else stripped
    for rx, g in ((ROOM_NO, 1), (ROOM_WORD_NO, 2), (WORD_NO, 2), (ABBR_NO, 0)):
        for m in rx.finditer(src):
            if off == 0 and not free(m):
                continue
            if rx is WORD_NO and m.group(1).lower() in NOT_ROOM_WORDS:
                continue
            if rx is ABBR_NO and (FIRE_CLASS.fullmatch(m.group(0).replace(" ", "")) or m.group(1) in ("EI", "REI", "WC")
                                  and not re.search(r"\bwc\s?\d\b", m.group(0), re.I)):
                continue
            if rx is ABBR_NO and m.group(1).upper() in ("M", "MM", "CM", "SQ", "KPL"):
                continue
            pre = fold(src[max(0, m.start() - 12):m.start()])
            if re.search(r"(level|floor|storey|kerros|korrus|etage|geschoss|apartment|flat|wohnung|unit)\s*$", pre):
                continue
            val = m.group(g) if g else m.group(0)
            if any(val in n["value"] or n["value"] in val for n in nos):
                continue
            nos.append({"kind": "room_no", "value": val, "phrase": m.group(0), "word": m.group(1) if rx is not ROOM_NO else ""})
    for m in TYPE_NO.finditer(src):
        if off == 0 and not free(m):
            continue
        w = fold(m.group(1)).lower()
        base = next((k for k in TYPE_LABEL if w.startswith(k)), None)
        val = f"{TYPE_LABEL[base]} {m.group(2)}" if base else m.group(0)
        if any(m.group(2) == n["value"].split()[-1] for n in nos):
            continue
        nos.append({"kind": "room_no", "value": val, "phrase": m.group(0), "word": "label"})
    out += nos
    for m in NAMED.finditer(question):
        out.append({"kind": "named", "value": m.group(1).strip(), "phrase": m.group(0)})
    m = next((m for m in PARTS.finditer(f) if not m.group(0).startswith((",", "and"))
              or re.search(r"\b(how|what|which)\b", f[:m.start()])), None)   # "On sheet A-801, how many": one ask
    if m or ("," in f and len(PART_NOUN.findall(f)) >= 3):
        out.append({"kind": "parts", "value": "", "phrase": m.group(0).strip(" ,") if m else "a list of quantities"})
    m = WHICH.search(f)
    if m and not any(rx.search(f) for _, rx in MEASURE_RX) and not re.search(r"\bhow many\b", f):
        out.append({"kind": "which", "value": "", "phrase": m.group(0).strip()})
    if not any(c["kind"] == "compare" for c in out):
        for meas, rx in MEASURE_RX:
            mm = rx.search(f)
            if mm:
                out.append({"kind": "measure", "value": meas, "phrase": mm.group(0)})
                break
    return out


# ---------------------------------------------------------------- was it applied?

def _room_phrase(c) -> str:
    p = c["phrase"].strip()
    return p if re.match(r"(?i)(room|rum|raum|zimmer|huone|ruum|space)\b", p) else f"room {p}"


def _text(ans: Answer) -> str:
    return f"{ans.explanation or ''} {ans.tier_reason or ''}"


def _norm(s) -> str:
    return re.sub(r"[\s.\-]", "", fold(str(s or "")))


def _ifc_scope(ctx, query):
    """(model, storey or None) for an IFC-backed drawing, else (None, None)."""
    try:
        path = ctx.model_ifc()
        if path is None:
            return None, None
        from .routes import ifc_route as R
        M = R.load(str(path))
        if query.level:
            return M, M.find_storey(query.level)
        if ctx.q.ext in ("pdf", "svg"):
            st, _ = R.sheet_storey(M, ctx)
            return M, st
        return M, None
    except Exception:
        return None, None


def _ifc_spaces_matching(M, storey, word: str, exact: bool):
    from .routes import ifc_route as R
    out = []
    for sp in M.entities("room"):
        if storey is not None and M.storey_of(sp) != storey:
            continue
        labs = [getattr(sp, "LongName", None) or "", sp.Name or ""]
        if exact:
            w = _norm(word)
            if any(_norm(x) == w or re.sub(r"\(.*?\)", "", fold(x)).strip() == fold(word).strip() for x in labs if x) \
                    or w in {_norm(v) for v in R.space_label_variants(sp)}:
                out.append(sp)
        elif any(_cog_match(t, word) for x in labs for t in re.findall(r"[^\W\d_]{4,}", x)):
            out.append(sp)
    return out


def _guids(ans: Answer) -> set:
    return {it.get("guid") for it in ans.items if isinstance(it, dict) and it.get("guid")}


def unused(ctx, query, ans: Answer, cons: list[dict] | None = None) -> list[dict]:
    """The constraints the reading and the route did not apply, each with a `why`."""
    cons = detect(ctx.q.question) if cons is None else cons
    text = _text(ans)
    tn = _norm(text)
    out = []
    multi = None
    for c in cons:
        k, v = c["kind"], c["value"]
        if k == "room_no":
            lab = _norm(query.label)
            if (query.room_no and _norm(query.room_no) == _norm(v)) or (lab and _norm(v) in lab):
                continue
            items = [it for it in ans.items if isinstance(it, dict) and ("number" in it or "room" in it or "name" in it)]
            if items and all(_norm(v) in _norm(it.get("number")) + _norm(it.get("room")) + _norm(it.get("name"))
                             for it in items):
                continue
            out.append({**c, "why": f"the question names {_room_phrase(c)}, and the answer was not limited to it"})
        elif k == "group":
            if (query.apartment or "").upper() == v or query.extra.get("group_applied") == v:
                if query.obj == "apartment" and query.measure == "area" and _types_asked(ctx.q.question):
                    out.append({"kind": "room_type", "value": v, "phrase": c["phrase"],
                                "why": f"the question asks for {', '.join(_types_asked(ctx.q.question))} rooms in "
                                       f"{c['phrase']}, and the answer covers every room of it"})
                continue
            named = {x.upper() for x in re.findall(r"\b(?:apartment|flat|unit|whg\.?|wohnung|huoneisto|asunto|korter)\s*"
                                                  r"([A-Z]|\d{1,3})\b", text, re.I)}
            if named == {v}:
                continue
            out.append({**c, "why": f"the question asks about {c['phrase']}, and the answer was not limited to it"})
        elif k == "storey":
            if multi is None:
                M, _ = _ifc_scope(ctx, query)
                multi = M is not None and len([s for s in M.storeys]) > 1
            if not multi or query.level or query.extra.get("scope_resolved") and re.search(r"storey|sheet", text):
                continue
            if re.search(r"\bon storey\b|sheet shows storey|storey [^,;]+ \(|, storey \S|\bstorey \S+:", text):
                continue
            out.append({**c, "why": f"the question says '{c['phrase']}', and the answer covers the whole model, not one storey"})
        elif k == "compare":
            if re.search(re.escape(v) + r"\s*(m|mm|cm|m2)?", text) and re.search(r"wider|narrower|taller|higher|larger|"
                                                                               r"smaller|more than|less than|over|under|>|<|at least", text):
                continue
            out.append({**c, "why": f"the question filters by '{c['phrase']}', and the answer did not apply that filter"})
        elif k == "steps":
            if ans.unit != "count" or STEPS_ANSWERED.search(text):
                continue
            out.append({**c, "why": f"the question asks for the steps of a stair ('{c['phrase']}'), and the answer "
                                    f"counts other objects (stairs, not their steps)"})
        elif k == "fire_class":
            if _norm(v) in tn:
                continue
            got = re.findall(r"\b(?:R?EI|EW)\s?\d{2,3}(?:\s?S\d+)?", text)
            out.append({**c, "why": f"the question asks for fire class {v}, and the answer did not filter on it"
                               + (f" (it counts {', '.join(sorted(set(got)))})" if got else "")})
        elif k == "who":
            rx = WHO_WORDS.get(v, re.escape(v))
            if re.search(rx, fold(text)):
                continue
            out.append({**c, "why": f"the question asks only for rooms '{c['phrase']}', and the answer did not apply that"})
        elif k == "measure":
            other = next((m for m, units in UNITS_OF.items() if m != v and ans.unit in units), None)
            if other:       # "the total length of skirting boards" answered with a floor area (ifc_bench dev, 14 Nov)
                out.append({**c, "why": f"the question asks for {v} ('{c['phrase']}'), and the answer is {'an' if other == 'area' else 'a'} {other}"})
                continue
            if ans.unit not in ("count", "rooms"):
                continue
            out.append({**c, "why": f"the question asks for {v} ('{c['phrase']}'), and the answer is a count"})
        elif k in ("parts", "which"):
            if not isinstance(ans.value, (int, float)) or isinstance(ans.value, bool):
                continue        # a lookup's text answer can list several things
            out.append({**c, "why": ("the question asks for several things at once ('" + c["phrase"] + "'), and the "
                                     "answer is one number, for one part at most; ask for each part separately")
                        if k == "parts" else
                        f"the question asks '{c['phrase']} ...' (things by name), and the answer is a number"})
        elif k == "named":
            if query.label and _norm(query.label) == _norm(v):
                continue
            M, st = _ifc_scope(ctx, query)
            if M is not None and ans.route == "ifc":
                want = {sp.GlobalId for sp in _ifc_spaces_matching(M, st, v, exact=True)}
                if want and _guids(ans) == want:
                    continue
            out.append({**c, "why": f"the question asks for rooms named {v}, and the answer was not limited to them"})
    # the drawing's own labels: a question word that is a printed space name ("corridors" -> KORIDOR) must be
    # answered with those spaces, before any room-type dictionary
    # (only for a sum over rooms read without a name: a quoted or named room is the label already)
    if ans.route == "ifc" and query.obj == "room" and not any(c["kind"] in ("named", "room_no") for c in cons) \
            and not query.label and not query.room_no and query.variant in ("type_sum", "total") \
            and not query.extra.get("single_room") and not re.search(r"['\"«„“]", ctx.q.question):
        M, st = _ifc_scope(ctx, query)
        if M is not None:
            got = _guids(ans)
            for w in sorted(set(re.findall(r"[^\W\d_]{5,}", fold(ctx.q.question))) - STOP):
                sps = _ifc_spaces_matching(M, st, w, exact=False)
                if sps and not {sp.GlobalId for sp in sps} <= got:
                    out.append({"kind": "label", "value": w, "phrase": w, "spaces": sps,
                                "why": f"the drawing labels {len(sps)} space(s) as "
                                       f"{', '.join(sorted({getattr(s, 'LongName', None) or s.Name for s in sps})[:4])}, "
                                       f"matching '{w}', and the answer did not use them all"})
                    break
    return out


def _types_asked(question: str) -> list[str]:
    from . import takeoff
    f = takeoff.EXCLUDE.sub(" ", fold(question))      # "all its rooms except balconies" asks for no balcony
    return [t for t, rx in takeoff._Q_TYPE_RE.items() if rx.search(f) and t not in takeoff.GROUPS]


# ---------------------------------------------------------------- repairs where the data supports them

def _repair(ctx, query, ans, miss, resolve):
    kinds = {m["kind"] for m in miss}
    # one room by its number (Swiss and vector plans read room numbers; the label reader on scans reads "MH 1")
    if kinds == {"room_no"} and query.measure in ("area", None) or kinds == {"room_no"} and query.obj == "room" and query.measure != "count":
        m = miss[0]
        q2 = copy.deepcopy(query)
        q2.measure, q2.obj, q2.variant = "area", "room", None
        q2.extra.pop("takeoff", None)
        q2.extra["single_room"] = True
        val = m["value"]
        if re.fullmatch(r"\d+(\.\d+)?", val) and not m.get("word") or m.get("word", "").lower() in ("room", "rum", "raum", "zimmer", "huone", "ruum", "piece", "nr", "no", "number", "space"):
            q2.room_no, q2.label = val, None
        else:
            q2.label, q2.room_no = val, None
            q2.extra["label_from_text"] = True
        a2 = _ifc_room(ctx, q2, m) if ctx.model_ifc() is not None else None
        if a2 is None:
            q2r, a2 = resolve(q2)
            if a2.answered and not unused(ctx, q2r, a2, [m]):
                return q2r, a2
            if q2.room_no and m.get("word"):            # "kitchen 3.14": the number, read as the room's number
                pass
            return None
        return (q2, a2) if a2.answered or a2.final else None
    if kinds == {"measure"} and query.obj:
        q2 = copy.deepcopy(query)
        q2.measure = miss[0]["value"]
        if q2.obj == "wall" and q2.measure == "area":
            q2.variant = "side_area"
        q2r, a2 = resolve(q2)
        if a2.answered and a2.unit not in ("count", "rooms") and not unused(ctx, q2r, a2, [miss[0]]):
            return q2r, a2
        return None
    if kinds <= {"label", "named"} and query.measure == "area" and ans.route == "ifc":
        a2 = _ifc_label_sum(ctx, query, miss[0])
        return (query, a2) if a2 is not None else None
    if kinds <= {"group", "room_type"} and ctx.sibling("dxf") is not None:
        a2 = _swiss_group(ctx, query, miss[0])
        return (query, a2) if a2 is not None else None
    return None


def _ifc_room(ctx, q2, m):
    """One IfcSpace by number or name on the sheet's storey: its area, or a final abstention naming the rooms there."""
    from .routes import ifc_route as R
    M, st = _ifc_scope(ctx, q2)
    if M is None:
        return None
    val = m["value"]
    word = m.get("word") or ""
    sps = []
    for sp in M.entities("room"):
        if st is not None and M.storey_of(sp) != st:
            continue
        labs = " ".join(x for x in [getattr(sp, "LongName", None) or "", sp.Name or ""] if x)
        if re.search(rf"(?<![\d.]){re.escape(val)}(?![\d.])", labs) and (not word or word.lower() in ("room", "rum", "raum", "nr", "no", "number", "space")
                                                                         or _cog_match(word, labs) or fold(word) in fold(labs)):
            sps.append(sp)
    where = f"on storey {st.Name}" if st is not None else "in the model"
    if len(sps) != 1:
        a = abstain(R.ROUTE, f"not answered: the question names {_room_phrase(m)}, and "
                             f"{'no space' if not sps else f'{len(sps)} spaces'} {where} match it; the answer for all "
                             f"rooms would be wrong")
        a.final = True
        return a
    if q2.measure != "area":
        return None
    sp = sps[0]
    area, src = M.space_area(sp)
    if area is None:
        return None
    return Answer(value=round(area, 2), unit="m2", route=R.ROUTE, confidence=0.9, tier="proven",
                  items=[{"guid": sp.GlobalId, "name": R.space_display(sp), "value": round(area, 2)}],
                  explanation=f"{area:.2f} m2 = IfcSpace {R.space_display(sp)} {where} ({src}), the room numbered "
                              f"{val} in the question. Source: {ctx.model_ifc().name}.")


def _ifc_label_sum(ctx, query, m):
    """The spaces the drawing itself labels with the asked name, on the sheet's storey: their summed area. A takeoff
    that needs wall areas is not repaired here."""
    from . import takeoff
    from .routes import ifc_route as R
    tk = query.extra.get("takeoff")
    if tk and (tk.get("surface") != "floor" or tk.get("baseboard")):
        return None
    M, st = _ifc_scope(ctx, query)
    if M is None:
        return None
    sps = m.get("spaces") or _ifc_spaces_matching(M, st, m["value"], exact=True)
    where = f"on storey {st.Name}" if st is not None else "in the model"
    if not sps:
        names = sorted({getattr(s, "LongName", None) or s.Name or "" for s in M.entities("room")
                        if st is None or M.storey_of(s) == st} - {""})
        a = abstain(R.ROUTE, f"not answered: no space named {m['value']} {where} (space names: {', '.join(names[:15])})")
        a.final = True
        return a
    rows = []
    for sp in sps:
        a, src = M.space_area(sp)
        if a is None:
            return None
        rows.append({"label": R.space_display(sp), "area": a, "number": sp.Name, "source": f"IfcSpace {src}", "guid": sp.GlobalId})
    labs = sorted({getattr(s, "LongName", None) or s.Name for s in sps})
    head = f"Rooms the drawing labels {', '.join(labs)} {where}, matched to '{m['value']}' in the question."
    if tk:
        tk2 = dict(tk, types=sorted(set(tk.get("types") or []) | {"room"}))
        val, items, expl = takeoff.summary(rows, tk2)
    else:
        val = round(sum(r["area"] for r in rows), 2)
        items = [{"guid": r["guid"], "name": r["label"], "value": round(r["area"], 2)} for r in rows]
        expl = f"{val:.2f} m2 = " + " + ".join(f"{r['label']} {r['area']:.2f}" for r in rows) + "."
    for it, r in zip(items, rows):
        it["guid"] = r["guid"]
    return Answer(value=val, unit="m2", route=R.ROUTE, confidence=0.9, tier="proven", items=items,
                  explanation=f"{head} {expl} Source: {ctx.model_ifc().name}.")


def _swiss_group(ctx, query, m):
    """Rooms of one apartment on a Swiss plan (the DXF assigns each room to its apartment outline): a room-type count
    or a room-type floor area. Anything else (doors, baseboard, tiling with walls) is not repaired."""
    from . import takeoff
    from .routes import dxf_swiss
    dxf = ctx.sibling("dxf")
    try:
        if not dxf_swiss.swiss_scheme(str(dxf)):
            return None
        fl = dxf_swiss.load(str(dxf))
    except Exception:
        return None
    letter = m["value"]
    units = sorted({r["unit"] for r in fl.rooms if r.get("unit")})
    if letter not in units:
        a = abstain(dxf_swiss.ROUTE, f"not answered: apartment {letter} is not on this floor (apartments: {', '.join(units) or 'none'})")
        a.final = True
        return a
    mine = [r for r in fl.rooms if r.get("unit") == letter]
    if query.measure == "count" and query.obj in takeoff.COUNT_TYPES:
        rt = takeoff.count_room_type(mine, query.obj, fixtures=lambda r: dxf_swiss.swiss_room_fixtures(fl, r))
        if rt["value"] is None:
            return None
        ans = Answer(value=rt["value"], unit="count", route=dxf_swiss.ROUTE, confidence=0.95, tier="proven",
                     items=[{"number": r["number"], "name": r["name"], "unit": letter} for r in rt["rooms"]],
                     explanation=f"{rt['value']} = {rt['note']} inside apartment {letter} in {dxf.name}")
        if rt.get("estimate"):
            ans.tier, ans.tier_reason = "estimate", rt["estimate"]
        return ans
    types = (query.extra.get("takeoff") or {}).get("types") or _types_asked(ctx.q.question)
    tk = query.extra.get("takeoff")
    if query.measure == "area" and types and not (tk and (tk.get("surface") != "floor" or tk.get("baseboard"))):
        rooms = [r for r in mine if takeoff.room_types(r["name"]) & set(types)]
        if not rooms:
            names = sorted({r["name"] for r in mine if r["name"]})
            a = abstain(dxf_swiss.ROUTE, f"not answered: no {', '.join(types)} room in apartment {letter} (rooms: {', '.join(names)})")
            a.final = True
            return a
        rows = [{"label": r["name"] or "room", "number": r["number"], "area": fl.room_area(r), "source": "ROOM_OUTLINE polygon"}
                for r in rooms]
        tk2 = dict(tk or {"types": types, "surface": "floor", "aggregation": "sum", "material": None, "group": None})
        val, items, expl = takeoff.summary(rows, tk2)
        return Answer(value=val, unit="m2", route=dxf_swiss.ROUTE, confidence=0.95, tier="proven", items=items,
                      explanation=f"Rooms inside apartment {letter} only. {expl} Source: {dxf.name}.")
    return None


# ---------------------------------------------------------------- the guard

def check(ctx, query, ans: Answer, resolve) -> tuple:
    """(query, answer) after the guard. `resolve(q)` solves the same drawing with another reading."""
    if not ans.answered:
        return query, ans
    try:
        cons = detect(ctx.q.question)
        if not cons and not (ans.route == "ifc" and query.obj == "room"):
            return query, ans
        miss = unused(ctx, query, ans, cons)
    except Exception:
        return query, ans
    if not miss:
        return query, ans
    try:
        fixed = _repair(ctx, query, ans, miss, resolve)
    except Exception:
        fixed = None
    if (fixed is not None and fixed[1].answered and fixed[1].unit in UNITS_OF["area"] and ans.unit not in UNITS_OF["area"]
            and query.measure != "area" and not MEASURE_RX[1][1].search(fold(ctx.q.question))):
        # a repair that turns the question into a room area when no area was asked: "which sanitary furniture is in
        # room 110" answered 4.07 m2, marked proven (ifc_bench dev, 14 Nov)
        fixed = None
    if fixed is not None:
        q2, a2 = fixed
        if a2.answered:
            a2.explanation = f"{a2.explanation} (Read from the question: {miss[0]['phrase'].strip()}.)"
        return q2, a2
    shown = f"{ans.value:g}" if isinstance(ans.value, (int, float)) else str(ans.value)
    a = abstain(ans.route, "not answered: " + "; ".join(m["why"] for m in miss)
                + f". Without it the answer would be {shown} {ans.unit or ''}".rstrip() + ", which does not answer this question")
    a.final = True
    a.items = [{"unapplied": [m["phrase"].strip() for m in miss], "would_answer": ans.value}]
    return query, a
