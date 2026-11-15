"""Questions answered from several answers: lists, superlatives, conditions, shares, several parts, per apartment,
and the storey a sheet shows.

The routes answer one measurable shape each (a count, a room-type area sum, the total floor area). A judge asks
more: "list the bedrooms and their sizes", "smallest bathroom", "rooms over 20 m2", "what share of the floor is
bathrooms", "how many doors and windows", "bedroom area apartment by apartment", "which floor is this plan".
`plan()` recognises these (rules, no model) and `run()` answers them from ordinary route answers:

  list           the room-type area sum (or the total floor area) of the drawing, one row per room it used
  extreme        the same rows, the smallest or largest one ("which" asks for its name too)
  condition      the rows over or under an area ("how many rooms are over 20 m2" counts them)
  share          a room-type area sum over the total floor area, in percent
  per_apartment  the rows grouped by the apartment the route assigned them to (Swiss DXF outlines, IFC numbering)
  parts          "doors and windows": each part asked as its own question, one row per part with its own status
  which_storey   the storey of a plan sheet (IFC sheets), or of the model's storeys

Every row keeps the route's own item (its position on the drawing, its source), so the overlay and the items list
work as for any answer. Nothing is computed that the base answers do not carry: when the base cannot answer, the
composite says why and does not answer either.
"""
from __future__ import annotations

import re

from .answer import Answer, abstain
from .query import Query, RuleParser, fold, quotes, strip_quoted

# ----------------------------------------------------------------------------------------------- vocabulary

# canonical room type -> English plural the rules read
TYPE_WORDS = {"bedroom": "bedrooms", "bathroom": "bathrooms", "wc": "toilets", "kitchen": "kitchens",
              "living": "living rooms", "dining": "dining rooms", "hall": "hallways", "utility": "utility rooms",
              "storage": "storage rooms", "balcony": "balconies", "sauna": "saunas", "office": "offices",
              "meeting": "meeting rooms"}
ALL_TYPES = ["bedroom", "kitchen", "bathroom", "wc", "living", "dining", "hall", "utility", "storage", "balcony",
             "sauna", "office", "meeting"]
ROOMS_ANY = re.compile(r"\brooms?\b|\bspaces?\b|\bhuone(?!isto)\w*|\btilat\b|\btiloj\w*|\braum\w*|"
                       r"\braeume\b|\braume\b|\bzimmer\b|\brum(men|mer|men)?\b|\bpieces?\b|\blocali\b|\bstanze\b")

LIST_Q = re.compile(
    r"\b(list|enumerate|itemi[sz]e|tabulate)\b|\b(area|size)s? of (each|every)\b|\b(each|every|all)\s+(of the\s+)?"
    r"(\w+\s+)?(rooms?|spaces?|bedrooms?|bathrooms?|kitchens?|toilets?|offices?|wcs?)\b.*\b(areas?|sizes?|m2|how big)\b|"
    r"\b(rooms?|spaces?|bedrooms?|bathrooms?|toilets?|offices?)\b.*\b(and|with)\s+(their|its|the)\s+(areas?|sizes?)\b|"
    r"\bwhat\s+(rooms|spaces)\s+(are|is)\s+there\b.*\b(how big|areas?|sizes?|large)\b|"
    r"\bhow big\s+(is|are)\s+(each|every)\b|"
    r"\bluettele\w*|\blistaa\b|\blistaus\w*|\b(kunkin|jokaisen|kaikkien)\b.*\b(pinta-ala\w*|koko|koot)\b|\bpinta-alat\b.*\b(huoneittain|eriteltyna)\b|"
    r"\bhuoneittain\b|\b(liste|auflist\w*)\b|\b(jede[sn]?|jeder|aller)\s+\w+.*\b(flache|grosse)\b|"
    r"\b(lista|listan)\b|\b(varje|alla)\s+\w+.*\b(yta|ytor|storlek)\b")
EXTREME = re.compile(r"\b(?P<min>smallest|tiniest|least spacious|minimum|min)\b|\b(?P<max>largest|biggest|most spacious|"
                     r"maximum|max)\b|\b(?P<min2>pienin|pienimman|pienimpi|kleinste\w*|minsta)\b|"
                     r"\b(?P<max2>suurin|suurimman|isoin|isoimman|grosste\w*|groesste\w*|storsta)\b")
# "which bedroom", "kumpi", "welches": the room is asked for by name, with or without its size
WHICH_ROOM = re.compile(r"\b(which|welche[rsn]?|vilke[nt]|vilka|kumpi|name)\b")
SIZE_WORD = re.compile(r"\b(area|size|how big|how large|m2|square|pinta-ala\w*|koko|flache|grosse|yta|storlek)\b")
WHICH = re.compile(r"\b(which|what|name|mika|mita|missa|welche[rsn]?|vilke[nt]|vilka|quel\w*)\b")
COND = re.compile(
    r"\b(?P<op>over|above|more than|larger than|bigger than|greater than|at least|exceeding|under|below|less than|"
    r"smaller than|at most|yli|alle|vahintaan|enintaan|uber|ueber|mehr als|unter|weniger als|mindestens|hochstens|"
    r"grosser als|groesser als|kleiner als|suurempi\w* kuin|pienempi\w* kuin|"
    r"over|mer an|mindre an|storre an|minst|hogst)\s+(?P<n>\d+(?:[.,]\d+)?)\s*(?P<u>m2|m\^2|sqm|sq\.? ?m|square met\w*|"
    r"nelio\w*|quadratmeter|kvadratmeter|kvm|sq\.? ?ft|square f(?:ee|oo)t)?")
COND_GT = {"over", "above", "more than", "larger than", "bigger than", "greater than", "at least", "exceeding", "yli",
           "vahintaan", "uber", "ueber", "mehr als", "mindestens", "mer an", "minst", "grosser als", "groesser als",
           "storre an"}
COND_GE = {"at least", "vahintaan", "mindestens", "minst"}
COND_LE = {"at most", "enintaan", "hochstens", "hogst"}
SHARE = re.compile(r"\b(share|percentage|percent|proportion|fraction|ratio)\b|%\s+of\b|\bhow much of the\b|"
                   r"\bosuus\w*|\bprosent\w*|\banteil\w*|\bprozent\w*|\bandel\w*|\bprocent\w*")
PER_APT = re.compile(r"\b(per|by|for each|each|every)\s+(apartment|flat|unit|dwelling)\b|\bapartment by apartment\b|"
                     r"\bflat by flat\b|\basunnoittain\b|\bhuoneistoittain\b|\b(pro|je)\s+wohnung\b|\bper lagenhet\b|"
                     r"\bfor every apartment\b|\bin each (apartment|flat|unit)\b")
COUNT_CUE = re.compile(r"\b(how many|number of|count|kuinka monta|montako|wie viele|hur manga|antal|anzahl)\b")
SUM_WORDS = re.compile(r"\b(total|together|combined|altogether|in all|sum|summed|yhteensa|yhteenlaskettu|zusammen|"
                       r"insgesamt|gesamt\w*|totalt|sammanlagt|tillsammans)\b")
MATERIAL = re.compile(r"\btil(e|es|ing)\b|laatta|laato|fliese|kakel|flooring|parquet|parkett|paint|maali|farbe|"
                      r"screed|waterproof|vedeneristys|carpet|vinyl|laminat")
CONJ = re.compile(r"\s+(?:and|ja|sekä|und|och|og|et|e|y)\s+|\s*,\s*(?:and\s+)?|\s*&\s*|\s*\+\s*", re.I)
# object pairs the dataset asks as one object: never split
JOINED = re.compile(r"showers? and bath(tub)?s?|suihku\w* ja kylpyamme\w*|sinks? and wash ?basins?|pesu- ja tiskiallas|"
                    r"bathrooms? or washrooms?|kylpy- tai pesuhuone|doors? and windows? (in|on) the schedule|"
                    r"\binterior and (exterior|entrance)|\bexterior and interior|\binside and outside|"
                    r"\b(wc|toilet)s? and (bathroom|shower)s? together")


def _types(question: str) -> list[str]:
    from .phrasing import _named_types
    t = _named_types(question)
    return [x for x in ALL_TYPES if x in t]


def _orig_scope(question: str) -> Query:
    from .phrasing import read
    return read(question, use_llm=False)


# ----------------------------------------------------------------------------------------------- plan

def plan(question: str) -> dict | None:
    """The composite shape of a question, or None for a question one route answers. Quoted questions (the dataset
    templates) are never composite."""
    if quotes(question) or '"' in question or len(question) > 300:
        return None
    from .understand import WHICH_STOREY
    from .query import _SCHEDULE_Q
    f = fold(strip_quoted(question))
    if _SCHEDULE_Q.search(f):
        return None                        # one schedule entry or sheet: the schedule routes read those
    if WHICH_STOREY.search(f) and not COUNT_CUE.search(f):
        return {"kind": "which_storey"}
    cmp = compare_apartments(f)
    if cmp:
        return cmp
    types = _types(question)
    if types and COUNT_CUE.search(f) and FOLLOW_SIZE.search(f) and not MATERIAL.search(f):
        # "how many meeting rooms are there and how big are they": the rooms of that type, one row each, with the count
        return {"kind": "list", "types": types}
    parts = split_parts(question)
    if parts:
        return {"kind": "parts", "parts": parts}
    if APTS_AREA.search(f) and not COUNT_CUE.search(f) and not MATERIAL.search(f):
        # "the total area of the apartments on this floor": the room areas grouped by apartment
        return {"kind": "per_apartment", "types": types}
    if EACH_APT_AREA.search(f) and not COUNT_CUE.search(f) and not MATERIAL.search(f):
        # "what is the area of each apartment": the same rows as "the areas of the apartments", per apartment
        return {"kind": "per_apartment", "types": types}
    rooms = bool(types) or bool(ROOMS_ANY.search(f))
    if not rooms or MATERIAL.search(f):
        return None
    from .walls import _parse_inputs
    if _parse_inputs(question).get("waste_pct") is not None:
        return None                        # "hukkaa kymmenen prosenttia" is a takeoff's waste, not a share
    if PER_APT.search(f) and not re.search(r"\bhow many (apartments|flats|units)\b", f):
        return {"kind": "per_apartment", "types": types, **({"count": True} if COUNT_CUE.search(f) else {})}
    if SHARE.search(f) and types:
        return {"kind": "share", "types": types}
    m = COND.search(f)
    if m and (m.group("u") or re.search(r"\b(m2|area|size|pinta-ala|flache|yta)\b", f)):
        op = m.group("op")
        n = float(m.group("n").replace(",", "."))
        if m.group("u") and re.match(r"sq\.? ?ft|square f", m.group("u")):
            n = n / 10.7639
        return {"kind": "condition", "types": types, "op": op, "gt": op in COND_GT or op.startswith("suurempi"),
                "ge": op in COND_GE or op in COND_LE,
                "threshold": round(n, 3), "count": bool(COUNT_CUE.search(f))}
    e = EXTREME.search(f)
    if e:
        mx = bool(e.group("max") or e.group("max2"))
        which = bool(WHICH_ROOM.search(f)) or (bool(WHICH.search(f)) and not SIZE_WORD.search(f))
        if mx and not types and not which:
            return None                    # "biggest room?": the rules' own area max
        return {"kind": "extreme", "types": types, "max": mx, "which": which}
    if LIST_Q.search(f) and re.search(r"\b(areas?|sizes?|how big|how large|m2|square|big|large|pinta-ala\w*|koko|koot|"
                                      r"flache\w*|grosse\w*|yta|ytor|storlek\w*)\b", f):
        return {"kind": "list", "types": types}
    if (LIST_VERB.search(f) or ROOMS_THERE.search(f)) and not re.search(r"\btypes?\b|\btyyp\w*|\btyp(en)?\b", f):
        # "list the rooms", "what rooms are there": the same rows, by name (with the area where the route has one).
        # A model's own space list (ifc-props) answers first (router._composite). "list the room types" is not this
        return {"kind": "list", "types": types}
    return None


# ---- two apartments compared (final judge review 15 Nov, fix 5): "is apartment A or B bigger, and by how much?",
# "how much bigger is apartment A than B", "difference in area between flat 1 and flat 2". Each apartment is answered
# with the existing per-apartment rule ("what is the area of apartment A?") and the two are subtracted.
_APT = r"(?:apartments?|flats?|units?|dwellings?|asun(?:to|non|nossa)|huoneisto\w*|wohnung\w*|lagenhet\w*)"
_ID = r"([a-z]|\d{1,3})"
_OR = r"(?:or|and|vs\.?|versus|than|compared (?:to|with)|vai|ja|oder|und|eller|och)"
CMP_APT = re.compile(rf"\b{_APT}\s+{_ID}\s+{_OR}\s+(?:(?:the\s+)?{_APT}\s+)?{_ID}\b(?!\s*(?:m|cm|mm|m2)\b)")
# "Kumpi asunto on isompi, A vai B, ja kuinka paljon?": the two ids after the apartment word, mid-sentence (Mac QA 15 Nov)
CMP_APT_MID = re.compile(rf"\b{_APT}\b[^?]*?[,:]\s*{_ID}\s+(?:or|vai|tai|oder|eller)\s+{_ID}\b(?!\s*(?:m|cm|mm|m2)\b)")
CMP_APT_TAIL = re.compile(rf"\b{_APT}\b.*?[,:]\s*{_ID}\s+(?:or|vai|oder|eller)\s+{_ID}\s*\??$")
CMP_SIZE = re.compile(r"\b(bigger|larger|smaller|greater|difference|differ|compare|more area|less area|how much (more|less|"
                      r"bigger|larger|smaller)|isompi|suurempi|pienempi|ero\w*|grosser|kleiner|unterschied|storre|mindre|"
                      r"skillnad)\b")
BY_HOW_MUCH = re.compile(r"\b(how much|by how many|by what|difference|differ|kuinka paljon|paljonko|ero\w*|wie viel|"
                         r"unterschied|hur mycket|skillnad)\b")
EACH_APT_AREA = re.compile(r"\b(area|size|floor area|how big|how large|pinta-ala\w*|koko|flache|grosse|yta|storlek)\b.*"
                           r"\b(each|every|per)\s+(apartment|flat|unit|dwelling)\b|"
                           r"\b(each|every)\s+(apartment|flat|unit|dwelling)\b.*\b(area|size|how big|how large)\b")


def compare_apartments(f: str) -> dict | None:
    """{kind: compare_apartments, a, b, by} for a folded question comparing the size of two apartments, else None.
    A count ("how many more rooms does A have than B") is not read here."""
    if COUNT_CUE.search(f) or MATERIAL.search(f) or not CMP_SIZE.search(f):
        return None
    m = CMP_APT.search(f) or CMP_APT_TAIL.search(f) or CMP_APT_MID.search(f)
    if not m:
        return None
    a, b = m.group(1).upper(), m.group(2).upper()
    if a == b:
        return None
    which = re.search(r"\b(which|bigger|larger|smaller|greater|kumpi|isompi|suurempi|pienempi|welche|grosser|kleiner|"
                      r"vilken|storre|mindre)\b", f)
    return {"kind": "compare_apartments", "a": a, "b": b, "by": bool(BY_HOW_MUCH.search(f)), "number": not which}


def run_compare_apartments(ctx, p: dict, solve_q, shell: Query) -> tuple[Query, Answer]:
    """Each apartment's area by the existing rule, then the difference; both answers are cited."""
    ids = (p["a"], p["b"])
    got = [(i, *solve_q(f"What is the area of apartment {i}?", None)) for i in ids]
    shell.measure, shell.obj = "area", "apartment"
    shell.extra["parts"] = [{"question": f"What is the area of apartment {i}?", "query": q.as_dict()} for i, q, _ in got]
    bad = [(i, a) for i, _, a in got if not a.answered or not isinstance(a.value, (int, float))]
    if bad:
        return shell, abstain(next((a.route for _, _, a in got if a.route), "compose"),
                              "cannot compare: " + "; ".join(f"apartment {i}: {a.abstain or f'answer {a.value!r} is not an area'}"
                                                             for i, a in bad))
    (ia, _, aa), (ib, _, ab) = got
    va, vb = round(float(aa.value), 2), round(float(ab.value), 2)
    diff = round(abs(va - vb), 2)
    big, small = ((ia, va), (ib, vb)) if va >= vb else ((ib, vb), (ia, va))
    if diff < 0.005:
        head = f"Apartments {ia} and {ib} are the same size: {va:.2f} m2 each"
        value = 0.0 if p["by"] or p.get("number") else f"same size, {va:.2f} m2"
    else:
        pct = 100.0 * diff / small[1] if small[1] else 0.0
        head = (f"Apartment {big[0]} is bigger by {diff:.2f} m2 ({pct:.1f}%): {big[0]} {big[1]:.2f} m2, "
                f"{small[0]} {small[1]:.2f} m2")
        value = diff if p.get("number") else f"{big[0]}, by {diff:.2f} m2" if p["by"] else big[0]
    unc = [(i, a) for i, _, a in got if a.tier != "proven"]
    a = Answer(value=value, unit="m2" if isinstance(value, float) else "", route=aa.route,
               confidence=min(aa.confidence or 0, ab.confidence or 0), evidence=aa.evidence,
               explanation=f"{head}. Apartment {ia}: {va:.2f} m2, {aa.explanation or ''} | Apartment {ib}: {vb:.2f} m2, "
                           f"{ab.explanation or ''}",
               items=[{"label": f"apartment {ia}", "value": va, "tier": aa.tier, "route": aa.route},
                      {"label": f"apartment {ib}", "value": vb, "tier": ab.tier, "route": ab.route},
                      {"label": "difference", "value": diff}],
               cost_usd=(aa.cost_usd or 0) + (ab.cost_usd or 0),
               tier="proven" if not unc else "estimate",
               tier_reason="; ".join(f"apartment {i}: {x.tier_reason or 'estimate'}" for i, x in unc))
    return shell, a


FOLLOW_SIZE = re.compile(r"\b(and|,)\s+(how big|how large|what size|what sizes|what are their (areas|sizes)|their (areas|sizes))\b|"
                         r"\bja\s+(kuinka|miten)\s+(isoja|suuria|isoa|suurta|iso|suuri)\b|\bja\s+niiden\s+(koko|koot|pinta-ala\w*)\b|"
                         r"\bund\s+wie\s+gross\b|\boch\s+hur\s+stora\b")
APTS_AREA = re.compile(r"\b(area|areas|size|sizes|floor area|pinta-ala\w*|flache|yta)\b.*\b(apartments|flats|dwellings|"
                       r"asuntojen|asunnot|huoneistojen|huoneistot|wohnungen|lagenheter\w*)\b|"
                       r"\b(apartments|flats|asunnot|huoneistot|wohnungen)\b.*\b(areas?|sizes?|how big|how large|pinta-ala\w*)\b")
LIST_VERB = re.compile(r"\b(list|enumerate|itemi[sz]e|tabulate|luettele\w*|listaa|liste|auflist\w*|lista|listan)\b")
ROOMS_THERE = re.compile(r"\b(what|which)\s+(rooms|spaces)\s+(are there|are shown|are drawn|does (it|the \w+|this \w+) have|"
                         r"are (on|in) (this|the) (plan|drawing|floor ?plan|sheet|apartment|flat))\b|"
                         r"\bmita (huoneita|tiloja)\s+(on|siina on|asunnossa on)\b|\bwelche (raume|zimmer) (gibt es|hat)\b|"
                         r"\bvilka rum (finns|har)\b")


# a clause that asks on its own: a count cue or a size cue ("..., und wie viele Fenster gibt es?")
CLAUSE_CUE = re.compile(r"\b(how many|how big|how large|what is|what's|kuinka monta|montako|kuinka suuri|kuinka iso|mika on|"
                        r"wie viele|wie gross|was ist|hur manga|hur stor|vad ar)\b|\barea\b|pinta-ala|flache|\byta\b")
CLAUSE_SPLIT = re.compile(r"\s*(?:,\s*(?:and|ja|und|och|og|et)?|;|\band\b|\bja\b|\bund\b|\boch\b)\s+(?=(?:how|what|kuinka|"
                          r"montako|mika|paljonko|wie|was|welche\w*|hur|vad|vilk\w*)\b)", re.I)


def _clauses(question: str) -> list[str] | None:
    """'Wie gross ist die Kueche, und wie viele Fenster gibt es?' -> two questions, each with its own cue and its own
    object or room type; None when a clause leans on the other ('..., and what is the area of each?')."""
    from .phrasing import _named_types
    from .query import RuleParser
    pieces = [p.strip(" ,;?") for p in CLAUSE_SPLIT.split(fold(question)) if p and p.strip(" ,;?")]
    if len(pieces) < 2:
        return None
    for p in pieces:
        if not CLAUSE_CUE.search(p):
            return None
        from .phrasing import read
        q = read(p, use_llm=False)
        if not (q.ok() and q.measure != "text" and not q.extra.get("compound")) and not split_parts(p):
            return None             # "..., and what is the breakdown by bathroom?" is not a question on its own
    return [p + "?" for p in pieces]


def split_parts(question: str) -> list[str] | None:
    """'How many doors and windows?' -> ['How many doors?', 'How many windows?']; 'kitchen and bathroom area' ->
    one area question per room type. None when the question asks one thing (a known joined object, a takeoff that
    is one order, a sum asked for)."""
    from .query import OBJECTS, COUNTABLE
    f = fold(strip_quoted(question))
    if JOINED.search(f) or not CONJ.search(f):
        return None
    clauses = _clauses(question)
    if clauses:
        out = []
        for c in clauses:
            out += split_parts(c) or [c]
        return out if 2 <= len(out) <= 4 else None
    if MATERIAL.search(f) or re.search(r"\bparquet|parket\w*|laminate|lattiamateriaal\w*|\bbaseboards?|skirting|"
                                       r"jalkalist\w*", f):
        return None             # "m2 of parquet for the bedrooms and the living room": one takeoff over both types
    # "how many square metres ..." asks an area, not a count
    count = bool(COUNT_CUE.search(f)) and not re.search(r"square met|\bm2\b|\bsqm\b|nelio\w*|quadratmeter|kvadratmeter", f)
    # the things named, in order of appearance: element objects, then room types
    found = []
    for name, pat in OBJECTS:
        if name in ("room", "sanitary", "storey", "apartment"):
            continue
        for m in re.finditer(pat, f):
            found.append((m.start(), name, m.group(0)))
    from .takeoff import _Q_TYPE_RE
    for t, rx in _Q_TYPE_RE.items():
        if t == "wet":
            continue
        for m in rx.finditer(f):
            found.append((m.start(), "type:" + t, m.group(0)))
    found.sort()
    seen, things = set(), []
    for pos, name, word in found:
        key = {"type:bedroom": "bedroom", "type:kitchen": "kitchen", "type:bathroom": "bathroom", "type:sauna": "sauna",
               "type:balcony": "balcony", "type:office": "office", "type:meeting": "meeting_room"}.get(name, name)
        if key in seen or any(abs(pos - p) < 2 for p, _, _ in things):
            continue
        seen.add(key)
        things.append((pos, name, word))
    if len(things) < 2 or len(things) > 4:
        return None
    # the things must be joined by a conjunction (not "doors in the kitchen")
    for (p1, _, w1), (p2, _, _) in zip(things, things[1:]):
        between = f[p1 + len(w1):p2]
        if not re.fullmatch(r"\s*(?:area|size|pinta-ala\w*|flache|yta)?\s*(?:,\s*)?(?:and|ja|seka|und|och|og|et|e|y|&|\+)"
                            r"\s*(?:the|die|der|das|les|la|le)?\s*|\s*,\s*", between):
            return None
    if count:
        if not all(n in COUNTABLE or n.startswith("type:") for _, n, _ in things):
            return None
        return [f"How many {w}?" for _, _, w in things]
    area = re.search(r"\b(area|size|how big|how large|m2|square met\w*|pinta-ala\w*|flache|grosse|yta|storlek)\b", f)
    if area and all(n.startswith("type:") for _, n, _ in things) and not SUM_WORDS.search(f) and not MATERIAL.search(f):
        return [f"What is the area of the {TYPE_WORDS[n[5:]]}?" for _, n, _ in things]
    return None


# ----------------------------------------------------------------------------------------------- rows

def rows_of(a: Answer) -> list[dict]:
    """One row per room the answer used: {name, area, unit (apartment), item}."""
    out = []
    for it in a.items or []:
        if not isinstance(it, dict) or it.get("reading") == "apartment summary" or "totals" in it:
            continue        # a printed summary total (3H+K+S 73,50 m2) or a takeoff total is never a room
        area = next((it[k] for k in ("area_m2", "floor_m2", "area", "net_area_m2") if isinstance(it.get(k), (int, float))
                     and not isinstance(it.get(k), bool)), None)
        if area is None and isinstance(it.get("value"), (int, float)) and not isinstance(it.get("value"), bool):
            area = it["value"]
        if area is None:
            continue
        nm = it.get("room") or it.get("name") or it.get("label") or it.get("space") or ""
        num = it.get("number") or ""
        name = nm if (not num or str(num) in str(nm)) else f"{num} {nm}".strip()
        unit = it.get("unit") or it.get("apartment")
        if not unit and re.fullmatch(r"[A-Z]\d{2,4}", str(num or nm)):
            unit = str(num or nm)[0]           # IFC numbering A203 / B104: house or flat letter
        elif not unit and re.fullmatch(r"\d{1,4}-\d{1,3}", str(num or nm).strip()):
            unit = str(num or nm).strip().split("-")[0]   # <flat>-<room> (Esplanades '3-1 ELUTUBA+KOOK')
            it = dict(it, unit_from="space numbering")
        out.append({"name": str(name or "unnamed room"), "area": float(area), "unit": unit, "item": it})
    return out


def _fmt(rows: list[dict], cap: int = 8) -> str:
    s = "; ".join(f"{r['name']} {r['area']:.2f} m2" for r in rows[:cap])
    return s + (f"; and {len(rows) - cap} more" if len(rows) > cap else "")


def _base_query(question: str, types: list[str], per_apartment: bool = False) -> Query:
    """The ordinary query whose answer the composite is built from, with the question's own scope."""
    if types:
        q = RuleParser().parse("What is the total area of the " + " and ".join(TYPE_WORDS[t] for t in types) + "?")
    else:
        q = RuleParser().parse("What is the total area of all rooms?")
    orig = _orig_scope(question)
    q.level, q.scope = orig.level, orig.scope
    for k in ("whole_model", "storey_ref", "external"):
        if k in orig.extra:
            q.extra[k] = orig.extra[k]
    if orig.extra.get("unresolved_scope") and not (per_apartment and not orig.extra.get("storey_ref")):
        # a storey or part named without quotes: the router resolves it (named storey, storey word, the sheet's own
        # ground floor) or the base question is not answered for the whole drawing. "apartment by apartment" is the
        # breakdown itself, not a scope
        q.extra["unresolved_scope"] = True
    q.extra.pop("compound", None)
    if q.extra.get("takeoff") is not None:
        q.extra["takeoff"].pop("single_type", None)
    q.raw = question
    return q


def _named(ctx, rows: list[dict]) -> list[dict]:
    """IFC rows named by their space (LongName and number), where the route listed only the number."""
    path = ctx.model_ifc()
    if path is None or not any(r["item"].get("guid") for r in rows):
        return rows
    try:
        from .routes import ifc_route
        M = ifc_route.load(str(path))
        for r in rows:
            g = r["item"].get("guid")
            if g and not r["item"].get("room"):
                sp = M.m.by_guid(g)
                r["name"] = ifc_route.space_display(sp) or r["name"]
                r["item"] = dict(r["item"], name=r["name"])
    except Exception:
        pass
    return rows


def _base(ctx, question: str, types: list[str], solve_q, per_apartment: bool = False) -> tuple[Query, Answer, list[dict]]:
    q, a, rows = _base0(ctx, question, types, solve_q, per_apartment)
    return q, a, _named(ctx, rows)


def _base0(ctx, question: str, types: list[str], solve_q, per_apartment: bool = False) -> tuple[Query, Answer, list[dict]]:
    q = _base_query(question, types, per_apartment)
    q, a = solve_q(question, q)
    if not types and (not a.answered or not rows_of(a)):
        # scans: no total floor area, but the labelled rooms of every known type can be measured
        q2, a2 = solve_q(question, _base_query(question, ALL_TYPES, per_apartment))
        if a2.answered and rows_of(a2):
            a2.explanation = ("Rooms of every labelled type (the total floor area is not measured on this drawing). "
                              + (a2.explanation or ""))
            return q2, a2, rows_of(a2)
        # one type at a time: the types that are measured, and the ones that are not said
        got, missed, first = [], [], None
        for t in ALL_TYPES:
            qt, at = solve_q(question, _base_query(question, [t], per_apartment))
            if at.answered and rows_of(at):
                got += rows_of(at)
                first = first or at
            elif not at.answered and at.abstain and not re.search(r"no room (of type|on this floor is labelled)|"
                                                                    r"no IfcSpace of type|not found|no room", at.abstain):
                missed.append(f"{TYPE_WORDS[t]}: {at.abstain[:160]}")
        if first is not None:
            seen, rows = set(), []
            for r in got:                     # a room of two types ("Wohnen/Essen") is listed once
                k = (r["name"], round(r["area"], 2))
                if k not in seen:
                    seen.add(k)
                    rows.append(r)
            base = _derived(first, sum(r["area"] for r in rows), "m2", [r["item"] for r in rows],
                            "Rooms of every labelled type, measured one type at a time (the total floor area is not "
                            "measured on this drawing)" + (f"; not measured: {'; '.join(missed)}" if missed else "") + ".")
            if missed:
                base.tier = "estimate"
                incomplete = f"{len(missed)} room type(s) could not be measured, so the list is incomplete"
                base.tier_reason = f"{base.tier_reason}; {incomplete}" if base.tier_reason else incomplete
            return q2, base, rows
    return q, a, rows_of(a) if a.answered else []


def _derived(base: Answer, value, unit: str, items: list, explanation: str, route_suffix: str = "") -> Answer:
    a = Answer(value=value, unit=unit, route=base.route, confidence=base.confidence, evidence=base.evidence,
               explanation=explanation, items=items, cost_usd=base.cost_usd, tier=base.tier, tier_reason=base.tier_reason,
               uncertain=[], checks=base.checks, approx=base.approx)
    return a


def _noun(types: list[str]) -> str:
    return " and ".join(TYPE_WORDS[t] for t in types) if types else "rooms"


# ----------------------------------------------------------------------------------------------- run

def run(ctx, p: dict, solve_q) -> tuple[Query, Answer]:
    """Answer a composite question. solve_q(question, query or None) -> (Query, Answer) solves one ordinary question
    on the same drawing."""
    question = ctx.q.question
    kind = p["kind"]
    shell = Query(raw=question, parser="rules")
    shell.extra["composite"] = kind
    shell.extra["reading"] = describe(p)
    if kind == "which_storey":
        return shell, which_storey(ctx)
    if kind == "parts":
        return run_parts(ctx, p["parts"], solve_q, shell)
    if kind == "compare_apartments":
        return run_compare_apartments(ctx, p, solve_q, shell)
    types = p.get("types") or []
    if kind == "share":
        qt, at, rt = _base(ctx, question, types, solve_q)
        qa, aa, ra = _base(ctx, question, [], solve_q)
        shell.measure, shell.obj = "area", "room"
        if not at.answered or not aa.answered or not aa.value:
            why = at.abstain if not at.answered else (aa.abstain or "the total floor area is zero")
            return shell, abstain(at.route or aa.route or "compose", f"cannot give the share: {why}")
        if "Rooms of every labelled type" in (aa.explanation or ""):
            denom_note = "the labelled rooms of every known type (no total floor area on this drawing)"
        else:
            denom_note = "the total floor area of all rooms"
        pct = round(100.0 * float(at.value) / float(aa.value), 1)
        items = [{"label": f"{_noun(types)} (sum)", "value": float(at.value)},
                 {"label": "floor area used as the whole", "value": float(aa.value)}] + [r["item"] for r in rt]
        a = _derived(at, pct, "%", items, f"{_noun(types).capitalize()}: {float(at.value):.2f} m2 of "
                     f"{float(aa.value):.2f} m2 ({denom_note}) = {pct:.1f}%. {at.explanation or ''}")
        if aa.tier == "estimate" and a.tier != "estimate":
            a.tier, a.tier_reason = "estimate", aa.tier_reason or "the total floor area is an estimate"
        return shell, a
    q, base, rows = _base(ctx, question, types, solve_q, per_apartment=kind == "per_apartment")
    shell.measure, shell.obj, shell.level, shell.scope = "area", "room", q.level, q.scope
    if not base.answered:
        return shell, abstain(base.route or "compose", base.abstain or "the base answer is missing")
    if kind == "per_apartment" and p.get("count") and types:
        return shell, _count_per_apartment(ctx, question, types, base, rows, solve_q, shell)
    if not rows:
        return shell, abstain(base.route, f"the route answered {base.value} {base.unit} but did not list the rooms it "
                                          f"used, so a per-room answer cannot be given")
    noun = _noun(types)
    if kind == "list":
        zero = [r for r in rows if r["area"] <= 0.05]
        rows = [r for r in rows if r["area"] > 0.05] or rows
        items = [dict(r["item"], source=r["item"].get("source") or base.route) for r in rows]
        a = _derived(base, _fmt(rows), "", items,
                     f"One row per room: {len(rows)} {noun}, {sum(r['area'] for r in rows):.2f} m2 in total"
                     + (f" ({len(zero)} space(s) of 0 m2 left out: {', '.join(r['name'] for r in zero[:6])})" if zero else "")
                     + f". {base.explanation or ''}")
        return shell, a
    if kind == "extreme":
        cand = [r for r in rows if r["area"] > 0.05]
        if not cand:
            return shell, abstain(base.route, "no room with an area above zero")
        pick = max(cand, key=lambda r: r["area"]) if p["max"] else min(cand, key=lambda r: r["area"])
        word = "largest" if p["max"] else "smallest"
        ties = [r for r in cand if abs(r["area"] - pick["area"]) < 0.005 and r is not pick]
        expl = (f"The {word} of {len(cand)} {noun}: {pick['name']}, {pick['area']:.2f} m2"
                + (f" (tied with {', '.join(r['name'] for r in ties)})" if ties else "") + f". {base.explanation or ''}")
        val = f"{pick['name']}, {pick['area']:.2f} m2" if p["which"] else round(pick["area"], 2)
        a = _derived(base, val, "" if p["which"] else "m2", [pick["item"]] + [r["item"] for r in ties], expl)
        return shell, a
    if kind == "condition":
        t = p["threshold"]
        if p["gt"]:
            keep = [r for r in rows if (r["area"] >= t if p["ge"] else r["area"] > t)]
        else:
            keep = [r for r in rows if (r["area"] <= t if p["ge"] else r["area"] < t)]
        near = [r for r in rows if abs(r["area"] - t) <= 0.02 * t]
        expl = (f"{len(keep)} of {len(rows)} {noun} {p['op']} {t:g} m2"
                + (f" ({', '.join(r['name'] for r in near)} within 2% of the limit, check by eye)" if near else "")
                + f". {base.explanation or ''}")
        if p["count"]:
            shell.measure = "count"
            a = _derived(base, len(keep), "count", [r["item"] for r in keep], expl)
        else:
            a = _derived(base, _fmt(keep) if keep else "none", "", [r["item"] for r in keep], expl)
        if near and a.tier == "proven":
            a.tier, a.tier_reason = "estimate", "a room is within 2% of the limit"
        return shell, a
    if kind == "per_apartment":
        if types and any(r["unit"] is None for r in rows):
            # the room-type rows may not carry the apartment; the all-rooms answer of the same route does
            _, aa, ra = _base(ctx, question, [], solve_q, per_apartment=True)
            key = lambda r: (str(r["item"].get("number") or r["name"]), round(r["area"], 1))
            unit_of = {key(r): r["unit"] for r in ra if r["unit"]}
            for r in rows:
                r["unit"] = r["unit"] or unit_of.get(key(r))
        units = {}
        for r in rows:
            units.setdefault(r["unit"], []).append(r)
        shared = units.pop(None, []) if len([u for u in units if u]) >= 2 else []
        if shared:
            units["outside any apartment"] = shared
        if None in units or len(units) < 1:
            return shell, abstain(base.route, f"the route does not assign the {noun} to apartments on this drawing "
                                              f"({sum(1 for r in rows if r['unit'] is None)} of {len(rows)} rooms have no "
                                              f"apartment), so a breakdown per apartment cannot be given")
        lines = [(u, sum(r["area"] for r in rs), rs) for u, rs in sorted(units.items(), key=lambda kv: _unit_key(kv[0]))]
        items = [{"label": f"apartment {u}", "value": round(s, 2), "rooms": [r["name"] for r in rs]} for u, s, rs in lines]
        val = "; ".join(f"{u}: {s:.2f} m2" for u, s, _ in lines)
        flats = [u for u, _, _ in lines if u != "outside any apartment"]
        a = _derived(base, val, "", items + [r["item"] for r in rows],
                     f"{noun.capitalize()} per apartment ({len(flats)} apartments): "
                     + "; ".join(f"{u} {s:.2f} m2 ({len(rs)} room(s))" for u, s, rs in lines)
                     + f". {base.explanation or ''}")
        if any(r["item"].get("unit_from") for r in rows):
            from .answer import estimate
            estimate(a, "apartments read from the space numbering <flat>-<room>; the model has no apartment objects")
        return shell, a
    return shell, abstain("compose", f"unknown composite {kind}")


def _unit_key(u):
    s = str(u)
    return (s == "outside any apartment", int(s) if s.isdigit() else 0, s)


def _count_per_apartment(ctx, question: str, types: list[str], base: Answer, rows: list[dict], solve_q, shell) -> Answer:
    """'How many bathrooms per apartment?': the rooms of the type counted in each apartment, over every apartment the
    route assigns rooms to (a flat with none of them counts 0)."""
    from collections import Counter
    from .answer import estimate
    shell.measure = "count"
    noun = _noun(types)
    _, aa, ra = _base(ctx, question, [], solve_q, per_apartment=True)
    units = sorted({r["unit"] for r in ra if r["unit"]}, key=_unit_key)
    if len(units) < 2:
        return abstain(base.route or aa.route or "compose", f"the route does not assign rooms to apartments on this "
                                                            f"drawing, so the {noun} cannot be counted per apartment")
    key = lambda r: (str(r["item"].get("number") or r["name"]), round(r["area"], 1))
    unit_of = {key(r): r["unit"] for r in ra if r["unit"]}
    for r in rows:
        r["unit"] = r["unit"] or unit_of.get(key(r))
    counts = {u: sum(1 for r in rows if r["unit"] == u) for u in units}
    outside = sum(1 for r in rows if r["unit"] not in counts)
    dist = Counter(counts.values())
    if len(dist) == 1:
        val = f"{next(iter(dist))} in each of the {len(units)} apartments"
    else:
        val = "; ".join(f"{u}: {c}" for u, c in counts.items())
    expl = (f"{noun.capitalize()} per apartment, {len(units)} apartments ("
            + ", ".join(f"{c} in {k} apartment{'s' if k != 1 else ''}" for c, k in sorted(dist.items())) + ")"
            + (f"; {outside} outside any apartment" if outside else "") + ". Per apartment: "
            + ", ".join(f"{u}: {c}" for u, c in counts.items()) + f". {base.explanation or ''}")
    items = [{"label": f"apartment {u}", "value": c} for u, c in counts.items()] + [r["item"] for r in rows]
    a = _derived(base, val, "", items, expl)
    if any(r["item"].get("unit_from") for r in ra):
        estimate(a, "apartments read from the space numbering <flat>-<room>; the model has no apartment objects")
    return a


def describe(p: dict) -> str:
    """One plain sentence: how the question was split or built (shown in the Why panel)."""
    noun = _noun(p.get("types") or [])
    k = p["kind"]
    if k == "parts":
        return f"{len(p['parts'])} questions, each answered on its own: " + " | ".join(p["parts"])
    if k == "list":
        return f"a list of the {noun} with their floor areas, one row per room"
    if k == "extreme":
        return (f"the {'largest' if p['max'] else 'smallest'} of the {noun} by floor area"
                + (", with its name" if p.get("which") else ""))
    if k == "condition":
        return (f"{'the number of' if p.get('count') else 'the'} {noun} {p['op']} {p['threshold']:g} m2")
    if k == "share":
        return f"the floor area of the {noun} as a percentage of the floor area of all rooms"
    if k == "per_apartment":
        return f"the floor area of the {noun}, summed per apartment"
    if k == "which_storey":
        return "the storey this drawing shows"
    if k == "compare_apartments":
        return (f"the floor area of apartment {p['a']} and of apartment {p['b']}, each answered on its own, "
                f"then compared" + (" and subtracted" if p.get("by") else ""))
    return k


def run_parts(ctx, parts: list[str], solve_q, shell: Query) -> tuple[Query, Answer]:
    answers = [(p, *solve_q(p, None)) for p in parts]
    shell.extra["parts"] = [{"question": p, "query": q.as_dict()} for p, q, _ in answers]
    got = [(p, q, a) for p, q, a in answers if a.answered]
    if not got:
        return shell, abstain(answers[0][2].route or "compose",
                              "no part could be answered: " + " | ".join(f"{p} {a.abstain}" for p, _, a in answers))
    rows, vals = [], []
    for i, (p, q, a) in enumerate(answers, 1):
        status = "cannot" if not a.answered else (a.tier or "proven")
        v = "" if not a.answered else (f"{a.value:g}" if isinstance(a.value, float) else str(a.value))
        unit = "" if a.unit == "count" else (" " + a.unit if a.unit else "")
        rows.append({"label": p, "value": f"{v}{unit} ({status}, {a.route})" if a.answered else f"cannot: {a.abstain}",
                     "part": i, "status": status, "route": a.route, "answer": v, "unit": a.unit})
        vals.append(f"{v}{unit}" if a.answered else "cannot")
    first = got[0][2]
    unc = [r for r in rows if r["status"] != "proven"]
    a = Answer(value="; ".join(f"{_short(p)} {v}" for p, v in zip(parts, vals)), unit="", route=first.route,
               confidence=min(x.confidence or 0 for _, _, x in got), evidence=first.evidence,
               explanation=" | ".join(f"Part {i}: {p} -> " + (f"{x.value} {x.unit}. {x.explanation or ''}" if x.answered
                                                              else f"cannot: {x.abstain}")
                                      for i, (p, _, x) in enumerate(answers, 1)),
               items=rows + [it for _, _, x in got for it in (x.items or [])[:50]],
               cost_usd=sum(x.cost_usd or 0 for _, _, x in answers),
               tier="proven" if not unc else "estimate",
               tier_reason="" if not unc else "; ".join(f"part {r['part']} is {r['status']}" for r in unc))
    return shell, a


def _short(q: str) -> str:
    m = re.match(r"(?i)how many (.+?)\?$", q) or re.match(r"(?i)what is the area of the (.+?)\?$", q)
    if not m:
        return q.rstrip("?")
    return re.split(r"\s+(?:are|is|do|does|exist|there|on|in|gibt|finns|on)\b", m.group(1))[0]


# ----------------------------------------------------------------------------------------------- which storey

def which_storey(ctx) -> Answer:
    """The storey a plan sheet shows: from the IFC model it was drawn from (storey name and space labels printed
    on the sheet, bpqa/routes/ifc_route.py storey_of_sheet), or the model's storeys when asked on the model."""
    path = ctx.model_ifc()
    if path is not None:
        from .routes import ifc_route
        M = ifc_route.load(str(path))
        if ctx.q.ext == "ifc":
            sts = sorted(M.storeys, key=lambda s: float(getattr(s, "Elevation", 0) or 0))
            names = [s.Name for s in sts]
            a = Answer(value=f"the whole model: {len(names)} storeys ({', '.join(names)})", unit="", route="ifc",
                       confidence=0.9, items=[{"name": s.Name, "guid": s.GlobalId,
                                               "value": f"elevation {float(getattr(s, 'Elevation', 0) or 0) * M.len_scale:.2f} m"}
                                              for s in sts],
                       explanation="Asked on the IFC model itself, which holds every storey; ask on a plan sheet for one.",
                       tier="proven")
            return a
        st, note = ifc_route.sheet_storey(M, ctx)
        if st is None:
            a = abstain("ifc", f"cannot tell which storey this sheet shows: {note}")
            a.final = True
            return a
        elev = float(getattr(st, "Elevation", 0) or 0) * M.len_scale
        return Answer(value=st.Name, unit="", route="ifc", confidence=0.9,
                      items=[{"name": st.Name, "guid": st.GlobalId, "value": f"elevation {elev:.2f} m"}],
                      explanation=f"Storey {st.Name} (elevation {elev:.2f} m): {note}. Source: {path.name}.",
                      tier="proven")
    text = _sheet_text(ctx)
    if text:
        m = re.search(r"(?i)\b(\d{1,2}\.\s*(?:OG|UG|Obergeschoss|Untergeschoss|kerros|korrus|etage|vaning)|"
                      r"(?:Erdgeschoss|Untergeschoss|Obergeschoss|Dachgeschoss|Kellergeschoss|Ground floor|"
                      r"Basement|Attic|First floor|Second floor|Third floor|Level\s+-?\d{1,2}|Floor\s+-?\d{1,2}|"
                      r"pohjakerros|kellarikerros|ullakkokerros|rez-de-chauss[ée]e|[1-9](?:er|e|ème)\s+étage|"
                      r"piano\s+terra|piano\s+\d))\b", text)
        if m:
            return Answer(value=m.group(1).strip(), unit="", route="pdf-text", confidence=0.7, tier="estimate",
                          tier_reason="the storey name is read from the sheet's text, not from a model",
                          items=[{"text": m.group(1).strip()}],
                          explanation=f"The sheet's text layer names the storey '{m.group(1).strip()}'.")
    a = abstain("parser", "the drawing does not name its storey (no building model behind it, and no storey name "
                          "in its text)")
    a.final = True
    return a


def _sheet_text(ctx) -> str:
    if ctx.q.ext not in ("pdf", "dxf"):
        return ""
    try:
        if ctx.q.ext == "pdf":
            import pymupdf
            with pymupdf.open(str(ctx.file)) as doc:
                return " ".join(page.get_text() for page in doc)[:200000]
        import ezdxf
        doc = ezdxf.readfile(str(ctx.file))
        return " ".join(e.dxf.text for e in doc.modelspace().query("TEXT"))[:200000]
    except Exception:
        return ""
