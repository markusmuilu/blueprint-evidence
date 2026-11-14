"""Question understanding beyond one measurable shape: what the reading misses, storeys named in words, and
questions that are built from several answers.

Kept apart from the rule parser (bpqa/query.py, bpqa/takeoff.py) and the language-model reader (bpqa/phrasing.py)
so other parser work does not collide with it. Four parts:

  REWRITE     `rewrite()` turns Swedish area words ("sovrummens yta", "koksytan") into the words the rules read.
              Used for the rule parse only; the language model sees the question as asked.
  FINISH      `finish()` adds what the rules can read deterministically after any parse: a storey named in words
              ("top floor", "kellarissa", "im Obergeschoss") as extra["storey_ref"], a qualifier the adjacency rule
              missed ("doors made of glass"), and an output unit ("in square feet").
  GAPS        `gaps()` says why a confident rule reading is still incomplete, so the language model is asked: a room
              type, storey or qualifier word in the question that the reading does not carry.
  STOREYS     `resolve_storey()` maps "top", "ground", "basement", "attic" onto the storeys of an IFC model.

The composite questions (lists, superlatives, conditions, shares, several parts, per apartment) are in
bpqa/compose.py.
"""
from __future__ import annotations

import re

from .query import _SCHEDULE_Q, QUALIFIER_OBJS, Query, attach_qualifier, fold, quotes, strip_quoted

# --------------------------------------------------------------------------- rewrite (rules only)

# Swedish: "sovrummens yta", "badrummets yta", "koksytan", "golvytan", "hur stor yta har sovrummen". fold() has
# already turned a-ring / a-umlaut / o-umlaut into a / a / o.
_SV_COMPOUND_AREA = re.compile(r"\b([a-z]{3,}?)s?(ytan|ytor|ytorna|golvytan|golvyta)\b")
_SV_AREA = re.compile(r"\b(ytan|ytor|ytorna|yta|golvytan|golvyta|boyta|boytan)\b")
_SV_CUE = re.compile(r"\b(vad|hur|ar|har|rummen|rummet|rummens|rummets|sovrum\w*|badrum\w*|kok\w*|vardagsrum\w*)\b")


def rewrite(question: str) -> str:
    """The question with Swedish area words replaced by 'area' (and a compound split: 'koksytan' -> 'kok area');
    unchanged when there is none. Only the rule parse reads the result."""
    f = fold(question)
    if not (_SV_AREA.search(f) or _SV_COMPOUND_AREA.search(f)) or not _SV_CUE.search(f):
        return question
    s = fold(question)
    s = _SV_COMPOUND_AREA.sub(lambda m: f"{m.group(1)} area" if m.group(1) not in ("golv", "bo") else "area", s)
    s = _SV_AREA.sub("area", s)
    return s


# --------------------------------------------------------------------------- storeys named in words

# (kind, pattern) on folded text with quotes removed. Ordered: the first match wins.
STOREY_WORDS: list[tuple[str, re.Pattern]] = [
    ("top", re.compile(r"\b(top|uppermost|highest|upper ?most|topmost)\s+(floor|storey|story|level)\b|"
                       r"\b(ylin|ylimma|ylimmassa|ylimman|ylimpaan|ylin)\w*\s+kerro\w*|"
                       r"\bobersten?\s+(geschoss|stockwerk|etage|stock)\w*|\bversta\s+vaning\w*|\bovre\s+vaning\w*|"
                       r"\bdernier\s+etage\b|\bultimo\s+piano\b|\bpiano\s+superiore\b")),
    ("ground", re.compile(r"\b(ground|main|entrance|street)\s+(floor|level|storey)\b|\bgroundfloor\b|"
                          r"\berdgeschoss\w*|\bpohjakerro\w*|\bmaantasokerro\w*|\bensimmai\w*\s+kerro\w*|"
                          r"\bbottenvaning\w*|\bentreplan\w*|\bstueetage\w*|\brez-de-chaussee\b|\bpiano\s+terra\b|"
                          r"\bpianterreno\b")),
    ("basement", re.compile(r"\bbasements?\b|\bcellars?\b|\bkellari\w*|\bkeller\b|\bkellergeschoss\w*|"
                            r"\buntergeschoss\w*|\bkallar(e|en|plan)\w*|\bkallarvaning\w*|\bsous-sol\b|"
                            r"\bseminterrato\b|\binterrato\b|\bunderground\s+(floor|level|storey)\b")),
    ("attic", re.compile(r"\battic\b|\bloft\b|\bullakko\w*|\bdachgeschoss\w*|\bdachboden\b|\bvinds?(vaning|plan)\w*|"
                         r"\bcombles\b|\bsottotetto\b|\bmansard\w*|\broof\s+(floor|storey|level)\b")),
    ("bottom", re.compile(r"\b(lowest|bottom|bottommost)\s+(floor|storey|story|level)\b|\b(alin|alimma|alimmassa|alimman)\w*\s+"
                          r"kerro\w*|\bunterste[ns]?\s+(geschoss|stockwerk|etage)\w*|\bnedersta\s+vaning\w*")),
    # "upstairs", "ylakerta", "im Obergeschoss": the upper floor of a two-floor house (ambiguous with more floors)
    ("upper", re.compile(r"\bupstairs\b|\bupper\s+(floor|storey|level)\b|\bylakerta\w*|\byla-?kerro\w*|"
                         r"\bobergeschoss\b|\bovanvaning\w*|\bovervaning\w*|\betage\s+superieur\b")),
    ("lower", re.compile(r"\bdownstairs\b|\blower\s+(floor|storey|level)\b|\balakerta\w*|\bala-?kerro\w*|"
                         r"\bnedervaning\w*|\betage\s+inferieur\b")),
]
# "first floor" (ground floor in US and Finnish usage, the floor above in British usage) is left to the router's
# numbered-storey reading (bpqa/router.py named_storey), which says how it read it
# a question about the storey itself ("which floor is this plan", "mika kerros tama on")
WHICH_STOREY = re.compile(
    r"\b(which|what)\s+(floor|storey|story|level)\s+(is|does|do|am|are|shows?|was)\b|"
    r"\b(which|what)\s+(floor|storey|story|level)\b.*\b(plan|sheet|drawing|this|shown|show)\b|"
    r"\b(floor|storey|story|level)\s+(name|number)\s+of\s+(this|the)\s+(plan|sheet|drawing)\b|"
    r"\bmika\s+kerros\b|\bmonesko\s+kerros\b|\bmissa\s+kerroksessa\b.*\b(tama|kuva|piirustus)\b|"
    r"\bwelche[sr]?\s+(geschoss|stockwerk|etage)\b|\bvilken\s+vaning\b|\bquel\s+etage\b|\bquale\s+piano\b")


def storey_word(question: str) -> tuple[str, str] | None:
    """(kind, phrase) for a storey named in words, or None. Quoted names are left to the rules."""
    f = fold(strip_quoted(question))
    if WHICH_STOREY.search(f):
        return None
    for kind, rx in STOREY_WORDS:
        m = rx.search(f)
        if m:
            return kind, m.group(0)
    return None


def _rooms_by_storey(M) -> dict:
    out = {}
    for sp in M.entities("room"):
        st = M.storey_of(sp)
        if st is not None:
            out[st.id()] = out.get(st.id(), 0) + 1
    return out


def _elev(s) -> float:
    try:
        return float(getattr(s, "Elevation", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


_NAME = {
    "ground": re.compile(r"ground|erdgeschoss|^eg\b|^eg$|pohjakerros|maantaso|bottenv|entreplan|stue|rez-de|terra|"
                         r"^0+$|level 0|^00\b|piano terra|^1\.? ?korrus|grundplan"),
    "basement": re.compile(r"basement|cellar|keller|untergeschoss|^ug\b|kellari|kallare|sous-sol|interrato|"
                           r"underground|^-\d|^b\d\b|level -|kjeller|kaelder|kalder|souterrain|"
                           r"sotano"),
    "attic": re.compile(r"attic|loft|dachgeschoss|^dg\b|ullakko|vind|combles|sottotetto|mansard"),
    "roof": re.compile(r"roof|dach(?!geschoss)|katto|^tak\b|toit|tetto|copertura"),
}


def resolve_storey(M, kind: str) -> tuple[object | None, str]:
    """(storey, note) for 'top', 'ground', 'basement' or 'attic' on an IFC model, or (None, why not). The storeys
    that hold rooms are the floors; a roof or foundation level without rooms is not a floor."""
    sts = sorted(M.storeys, key=_elev)
    if not sts:
        return None, "the model has no storeys"
    rooms = _rooms_by_storey(M)
    floors = [s for s in sts if rooms.get(s.id())]
    if not floors:                      # no IfcSpace: the storeys that hold doors or windows are the floors
        held = set()
        for obj in ("door", "window"):
            for e in M.entities(obj):
                st = M.storey_of(e)
                if st is not None:
                    held.add(st.id())
        floors = [s for s in sts if s.id() in held] or sts
    names = ", ".join(s.Name or "?" for s in sts)
    nm = lambda s: fold(s.Name or "").strip()
    if kind == "top":
        cand = [s for s in floors if not _NAME["roof"].search(nm(s))
                and not (_NAME["attic"].search(nm(s)) and not rooms.get(s.id()))] or floors
        top = cand[-1]
        return top, (f"'top floor' read as storey {top.Name}, the highest storey with rooms" if rooms else
                     f"'top floor' read as storey {top.Name}, the highest storey with doors or windows (the model has "
                     f"no rooms)") + f" (storeys: {names})"
    if kind == "bottom":
        low = floors[0]
        return low, (f"'lowest floor' read as storey {low.Name}, the lowest storey with "
                     + ("rooms" if rooms else "doors or windows") + f" (storeys: {names})")
    if kind in ("upper", "lower"):
        main = [s for s in floors if not _NAME["roof"].search(nm(s)) and not _NAME["basement"].search(nm(s))]
        if len(main) != 2:
            return None, (f"'{'upper' if kind == 'upper' else 'lower'} floor' is clear only in a two-floor building; "
                          f"this model has {len(main)} floors with rooms (storeys: {names})")
        s = main[-1] if kind == "upper" else main[0]
        return s, f"'{kind} floor' read as storey {s.Name}, of the two floors with rooms (storeys: {names})"
    if kind in ("ground", "basement", "attic"):
        named = [s for s in floors if _NAME[kind].search(nm(s))]
        if len(named) == 1:
            return named[0], f"'{kind}' read as storey {named[0].Name} (by its name; storeys: {names})"
        if len(named) > 1:
            return None, f"several storeys could be the {kind}: {', '.join(s.Name for s in named)}"
        if kind == "ground":
            above = [s for s in floors if _elev(s) * M.len_scale >= -0.5 and not _NAME["basement"].search(nm(s))]
            if above:
                g = min(above, key=lambda s: abs(_elev(s)))
                return g, f"'ground floor' read as storey {g.Name}, the lowest floor at or above elevation 0 that is not a basement (storeys: {names})"
        if kind == "basement":
            below = [s for s in floors if _elev(s) * M.len_scale < -1.0]
            if len(below) == 1:
                return below[0], f"'basement' read as storey {below[0].Name}, the one floor below ground (storeys: {names})"
            if len(below) > 1:
                return None, f"several storeys are below ground: {', '.join(s.Name for s in below)}"
        return None, f"no storey of this model is a {kind} (storeys: {names})"
    return None, f"unknown storey word {kind}"


# --------------------------------------------------------------------------- finish (deterministic additions)

SQFT = re.compile(r"\bsq\.?\s?ft\b|\bsquare\s+f(ee|oo)t\b|\bsqft\b|\bft2\b|\bft\^2\b|\bneliojal\w*|\bquadratfu\w*|"
                  r"\bkvadratfot\w*|\bpieds? carres\b")
# English qualifier adjectives anywhere in the question (the rule needs them next to the noun): kind -> pattern
_QUAL_ANY = {
    "fire": r"\bfire[- ]?(rated|resistant|proof|doors?|rating)\b|\bfire\b",
    "sliding": r"\bsliding\b",
    "glazed": r"\bglazed\b|\bglass\b",
    "double": r"\bdouble[- ]?(leaf|doors?|wing)\b",
    "accessible": r"\bwheelchair\b|\baccessible\b|\bbarrier[- ]?free\b",
    "loadbearing": r"\bload[- ]?bearing\b|\bstructural\b",
}
_QUAL_NOT = re.compile(r"\bfire (exit|escape|alarm|extinguisher|place|safety|compartment)|\bglass of\b")


def finish(q: Query, question: str) -> Query:
    """Deterministic additions to any reading (rules or language model): the storey named in words, a qualifier
    the adjacency rule missed, the output unit. Never changes measure, object or variant."""
    if not q.ok():
        return q
    sw = storey_word(question)
    if sw and not q.level and not q.extra.get("whole_model") and not q.extra.get("storey_ref"):
        q.extra["storey_ref"] = {"kind": sw[0], "phrase": sw[1]}
        q.extra["unresolved_scope"] = True
    if q.measure == "area" and SQFT.search(fold(question)):
        q.extra["unit_out"] = "sqft"
    rs = room_scope(q, question) if not quotes(question) else None
    if rs:
        q.extra["room_scope"] = rs
    if q.obj in QUALIFIER_OBJS and q.measure in ("count", "area", "length", "volume") and not q.extra.get("qualifier") \
            and not quotes(question) and not q.extra.get("compound"):
        f = fold(strip_quoted(question))
        if not _QUAL_NOT.search(f) and not _SCHEDULE_Q.search(f) and not q.extra.get("unresolved_scope"):
            for kind, pat in _QUAL_ANY.items():
                m = re.search(pat, f)
                if m:
                    # the rule's own reading first (it knows the other languages), then the broad one
                    q2 = attach_qualifier(question, Query(measure=q.measure, obj=q.obj))
                    found = q2.extra.get("qualifiers") or [{"kind": kind, "raw": m.group(0)}]
                    q.extra["qualifier"], q.extra["qualifiers"] = found[0], found
                    q.extra["qualifier_broad"] = True
                    break
    return q


# --------------------------------------------------------------------------- gaps (why ask the language model)

# compound room words in fi / sv / de whose type the vocabulary may not know ("pukuhuoneiden", "gastrummet",
# "Hauswirtschaftsraum"); the whole word is checked against the room-type vocabulary
_COMPOUND_ROOM = re.compile(r"\b([a-z]{3,}(?:huone|tila|komero)(?:en|n|iden|jen|et|t|ita|a|ssa|an|ita|issa)?)\b|"
                            r"\b([a-z]{3,}(?:rum|rummet|rummen|rummens|rummets))\b|"
                            r"\b([a-z]{3,}(?:zimmer|zimmers|raum|raume|raumes|raeume))\b")
_GENERIC_PREFIX = re.compile(r"^(kaikki|kaikkien|asuin|sisa|ulko|koko|huone|eri|muu|muiden|jokai|niiden|naiden|"
                             r"alla|samt|alle|aller|einzel|jede|wohn|neben|innen|aussen|ruum|tila|lisa|kahden|"
                             r"kolmen|yhden|kahdeksan|neljan|viiden)")


def unknown_compound_room(question: str) -> str | None:
    """A compound room word the room-type vocabulary does not know, or None."""
    from .takeoff import _Q_TYPE_RE, LABEL_RE
    f = fold(strip_quoted(question))
    for m in _COMPOUND_ROOM.finditer(f):
        w = next(g for g in m.groups() if g)
        if _GENERIC_PREFIX.match(w):
            continue
        if any(rx.search(w) for rx in _Q_TYPE_RE.values()):
            continue
        if re.fullmatch(r"(huone|tila|rum|zimmer|raum)\w*", w):
            continue
        # a word the drawing labels know ("kodinhoitohuone", "vaatehuone", "abstellraum")
        stem = re.sub(r"(en|n|iden|jen|et|t|ita|a|ssa|an|issa|met|men|mens|mets|s|e|es)$", "", w)
        if any(re.fullmatch(rf"(?:{v})", stem) or re.fullmatch(rf"(?:{v})", w) for v in LABEL_RE.values()):
            continue
        return w
    return None


def gaps(q: Query, question: str) -> str | None:
    """Why a confident rule reading (after `finish`) is still incomplete, or None. Never for quoted questions (the
    dataset templates), which the caller leaves to the rules."""
    from . import phrasing
    if not q.ok() or q.extra.get("room_scope"):
        return None                       # a count inside one room: the room type is the scope, read by finish()
    f = fold(strip_quoted(question))
    tk = q.extra.get("takeoff")
    # a room type the question names that the reading leaves out
    named = phrasing._named_types(question)
    if tk is not None:
        have = set(tk.get("types") or ())
        if tk.get("group"):
            have.add(tk["group"])
        miss = named - have - {"stair"}
        if miss:
            return f"the question names room type(s) {sorted(miss)} that the reading leaves out"
    elif q.measure == "count" and q.obj in phrasing._OBJ_TO_TYPE:
        miss = named - {phrasing._OBJ_TO_TYPE[q.obj], "stair"}
        if q.obj == "bathroom":
            miss -= {"wc"}
        if miss and not re.search(r"\b(with|having|containing|mit|med|jossa|joissa|avec|con)\b", f):
            return f"a count of {q.obj}s was read, but the question also names {sorted(miss)}"
    # a synonym the reader knows and the rules do not ("sleeping rooms", "wet room"): its type must be in the reading
    have = set((tk or {}).get("types") or ()) | ({phrasing._OBJ_TO_TYPE.get(q.obj)} if q.obj else set())
    f_in = _NOT_COUNTING.sub(" ", f)
    for k, v in phrasing.TYPE_SYN.items():
        if q.measure not in ("count", "area"):
            break
        if " " in k and v not in have and v != "wet" and re.search(rf"\b{re.escape(k)}s?\b", f_in):
            return phrasing.BLOCKING[1] + f" ('{k}', {v})"
    # a room word the vocabulary does not know, under a reading of rooms of any kind
    if q.obj == "room" and q.measure in ("count", "area") and not q.label and not q.room_no \
            and q.variant not in ("type_sum", "net_indoor_total"):
        w = unknown_compound_room(question)
        if w:
            return phrasing.BLOCKING[0] + f" ('{w}')"
    # an area or takeoff without a room type or object, where the question has a size word and a room-ish noun
    if q.measure == "area" and q.obj is None:
        return "an area was read without a room type or object"
    return None


def storey_scope(ctx, query: Query) -> tuple[str | None, str]:
    """(storey name, note) for the storey the question names in words, or (None, why it cannot be told)."""
    sr = query.extra.get("storey_ref") or {}
    phrase = sr.get("phrase", "storey")
    path = ctx.model_ifc()
    if path is None:
        return None, (f"the question names the {phrase}, but this drawing is one plan with no building model behind it, "
                      f"so which storey that is cannot be told from it")
    from .routes import ifc_route
    st, note = resolve_storey(ifc_route.load(str(path)), sr.get("kind", ""))
    return (st.Name, note) if st is not None else (None, f"the question names the {phrase}: {note}")


# "not counting separate WC rooms", "except the sauna": the words after it are left out on purpose
_NOT_COUNTING = re.compile(r"\b(not counting|not including|excluding|except|without|apart from|other than|ohne|ausser|"
                           r"paitsi|lukuun ottamatta|pois lukien|ilman|utom|utan|exklusive|sauf|hors|tranne)\b[^)?.;]*")


# --------------------------------------------------------------------------- a count inside one room

# element counts that can be asked for one room ("windows in the living room", "ikkunaa olohuoneessa")
_ROOM_SCOPED_OBJS = {"door", "window", "toilet", "sink", "washbasin", "shower", "bathtub", "shower_or_bath", "column",
                     "beam", "stair", "wall"}


def room_scope(q: Query, question: str) -> dict | None:
    """{type, phrase} when an element count is asked inside a room type, else None. QA 14 Nov: "Montako ikkunaa
    olohuoneessa on?" counted every window on the plan."""
    from .takeoff import Q_TYPE_RE, EXCLUDE
    if q.measure != "count" or q.obj not in _ROOM_SCOPED_OBJS:
        return None
    f = EXCLUDE.sub(" ", fold(strip_quoted(question)))
    if _SCHEDULE_Q.search(f):
        return None                       # a drawing sheet or schedule question: the schedule routes read rooms there
    for t, rx in Q_TYPE_RE.items():
        if t == "wet" or (t == "wc" and q.obj == "toilet"):
            continue
        pats = (rf"\b(?:in|inside|within)\s+(?:the\s+|this\s+|that\s+|each\s+|every\s+|all\s+(?:the\s+)?|a\s+|my\s+)?"
                rf"(?:\w+\s+)?(?:{rx})",
                rf"(?:{rx})\w*?ss[a]\b",                                     # fi inessive: olohuoneessa, keittiossa
                rf"\b(?:im|in\s+der|in\s+den|in\s+dem)\s+(?:\w+\s+)?(?:{rx})",  # de
                rf"\bi\s+(?:\w+\s+)?(?:{rx})")                                # sv / da
        for p in pats:
            m = re.search(p, f)
            if m:
                return {"type": t, "phrase": m.group(0).strip()}
    return None
