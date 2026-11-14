"""Natural-language question -> typed query.

A small multilingual keyword/rule layer (EN, DE, FR, IT, FI, DA, ET) runs first. It is free
and deterministic. `QueryParser` is the interface: a cheap LLM parser can be plugged in later
as a fallback for questions the rules leave unparsed (see `LLMParser`). No API calls are made.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field


@dataclass
class Query:
    measure: str | None = None        # count | area | length | volume | text
    obj: str | None = None            # canonical object, e.g. door, window, room, apartment
    label: str | None = None          # room label / room name filter
    room_no: str | None = None        # room number, e.g. 2.10
    apartment: str | None = None      # apartment letter, e.g. A
    level: str | None = None          # storey / level name (IFC)
    scope: str = "drawing"            # drawing | level | model
    variant: str | None = None        # e.g. side_area, net_indoor
    parser: str = "rules"
    lang: str | None = None
    raw: str = ""
    extra: dict = field(default_factory=dict)

    def ok(self) -> bool:
        return self.measure is not None and self.obj is not None

    def as_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw")
        return {k: v for k, v in d.items() if v not in (None, {}, "")}


def fold(s: str) -> str:
    s = s.lower().replace("æ", "ae").replace("ø", "o").replace("ß", "ss").replace("œ", "oe")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


# Count / measure cues. Checked on accent-folded lower-case text.
COUNT = (r"\b(how many|wie viele|wieviele|combien|quant[ie]|kuinka monta|montako|hvor mange|mitu|hur manga|"
         r"^number of|count the|anzahl|antalet|antal|lukumaara|raekna|rakna|zahl mal|zahl die)\b")
AREA = r"(area|flache|surface|superficie|nettoareal|areal|pind|pinta-ala|\byta\b|\bytan\b|golvyta)"
LENGTH = r"(length|lange|laengde|langde|longueur|lunghezza|pituus|wie lang)"
VOLUME = r"(volume|volumen|volym|tilavuus|maht)"

# Object vocabulary. Order matters: the first match wins, so specific phrases come first.
OBJECTS: list[tuple[str, str]] = [
    ("bathroom", r"bathrooms or washrooms|kylpy- tai pesuhuone"),
    ("wc_room", r"separate wc rooms|erillista wc-tilaa"),
    ("sink", r"sinks and wash basins|pesu- ja tiskiallas|\bsinks?\b|tiskialta|tiskiallas|diskho|\bvask|spulbecken|\bspulen?\b"),
    ("shower_or_bath", r"showers and bathtubs|suihkua ja kylpyammetta"),
    ("toilet", r"water closets|toilet bowls|wc-istuin|toilets \(wc\)|\bwcs\b|combien de wc|quanti wc|\bwc\b sont|toilets exist"),
    ("washbasin", r"lavatories|washbasin|lavabo|lavabi|wash ?basins?|hand ?basins?|pesuallas|pesualtaa|pesualtaita|"
                  r"handfat|tvattstall|waschbecken|waschtisch"),
    ("sink", r"\bsinks?\b"),
    ("bathtub", r"bathtub|badewanne|baignoire|vasche da bagno|kylpyamme"),
    ("shower", r"shower|dusche|douche|docce|suihku"),
    ("sauna", r"sauna"),
    ("kitchen", r"kitchens|keittiota"),
    ("bedroom", r"bedrooms|makuuhuone"),
    ("balcony", r"balconies|parveke"),
    ("storey", r"floors \(storeys\)|kerrosta"),
    ("apartment", r"apartment|wohnung|appartement|appartament|residential unit|unite abitativ|lagenhet|huoneisto|\basuntoa?\b"),
    ("door", r"\bdoors?\b|turen|\bportes?\b|\bporte\b|\bdore\b|\bust\b|\bovea\b|\bovi|"
             r"\b(?:palo|liuku|lasi|pari|sisa|ulko)-?ov(?:i\w*|ea)\b|\w*dorr(?:ar|en)?\b"),
    ("window", r"windows?|fenster|fenetres|finestre|vinduer|akent|ikkuna|fonster"),
    ("wall", r"walls?\b|wande|vaegge|vaegge|vagge|seina|\bvagg"),
    ("column", r"columns|saojler|sojler|posti\b|stutzen|saulen|pelare|pilari|pilareit"),
    ("beam", r"beams|bjaelker|bjelker|tala\b|trager|balkar|palkki|unterzu"),
    ("stair", r"stairs|trapper|treppi|treppen|trappor|porras|portaik"),
    ("slab", r"slabs|daek og plader|decken und bodenplatten|plattor|bjalklag|bodenplatte|geschossdecke|\bdecken\b"),
    ("sanitary", r"sanitary fixtures"),
    ("room", r"\brooms?\b|raume|raum\b|pieces?\b|piece\b|locali|locale\b|\brum\b|rummet|rummen\b|ruumi|ruumide|spaces|huonetta|huoneita|huoneiden|\bhuoneet\b"),
]

# Room types a count question can name without the fixed dataset phrasing above ("how many bedrooms?",
# "combien de chambres", "wie viele Badezimmer"): the takeoff room-type words (bpqa/takeoff.py, EN FI DE
# FR IT) mapped to the count objects the routes know. Only used when no object above matched, or only
# the generic "room".
COUNT_ROOM_TYPES = {"bedroom": "bedroom", "kitchen": "kitchen", "bathroom": "bathroom", "sauna": "sauna",
                    "balcony": "balcony", "wc": "wc_room"}
# "toilets" alone is ambiguous: the fixture (Swiss and CubiCasa questions: "toilets (WC)", "toilet bowls")
# or the room (IFC questions: "how many toilets on the ground floor" answered with WC spaces). It is read as
# the object toilet with extra["toilet_ambiguous"]; each route applies its dataset's convention. A named
# room ("toilet rooms", "restrooms", "WC-tila") is a WC room.
WC_ROOM_WORDS = re.compile(r"\b(wc|toilet|toilette|toiletten)s?[ -]?(rooms?|til[aoe]\w*|raume?|raeume)\b|restrooms?|powder rooms?|"
                           r"\bvesso|\bvessa(?!npont)|separate wc|erillis\w* wc|tualettruum|toalett ?rum|\bwc-?rum")
STOREY_COUNT = re.compile(r"\b(floors|levels|storeys|stories|stockwerke|geschosse|etagen|vaningar|vaningarna)\b|"
                          r"\bhur manga plan\b|\bkerrosten\b|\bkerroksia\b")
COUNT_BREAKDOWN = re.compile(r"\b(types?|distribut\w*|by|per|each|across|grouped|along|and what|and how)\b")
SCALE_Q = re.compile(r"\bscale\b|massstab|\bechelle\b|\bscala\b|mittakaava|maalestik|\bmalestok|skala\b")


def storey_count_obj(q, f_obj: str) -> str | None:
    """'storey' for a plain count that names no other object ("how many floors does the building have")."""
    if q.measure == "count" and q.obj is None and STOREY_COUNT.search(f_obj) and not COUNT_BREAKDOWN.search(f_obj):
        return "storey"
    return None


def count_room_type(f_obj: str) -> tuple[str | None, bool]:
    """(count object, toilet_ambiguous) for a count question naming one room type, else (None, False)."""
    from .takeoff import EXCLUDE, Q_TYPE_RE
    f = EXCLUDE.sub(" ", f_obj)
    types = [t for t, rx in Q_TYPE_RE.items() if t in COUNT_ROOM_TYPES and re.search(rx, f)]
    if len(types) != 1:
        return None, False
    if types[0] == "wc":
        return ("wc_room", False) if WC_ROOM_WORDS.search(f) else ("toilet", True)
    return COUNT_ROOM_TYPES[types[0]], False


QUOTE_RE = re.compile(
    r"(?:(?<=[\s(])'(.+?)'(?=[\s,?)!.:]|$))"   # 'x'  (inner apostrophes allowed)
    r"|«\s*(.+?)\s*»"                            # «x» and « x »
    r"|„(.+?)[“”]"                               # „x“
    r"|“(.+?)”"
)


def quotes(q: str) -> list[str]:
    return [next(g for g in m.groups() if g is not None) for m in QUOTE_RE.finditer(q)]


# Quoted names (a model, building, storey or room name) must not be read as the asked object:
# "doors in the model '10 Appartementen Schependomlaan'" asks for doors, not apartments.
DQUOTE_RE = re.compile(r'(?<![\w])"([^"]{1,120})"(?![\w])')


def strip_quoted(q: str) -> str:
    return DQUOTE_RE.sub(" ", QUOTE_RE.sub(" ", q))


# "the room 'X'", "space «X»", "Raum 'X'": the quote names one room, it is not a model or storey
ROOM_BEFORE_QUOTE = re.compile(r"\b(room|space|raums?|raumes|zimmers?|piece|locale|stanza|huone|ruum|ruumi|rum|tila|lokaal)"
                               r"\s*(?:named|called)?\s*$")
# one room asked for, not a sum: "the room 'X'", "the living room", "the kitchen" (English, singular)
PLURAL_SCOPE = re.compile(r"\b(all|every|each|total|sum|together|combined|alle|tous|tutti|kaikki)\b")


# A question that names the whole model or building asks for whole-model numbers, even when it is
# asked on a single-storey plan sheet: "in the model", "in the building", "whole building", and the
# same in NL, ES, DA, DE, FR, IT, FI, ET. A quoted model name does not count (strip_quoted first).
WHOLE_MODEL = re.compile(
    r"\b(model|modell?\w*|modelo|modele|modello|mudel\w*|malli\w*)\b"
    r"|\b(in|of|throughout|across|within|inside) (the |this )?(whole |entire |complete |overall )?building\b"
    r"|\b(whole|entire|complete|overall|total) building\b|\ball (the )?(floors|storeys|levels)\b"
    r"|\b(gebouw\w*|gebaude\w*|gebaeude\w*|edificio\w*|batiment\w*|bygning\w*|rakennu\w*|hoone\w*|kogu maja)\b")


def whole_model_asked(question: str) -> bool:
    return bool(WHOLE_MODEL.search(fold(strip_quoted(question))))


def detect_lang(f: str) -> str:
    for lang, cue in [("fi", r"kuinka|pohjapiirustu"), ("de", r"wie |grundriss|geschoss|wohnung"),
                      ("fr", r"combien|quelle|piece|etage"), ("it", r"quant|qual |pianta|locale"),
                      ("da", r"hvor mange|hvad er|plantegning|modellen"), ("et", r"mitu |kui suur|korrus")]:
        if re.search(cue, f):
            return lang
    return "en"


class QueryParser:
    name = "base"

    def parse(self, question: str) -> Query:  # pragma: no cover - interface
        raise NotImplementedError


class RuleParser(QueryParser):
    name = "rules"

    def parse(self, question: str) -> Query:
        q = Query(raw=question)
        f = fold(question)
        q.lang = detect_lang(f)
        qs = quotes(question)

        if re.search(COUNT, f):
            q.measure = "count"
        elif re.search(VOLUME, f):
            q.measure = "volume"
        elif re.search(LENGTH, f):
            q.measure = "length"
        elif re.search(AREA, f):
            q.measure = "area"
        elif re.search(r"\b(what|which|could you)\b", f):
            q.measure = "text"

        f_obj = fold(strip_quoted(question))
        for text in (f_obj, f):      # the quoted names only when nothing outside them names an object
            for name, pat in OBJECTS:
                if re.search(pat, text):
                    q.obj = name
                    break
            if q.obj:
                break

        # "how many floors does the building have": storeys, only for a plain count that names no other object
        q.obj = storey_count_obj(q, f_obj) or q.obj
        # "how many bathrooms does the apartment have": the apartment is the scope, not the object
        apt_scope = q.obj == "apartment" and not re.search(
            r"apartments|wohnungen|appartements|appartamenti|residential units|unita abitative", f_obj)
        if q.measure == "count" and (q.obj in (None, "room") or apt_scope) and not (qs and re.search(
                r"labelled|bezeichnung|designation|denominazione|denominati|designees|named", f)):
            obj, amb = count_room_type(f_obj)
            if obj:
                q.obj = obj
                if amb:
                    q.extra["toilet_ambiguous"] = True
        # "what is the scale of this drawing?": the drawing scale (read off the sheet)
        if q.obj is None and q.measure in ("text", None) and SCALE_Q.search(f_obj):
            q.measure, q.obj = "text", "scale"

        # Swiss-style room number "room 2.10 ('Zimmer')" in all languages
        m = re.search(r"(?:room|raum|piece|locale|stanza)\s+([A-Z]?\d+(?:\.\d+)?)\s*\(", fold(question), re.I)
        if m:
            q.room_no = m.group(1).upper()
            q.obj = "room"
        m = re.search(r"(?:apartment|wohnung|appartement|appartamento|dell'appartamento)\s+([A-Z])\b", question, re.I)
        if m and q.measure == "area":
            q.apartment = m.group(1).upper()
            q.obj = "apartment"

        # Scope and filters
        if re.search(r"in the model|in modellen|mudelis|enthalt das modell", f):
            q.scope = "model"
        if re.search(r"labelled|bezeichnung|designation|denominazione|denominati|designees|named", f) and qs:
            q.label = qs[0]
            q.obj = "room"
        if re.search(r"floor plan of|plantegningen for|korruse|on '|pa '|korrusel|im grundriss|im geschoss", f) and qs:
            q.scope = "level"
            q.level = qs[-1]
            if q.obj == "room" and q.measure == "area" and len(qs) >= 2:
                q.label = qs[0]
        if q.measure == "area" and q.obj == "room" and q.room_no and qs:
            q.label = qs[0]
        # "the net floor area of the room 'living room' in the model": the quote right after "room"
        # names one room. Without this the question was read as the total of all rooms.
        room_quote = None
        for mq in QUOTE_RE.finditer(question):
            if ROOM_BEFORE_QUOTE.search(fold(question[:mq.start()])):
                room_quote = next(g for g in mq.groups() if g is not None)
                break
        if room_quote and q.measure == "area" and q.obj == "room" and not q.room_no:
            q.label = q.label or room_quote
            if not PLURAL_SCOPE.search(fold(strip_quoted(question))) and not re.search(r"\brooms\b", f_obj):
                q.extra["single_room"] = True
        if q.obj == "room" and q.measure == "area" and not q.room_no and not q.label:
            q.variant = "total"
        if re.search(r"numbered indoor|nummerierten innen|interieures numerotees|interni numerati", f):
            q.obj, q.variant = "room", "net_indoor_total"
        if q.obj == "wall" and q.measure == "area":
            q.variant = "side_area"
        ext = re.search(r"\b(exterior|outside|external|outer)\b", f)
        inn = re.search(r"\b(interior|inside|internal|inner)\b", f)
        if ext and not inn:
            q.extra["external"] = True
        elif inn and not ext and q.obj in ("wall", "door") and not re.search(r"together|entrance", f):
            q.extra["external"] = False
        if q.obj == "slab" and re.search(r"floor slabs", f):
            q.extra["slab_type"] = "FLOOR"
        # Compound or breakdown questions want a table, not one number: leave them to a text route.
        compound = re.search(r"\b(types?|breakdown|each|with their|compared|list|distribution|distributed|grouped|"
                             r"along with|and what|which)\b", f)
        schedule_filter = re.search(r"door type [a-z]\d|labelled|bezeichnung|designation|denominazione", f)
        if compound and q.measure != "text" and not schedule_filter:
            q.extra["compound"] = True
        # A scope named without quotes ("on level 1", "ground floor", "north facade", "house A")
        # cannot be resolved by the rules; better to abstain than to answer for the whole building.
        if not qs and re.search(r"\b(on|in) (the )?(level|floor|ground|basement|first|second|underground|"
                                r"third|fourth|fifth|sixth|top|upper|lower|attic|north|south|east|west|house)\b", f):
            q.extra["unresolved_scope"] = True
        if whole_model_asked(question):
            q.extra["whole_model"] = True
        if q.scope == "model" and qs:
            names = [x for x in qs if x != room_quote]
            if names:
                q.extra["model_name"] = names[0]
        # Quantity takeoff ("how many m2 of tiling for the bathrooms", "kuinka monta neliota laattaa
        # kylpyhuoneisiin"): a room-type area sum. Only when nothing more specific was read (a quoted
        # label, a room number, an apartment, the numbered-indoor total).
        if not qs and not q.room_no and not q.apartment and q.variant != "net_indoor_total":
            from .takeoff import parse_takeoff
            tk = parse_takeoff(question)
            if tk is not None:
                q.measure, q.obj, q.variant, q.label = "area", "room", "type_sum", None
                # a breakdown per room is part of every takeoff answer, so "with a breakdown by
                # bathroom" is answered; a named sub-building ("house A") is not resolvable
                q.extra.pop("compound", None)
                if tk.get("scope_named"):
                    q.extra["unresolved_scope"] = True
                if single_type_asked(f, tk):
                    # a singular room TYPE ("the kitchen"): several matches are summed and said so (estimate),
                    # unlike a quoted room name, which keeps the abstaining guard (routes/scope_guard.py)
                    tk["single_type"] = True
                q.extra["takeoff"] = tk
        return attach_qualifier(question, q)


def single_type_asked(f: str, tk: dict) -> bool:
    """English 'the area of the living room' / 'tiles for the kitchen' names one room; 'the bathrooms',
    'all bedrooms' a sum. Other languages: not decided here (no flag, the sum stands)."""
    if len(tk["types"]) != 1 or tk.get("group") or PLURAL_SCOPE.search(f):
        return False
    from .takeoff import Q_TYPE_RE
    rx = Q_TYPE_RE.get(tk["types"][0])
    if not rx:
        return False
    hits = [m.group(0).strip() for m in re.finditer(rx, f)]
    if not hits or any(h.endswith("s") for h in hits):
        return False
    return bool(re.search(r"\b(the|this|that|my|our)\s+(\w+\s+)?" + re.escape(hits[0]), f))


class LLMParser(QueryParser):
    """Placeholder for a cheap LLM fallback (e.g. a small model returning Query JSON).

    Contract: given the question text, return a Query with parser='llm' and an estimated
    cost in extra['cost_usd']. Only called when the rule parser leaves measure or obj unset.
    Not wired to any API in this build.
    """
    name = "llm"

    def parse(self, question: str) -> Query:
        q = Query(raw=question, parser="llm")
        q.extra["unavailable"] = True
        return q


def parse(question: str, fallback: QueryParser | None = None) -> Query:
    q = RuleParser().parse(question)
    if not q.ok() and fallback is not None:
        q2 = fallback.parse(question)
        if q2.ok():
            return q2
    return q


# ---------------------------------------------------------------------------------------------------
# Qualifiers: words that narrow the counted object ("fire doors", "exterior walls", "sliding doors").
# A qualifier is never dropped silently: the router only lets a route answer when the route can filter
# by it (bpqa/qualifiers.py), otherwise the answer is an abstention that says why.
# Kept apart from RuleParser so a different question parser can call `attach_qualifier` unchanged.
# ---------------------------------------------------------------------------------------------------
QUALIFIER_OBJS = {"door", "window", "wall", "column", "beam", "slab", "stair"}
OBJ_NOUN = (r"(doors?|windows?|walls?|columns?|beams?|slabs?|stairs?|staircases?|openings?|"
            r"portes?|fenetres?|murs?|ovi\w*|ovea|ikkuna\w*|sein\w*|tur\w*|fenster|wand\w*|dorr\w*|dor\w*|"
            r"fonster|vagg\w*|vinduer?|vaegg\w*)")
# Adjective in English / French: must stand next to the object noun (at most two words between), so
# "outside the building" or "glass of water" do not qualify. Compound words (fi, de, sv, da) carry the
# object inside the word and are searched anywhere.
_NEAR = lambda adj: (rf"\b(?:{adj})\b(?:\W+\w+){{0,1}}?\W+{OBJ_NOUN}\b|\b{OBJ_NOUN}\b(?:\W+\w+){{0,1}}?\W+(?:{adj})\b")

# (kind, adjacency-bound words, anywhere words). First matching kind is the primary one.
QUALIFIER_KINDS: list[tuple[str, str, str]] = [
    ("emergency", r"emergency|exits?|escape|egress|sortie de secours|issue de secours",
     r"notausgang\w*|nottur\w*|nottuer\w*|fluchttur\w*|fluchttuer\w*|hataposisu\w*|poistumis\w*|nodutgang\w*|nodutg\w*"),
    ("fire", r"fire|fire[- ]?rated|fire[- ]?resistant|fire[- ]?proof|coupe[- ]?feu|pare[- ]?flammes|resistants? au feu|"
             r"ei ?\d{2,3}|ei[- ]?rated",
     r"palo\w*|ei ?\d{2,3}\b|brandschutz\w*|brandschott\w*|feuerhemm\w*|feuerbestaendig\w*|feuerwiderstand\w*|"
     r"feuerschutz\w*|brand(?:klass|skydd|dorr|dor|cell|sikr|skott|skot)\w*"),
    ("sliding", r"sliding|slide|coulissant\w*|coulissante?s?",
     r"liuku\w*|schiebe\w*|skjut\w*|skyde\w*"),
    ("double", r"double|double[- ]leaf|two[- ]leaf|doubles|deux vantaux|a deux battants|double battant",
     r"doppel\w*|zweifluegel\w*|zweiflugel\w*|dubbel\w*|pariov\w*|parioven\w*"),
    ("glazed", r"glazed|glass|glazing|vitre\w*|vitrees?",
     r"lasi\w*|verglas\w*|glas(?:tur|tuer|dorr|vagg|wand)\w*|glasdor\w*"),
    ("accessible", r"accessible|wheelchair|barrier[- ]?free|inva|pmr|handicap\w*|disabled",
     r"barrierefrei\w*|esteet\w*|rullatori\w*|tillganglig\w*"),
    ("loadbearing", r"load[- ]?bearing|porteurs?",
     r"tragend\w*|kantava\w*|baerande\w*|barande\w*"),
    ("material", r"steel|wooden|wood|timber|aluminium|aluminum|metal|concrete|brick|masonry|acier|bois|beton|brique",
     r"stahl\w*|holz\w*|teras\w*|puu(?:ovi|ikkuna|seina)\w*|betoni\w*|tegel\w*|trae\w*|tra(?:dor|vagg)\w*"),
    ("orientation", r"(?:north|south|east|west)(?:ern)?[- ]?(?:facing|side|facade|elevation|oriented)?",
     r"pohjois\w*|etela\w*|lansi\w*|nordseite\w*|suedseite\w*|ostseite\w*|westseite\w*|nordfassade\w*|suedfassade\w*"),
]
# interior / exterior also drive q.extra["external"] (the IFC IsExternal filter), set in RuleParser above
_EXT_ADJ = r"exterior|external|outer|outside|outdoor|exterieur\w*|exterieures?"
_EXT_ANY = r"ulko\w*|aussen\w*|ytter\w*|utvandig\w*|yder\w*|fassad\w*|facade\w*"
_INT_ADJ = r"interior|internal|inner|inside|indoor|interieur\w*"
_INT_ANY = r"sisa\w*|innen\w*|inner(?:dorr|vagg)\w*|invandig\w*|indvendig\w*"
# a question about one schedule entry or one drawing sheet filters by the schedule's own type codes
# ("door type A1 (swing door)"); the schedule route reads those, so nothing is dropped there
_SCHEDULE_Q = re.compile(r"\bsheet\b|schedule|\b(door|window) types?\b|\btypes? [a-z]{1,2}-?\d|\bmark\b|\btyp\b|"
                         r"\bbezeichnung|designation|labelled")
# the qualifier is the question's own subject, not a filter ("both interior and entrance doors together")
_TOGETHER = re.compile(r"together|zusammen|ensemble|insieme|yhteensa|tillsammans|og\b.*\bsammen|confondu\w*|"
                       r"\b(und|et|and|e|ja|och|og) (eingangs|d'entree|entree|entrance|ingress|sisaan|ytter)\w*|"
                       r"\ball (the )?(doors|windows|walls)\b|\bboth\b|\beither\b")


def attach_qualifier(question: str, q: Query) -> Query:
    """Detect qualifiers attached to the counted object and record them in q.extra.

    extra["qualifier"] = {"kind": "fire", "raw": "fire"} is the first one found, extra["qualifiers"] lists
    all of them. Only element counts and quantities (door, window, wall ...) carry qualifiers; a question
    about a schedule entry or sheet is left alone. Interior / exterior also set extra["external"].
    """
    if q.obj not in QUALIFIER_OBJS or q.measure not in ("count", "area", "length", "volume"):
        return q
    f = fold(strip_quoted(question))
    if _SCHEDULE_Q.search(f) or q.extra.get("compound") or q.extra.get("unresolved_scope"):
        return q
    found: list[dict] = []
    ext = re.search(_NEAR(_EXT_ADJ), f) or re.search(rf"\b(?:{_EXT_ANY})", f)
    inn = re.search(_NEAR(_INT_ADJ), f) or re.search(rf"\b(?:{_INT_ANY})", f)
    ext = ext or re.search(r"\b(exterior|outside|external|outer)\b", f)
    inn = inn or re.search(r"\b(interior|inside|internal|inner)\b", f)
    if ext and inn:             # "exterior and interior walls": both sides asked, no filter
        ext = inn = None
    if ext and not inn:
        found.append({"kind": "external", "raw": ext.group(0).strip()})
        q.extra["external"] = True
    elif inn and not ext and not _TOGETHER.search(f):
        found.append({"kind": "internal", "raw": inn.group(0).strip()})
        q.extra["external"] = False
    for kind, adj, anywhere in QUALIFIER_KINDS:
        m = re.search(_NEAR(adj), f) or re.search(rf"\b(?:{anywhere})", f)
        if m is None and kind == "fire":
            m = re.search(r"\b(?:with|have|having|has|carry|carrying|which|that|rated)\b\W+(?:\w+\W+){0,2}?"
                          r"(fire[- ]?rat\w*|fire[- ]?resist\w*)", f)
        if m is None:
            continue
        raw = m.group(0).strip()
        mm = re.search(rf"(?:{adj}|{anywhere})", raw)
        found.append({"kind": kind, "raw": (mm.group(0) if mm else raw).strip()})
    if found:
        q.extra["qualifier"] = found[0]
        q.extra["qualifiers"] = found
    return q
