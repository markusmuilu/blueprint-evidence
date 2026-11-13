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
COUNT = r"\b(how many|wie viele|combien|quant[ie]|kuinka monta|hvor mange|mitu)\b"
AREA = r"(area|flache|surface|superficie|nettoareal|areal|pind|pinta-ala)"
LENGTH = r"(length|lange|laengde|langde|longueur|lunghezza|pituus|wie lang)"
VOLUME = r"(volume|volumen|volym|tilavuus|maht)"

# Object vocabulary. Order matters: the first match wins, so specific phrases come first.
OBJECTS: list[tuple[str, str]] = [
    ("wc_room", r"separate wc rooms|erillista wc-tilaa"),
    ("bathroom", r"bathrooms or washrooms|kylpy- tai pesuhuone"),
    ("sink", r"sinks and wash basins|pesu- ja tiskiallas"),
    ("shower_or_bath", r"showers and bathtubs|suihkua ja kylpyammetta"),
    ("toilet", r"water closets|toilet bowls|wc-istuin|toilets \(wc\)|\bwcs\b|combien de wc|quanti wc|\bwc\b sont|toilets exist"),
    ("washbasin", r"lavatories|washbasin|lavabo|lavabi"),
    ("bathtub", r"bathtub|badewanne|baignoire|vasche da bagno|kylpyamme"),
    ("shower", r"shower|dusche|douche|docce|suihku"),
    ("sauna", r"sauna"),
    ("kitchen", r"kitchens|keittiota"),
    ("bedroom", r"bedrooms|makuuhuone"),
    ("balcony", r"balconies|parveke"),
    ("storey", r"floors \(storeys\)|kerrosta"),
    ("apartment", r"apartment|wohnung|appartement|appartament|residential unit|unite abitativ"),
    ("door", r"\bdoors?\b|turen|\bportes?\b|\bporte\b|\bdore\b|\bust\b|\bovea\b|\bovi"),
    ("window", r"windows?|fenster|fenetres|finestre|vinduer|akent|ikkuna"),
    ("wall", r"walls?\b|wande|vaegge|vaegge|vagge|seina"),
    ("column", r"columns|saojler|sojler|posti\b|stutzen|saulen"),
    ("beam", r"beams|bjaelker|bjelker|tala\b|trager"),
    ("stair", r"stairs|trapper|treppi|treppen"),
    ("slab", r"slabs|daek og plader|decken und bodenplatten"),
    ("sanitary", r"sanitary fixtures"),
    ("room", r"\brooms?\b|raume|raum\b|pieces?\b|piece\b|locali|locale\b|\brum\b|rummet|ruumi|ruumide|spaces"),
]

QUOTE_RE = re.compile(
    r"(?:(?<=[\s(])'(.+?)'(?=[\s,?)!.:]|$))"   # 'x'  (inner apostrophes allowed)
    r"|«\s*(.+?)\s*»"                            # «x» and « x »
    r"|„(.+?)[“”]"                               # „x“
    r"|“(.+?)”"
)


def quotes(q: str) -> list[str]:
    return [next(g for g in m.groups() if g is not None) for m in QUOTE_RE.finditer(q)]


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

        for name, pat in OBJECTS:
            if re.search(pat, f):
                q.obj = name
                break

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
        if q.obj == "room" and q.measure == "area" and not q.room_no and not q.label:
            q.variant = "total"
        if re.search(r"numbered indoor|nummerierten innen|interieures numerotees|interni numerati", f):
            q.obj, q.variant = "room", "net_indoor_total"
        if q.obj == "wall" and q.measure == "area":
            q.variant = "side_area"
        if q.scope == "model" and qs:
            q.extra["model_name"] = qs[0]
        return q


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
