"""Question understanding for phrasings the rules do not know: a closed schema, a validated LLM parse.

The rule parser (bpqa/query.py, bpqa/takeoff.py) is fast and deterministic and runs first. This module
adds the second reader for the questions the rules miss ("What's the total size of the bedrooms?",
"montako ikkunaa kuvassa on", "hur manga sovrum finns det"):

  CANONICAL SHAPES  `INTENTS` lists every question shape the routes accept, one entry per intent, and the
                    Query each one builds (measure, obj, variant, extra). The LLM never writes a Query: it
                    picks an intent from this closed list and fills a few slots (room types, material, surface,
                    waste, heights, a quoted name). `build()` turns that into exactly the Query the rules
                    would have built, so the deterministic routes accept it.
  PROMPT            `prompt()` states the schema with its enum values and a few examples; the call is text
                    only, thinking off, a short max_tokens, cached on disk (bpqa/nemotron.py).
  NORMALISER        `normalise()` maps synonyms and the older loose shape ({measure: area, obj: bedroom,
                    variant: total}) onto the canonical intents, checks every value against the schema and
                    against the question text (a name, number, letter or height the question does not
                    contain is dropped or rejected), and returns None for an invalid parse.
  POLICY            `read()`: rules first. The LLM is asked only when the rules produce no parse, a parse
                    that no route can answer (`shape_known`), or a parse that ignores a room type the question
                    names (`weak`). A question with quoted names (the dataset templates) keeps its rule
                    parse. The reply never overrides a rule parse that is routable.

An LLM-read question says so in the Why panel: Query.extra["reading"] is a plain-language sentence
("area of rooms typed bedroom, summed") and Query.parser is "llm".
"""
from __future__ import annotations

import json
import math
import os
import re
from functools import lru_cache

from . import nemotron as N
from .query import Query, QueryParser, RuleParser, fold, quotes, single_type_asked, strip_quoted
from . import takeoff as TK

# --------------------------------------------------------------------------- canonical shapes

# intent -> (measure, obj, variant, what it means). The Query built for each is the one the rules build.
INTENTS: dict[str, tuple[str, str | None, str | None, str]] = {
    "count_door": ("count", "door", None, "doors"),
    "count_window": ("count", "window", None, "windows"),
    "count_toilet": ("count", "toilet", None, "toilets, WCs, toilet bowls, toilet seats (the fixtures)"),
    "count_wc_room": ("count", "wc_room", None, "WC rooms, toilet rooms, restrooms (rooms, not fixtures)"),
    "count_sink": ("count", "sink", None, "sinks, kitchen sinks"),
    "count_washbasin": ("count", "washbasin", None, "wash basins, hand basins, lavatories, lavabos"),
    "count_shower": ("count", "shower", None, "showers only"),
    "count_bathtub": ("count", "bathtub", None, "bathtubs only"),
    "count_shower_or_bath": ("count", "shower_or_bath", None, "showers and bathtubs together"),
    "count_bedroom": ("count", "bedroom", None, "bedrooms"),
    "count_kitchen": ("count", "kitchen", None, "kitchens"),
    "count_bathroom": ("count", "bathroom", None, "bathrooms"),
    "count_sauna": ("count", "sauna", None, "saunas"),
    "count_balcony": ("count", "balcony", None, "balconies, terraces"),
    "count_room": ("count", "room", None, "rooms or spaces of any kind"),
    "count_apartment": ("count", "apartment", None, "apartments, flats, residential units"),
    "count_storey": ("count", "storey", None, "floors, storeys, levels of the building"),
    "count_wall": ("count", "wall", None, "walls"),
    "count_column": ("count", "column", None, "columns, pillars"),
    "count_beam": ("count", "beam", None, "beams"),
    "count_stair": ("count", "stair", None, "stairs, staircases"),
    "count_slab": ("count", "slab", None, "slabs"),
    "count_office": ("count", "office", None, "offices (rooms used as an office)"),
    "count_meeting_room": ("count", "meeting_room", None, "meeting rooms, conference rooms"),
    "area_room_total": ("area", "room", "total", "the total floor area of ALL rooms together"),
    "area_room_largest": ("area", "room", "max", "the area of the biggest / largest room"),
    "area_room_named": ("area", "room", None, "the area of ONE room given by name (name) or number (room_no)"),
    "area_apartment": ("area", "apartment", None, "the area of one apartment given by its letter (apartment)"),
    "area_rooms_of_type": ("area", "room", "type_sum", "floor area summed over the rooms of given types "
                           "(bedrooms, kitchens, bathrooms ...), or the tiles / flooring / paint needed for them"),
    "area_wall": ("area", "wall", "side_area", "the area of walls (building elements, not room surfaces)"),
    "length_wall": ("length", "wall", None, "the total length of walls"),
    "volume_wall": ("volume", "wall", None, "the volume of walls"),
    "volume_slab": ("volume", "slab", None, "the volume of slabs or floors (structure)"),
    "scale": ("text", "scale", None, "the scale of the drawing, e.g. 1:100"),
    "other": (None, None, None, "anything else: lists, types, properties, materials, fire rating, "
              "heights, storey names, comparisons, why/how questions"),
}
COUNT_OBJS = {v[1] for k, v in INTENTS.items() if k.startswith("count_")}
ROOM_TYPES = ["bathroom", "wc", "sauna", "kitchen", "bedroom", "living", "dining", "hall", "utility", "storage",
              "balcony", "stair", "office", "meeting", "wet"]
MATERIALS = ["tiles", "flooring", "paint", "waterproofing", "screed"]
SURFACES = ["floor", "walls", "floor+walls", "ceiling"]

_OBJ_TO_INTENT = {v[1]: k for k, v in INTENTS.items() if k.startswith("count_")}
# a count object the rules know but the routes treat as a room type: "bedroom" -> takeoff type bedroom
_OBJ_TO_TYPE = {"bedroom": "bedroom", "kitchen": "kitchen", "bathroom": "bathroom", "sauna": "sauna",
                "balcony": "balcony", "wc_room": "wc", "office": "office", "meeting_room": "meeting"}
_TYPE_TO_COUNT = {v: k for k, v in _OBJ_TO_TYPE.items()}

TYPE_SYN = {
    "bedroom": "bedroom", "bedrooms": "bedroom", "bed room": "bedroom", "sleeping room": "bedroom", "mh": "bedroom",
    "bathroom": "bathroom", "bathrooms": "bathroom", "bath": "bathroom", "washroom": "bathroom", "washrooms": "bathroom",
    "shower room": "bathroom", "kh": "bathroom", "ph": "bathroom", "kylpyhuone": "bathroom",
    "wc": "wc", "wcs": "wc", "toilet": "wc", "toilets": "wc", "toilet room": "wc", "restroom": "wc", "wc room": "wc",
    "sauna": "sauna", "saunas": "sauna",
    "kitchen": "kitchen", "kitchens": "kitchen", "kitchenette": "kitchen",
    "living": "living", "living room": "living", "living rooms": "living", "lounge": "living", "oh": "living",
    "dining": "dining", "dining room": "dining",
    "hall": "hall", "hallway": "hall", "corridor": "hall", "entrance": "hall", "hall / corridor": "hall",
    "utility": "utility", "utility room": "utility", "laundry": "utility", "utility / laundry": "utility",
    "storage": "storage", "storeroom": "storage", "closet": "storage", "storage / closet": "storage",
    "balcony": "balcony", "balconies": "balcony", "terrace": "balcony", "balcony / terrace": "balcony",
    "stair": "stair", "stairs": "stair", "staircase": "stair",
    "wet": "wet", "wet room": "wet", "wet rooms": "wet", "wet areas": "wet",
    "office": "office", "offices": "office", "meeting": "meeting", "meeting room": "meeting",
    "meeting rooms": "meeting", "conference room": "meeting", "sitting room": "living",
}
MATERIAL_SYN = {
    "tiles": "tiles", "tile": "tiles", "tiling": "tiles", "ceramic": "tiles", "tiles_floor": "tiles",
    "flooring": "flooring", "floor covering": "flooring", "floor finish": "flooring", "parquet": "flooring",
    "laminate": "flooring", "vinyl": "flooring", "carpet": "flooring",
    "paint": "paint", "painting": "paint",
    "waterproofing": "waterproofing", "waterproof": "waterproofing", "membrane": "waterproofing",
    "screed": "screed",
}
SURFACE_SYN = {
    "floor": "floor", "floors": "floor", "walls": "walls", "wall": "walls", "ceiling": "ceiling",
    "ceilings": "ceiling", "floor+walls": "floor+walls", "floor and walls": "floor+walls",
    "walls+floor": "floor+walls", "floor_and_walls": "floor+walls", "both": "floor+walls",
}
# loose intent names a model may produce -> canonical
INTENT_ALIAS = {
    "count_wc": "count_toilet", "count_toilets": "count_toilet", "count_wcs": "count_toilet",
    "count_toilet_bowl": "count_toilet", "count_basin": "count_washbasin", "count_basins": "count_washbasin",
    "count_wash_basin": "count_washbasin", "count_sinks": "count_sink", "count_doors": "count_door",
    "count_windows": "count_window", "count_rooms": "count_room", "count_bedrooms": "count_bedroom",
    "count_kitchens": "count_kitchen", "count_bathrooms": "count_bathroom", "count_saunas": "count_sauna",
    "count_balconies": "count_balcony", "count_apartments": "count_apartment", "count_flats": "count_apartment",
    "count_storeys": "count_storey", "count_floors": "count_storey", "count_levels": "count_storey",
    "count_walls": "count_wall", "count_columns": "count_column", "count_beams": "count_beam",
    "count_stairs": "count_stair", "count_staircases": "count_stair", "count_slabs": "count_slab",
    "count_showers": "count_shower", "count_bathtubs": "count_bathtub", "count_baths": "count_bathtub",
    "count_shower_or_bathtub": "count_shower_or_bath", "count_showers_and_bathtubs": "count_shower_or_bath",
    "count_offices": "count_office", "count_meeting_rooms": "count_meeting_room", "count_meeting": "count_meeting_room",
    "area_largest_room": "area_room_largest", "area_biggest_room": "area_room_largest", "largest_room": "area_room_largest",
    "biggest_room": "area_room_largest", "area_room_max": "area_room_largest",
    "count_restroom": "count_wc_room", "count_wc_rooms": "count_wc_room", "count_toilet_rooms": "count_wc_room",
    "area_total": "area_room_total", "area_all_rooms": "area_room_total", "total_room_area": "area_room_total",
    "area_rooms_total": "area_room_total", "area_room": "area_room_named", "area_named_room": "area_room_named",
    "area_rooms_by_type": "area_rooms_of_type", "area_room_type": "area_rooms_of_type",
    "area_room_types": "area_rooms_of_type", "takeoff": "area_rooms_of_type", "area_takeoff": "area_rooms_of_type",
    "area_type_sum": "area_rooms_of_type", "type_sum": "area_rooms_of_type", "material_takeoff": "area_rooms_of_type",
    "area_walls": "area_wall", "wall_area": "area_wall", "length_walls": "length_wall", "wall_length": "length_wall",
    "volume_walls": "volume_wall", "volume_slabs": "volume_slab", "drawing_scale": "scale",
    "unknown": "other", "none": "other", "unsupported": "other", "property_lookup": "other", "text": "other",
}


def canonical_intent(s) -> str | None:
    if not isinstance(s, str):
        return None
    k = re.sub(r"[\s\-]+", "_", s.strip().lower())
    k = INTENT_ALIAS.get(k, k)
    return k if k in INTENTS else None


# --------------------------------------------------------------------------- prompt

SYSTEM = ("You map questions about building drawings (any language) onto a closed query schema. You answer with "
          "one JSON object and nothing else: no markdown, no explanation.")

EXAMPLES = [
    ("Mikä on makuuhuoneiden yhteenlaskettu pinta-ala?",
     {"intent": "area_rooms_of_type", "types": ["bedroom"]}),
    ("tell me how many loos there are",
     {"intent": "count_toilet"}),
    ("Wie viel Quadratmeter Fliesen brauche ich fuer Bad und WC, Wandhoehe 2,4 m, 12 % Verschnitt?",
     {"intent": "area_rooms_of_type", "types": ["bathroom", "wc"], "material": "tiles", "surface": "walls",
      "waste_pct": 12, "ceiling_m": 2.4, "order": True}),
    ("Hur stor är lägenhet C?", {"intent": "area_apartment", "apartment": "C"}),
    ("How big is the room 'Studio 4' on level 'L2'?",
     {"intent": "area_room_named", "name": "Studio 4", "level": "L2"}),
    ("Which fire rating do the doors have?", {"intent": "other"}),
    ("kuinka monta kerrosta talossa on", {"intent": "count_storey"}),
    ("how many offices", {"intent": "count_office"}),
    ("biggest room?", {"intent": "area_room_largest"}),
    ("how many server rooms are there", {"intent": "other"}),
]


def prompt(question: str) -> str:
    intents = "\n".join(f"  {k}: {v[3]}" for k, v in INTENTS.items())
    ex = "\n".join(f"Q: {q}\nA: {json.dumps(a, ensure_ascii=False)}" for q, a in EXAMPLES)
    return (
        "Map the question to this JSON object. Keys with no value may be left out.\n"
        '{"intent": <one of the intents>, "types": [<room types>], "material": <material or null>, '
        '"surface": <surface or null>, "waste_pct": <number or null>, "ceiling_m": <number or null>, '
        '"tile_m": <number or null>, "order": <true/false>, "name": <room name or null>, "room_no": <room number or null>, '
        '"apartment": <letter or null>, "level": <storey name or null>}\n\n'
        f"intents:\n{intents}\n"
        f"types (only for area_rooms_of_type): {ROOM_TYPES}. 'wet' = wet rooms (bathroom, wc, sauna).\n"
        f"material: {MATERIALS} (tiles, tiling, laatat = tiles; flooring = parquet, laminate, vinyl, carpet).\n"
        f"surface: {SURFACES}; leave out when the question does not say (default floor).\n"
        "waste_pct: cutting waste in percent (0 for 'no waste'); ceiling_m: room or wall height in metres; tile_m: height "
        "tiles go up to in metres; order: true when the person wants the quantity to buy or order.\n"
        "name, room_no, apartment, level: copy exactly as written in the question, only when it names one.\n"
        "Rules: 'the area of the bedrooms / living room / kitchen / bathrooms' is area_rooms_of_type, not "
        "area_room_named. 'Total area of all rooms' is area_room_total. Never drop a room type the question names: "
        "count_room and area_room_total are only for rooms of any kind; a room type with no count intent is other. "
        "Use other when no intent fits.\n\n"
        f"{ex}\n\nQ: {question}\nA:")


# --------------------------------------------------------------------------- normaliser

def _num(v, lo: float, hi: float) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(?:%|m|cm|mm)?\s*", v)
        if not m:
            return None
        v = float(m.group(1).replace(",", "."))
    if not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        return None
    return float(v)


def _in_text(v: float, f: str, scales=(1,)) -> bool:
    """The number (or the number times 100/1000 for cm/mm) is written in the question."""
    for s in scales:
        x = v * s
        forms = {f"{x:g}", f"{x:.1f}", f"{x:.2f}"}
        forms |= {t.replace(".", ",") for t in list(forms)}
        for t in forms:
            if re.search(r"(?<![\d.,])" + re.escape(t) + r"(?![\d])", f):
                return True
    return False


def _legacy_intent(d: dict) -> str | None:
    """The loose shape {measure, obj, variant, label ...} onto a canonical intent."""
    measure, obj, variant = d.get("measure"), d.get("obj"), d.get("variant")
    if measure == "count":
        return _OBJ_TO_INTENT.get(obj)
    if measure == "area":
        if obj == "apartment" or d.get("apartment"):
            return "area_apartment"
        if obj in _OBJ_TO_TYPE or obj in ("living", "dining", "hall", "utility", "storage"):
            return "area_rooms_of_type"
        if obj == "room":
            if d.get("label") or d.get("room_no"):
                return "area_room_named"
            return "area_room_total"
        if obj == "wall":
            return "area_wall"
        return None
    if measure == "length" and obj == "wall":
        return "length_wall"
    if measure == "volume" and obj in ("wall", "slab"):
        return "volume_" + obj
    if measure == "text" and obj == "scale":
        return "scale"
    return None


def _types(d: dict) -> list[str] | None:
    raw = d.get("types")
    if raw is None and d.get("type"):
        raw = d["type"]
    if raw is None and d.get("obj"):
        raw = [d["obj"]]
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw or len(raw) > 8:
        return None
    out = []
    for t in raw:
        if not isinstance(t, str):
            return None
        c = TYPE_SYN.get(t.strip().lower().replace("_", " "))
        if c is None and t.strip().lower() in _OBJ_TO_TYPE:
            c = _OBJ_TO_TYPE[t.strip().lower()]
        if c is None:
            return None
        out.append(c)
    return list(dict.fromkeys(out))


NO_WASTE = re.compile(r"\b(no|without|zero|ilman|ohne|utan|kein\w*|sans|senza)\b|\b0\s*%")
FLOOR_WORDS = re.compile(r"\bfloors?\b|\bflooring\b|lattia|golv|boden|\bvloer|\bgulv|pavimento|\bsol\b")
WALL_WORDS = re.compile(r"\bwalls?\b|seina|seinie|\bvagg|\bwand|\bwande|\bmurs?\b|pareti|parete|\bvaegg")
CEILING_WORDS = re.compile(r"ceiling|\bkatto|katon|kattoo|\btak\b|innertak|\bdecke|plafond|soffitt")


def _surface_in_text(s: str, f: str) -> str | None:
    """The model's surface, kept only as far as the question names it (a model tends to fill in 'floor')."""
    floor, wall, ceil = bool(FLOOR_WORDS.search(f)), bool(WALL_WORDS.search(f)), bool(CEILING_WORDS.search(f))
    if s == "floor":
        return "floor" if floor else None
    if s == "walls":
        return "walls" if wall else None
    if s == "ceiling":
        return "ceiling" if ceil else None
    if s == "floor+walls":
        return "floor+walls" if floor and wall else ("walls" if wall else ("floor" if floor else None))
    return None


def normalise(d, question: str) -> tuple[dict | None, str]:
    """(clean reading, "") or (None, reason). The clean reading has intent and, per intent, types, material,
    surface, inputs {waste_pct, ceiling_m, tile_m}, order, name, room_no, apartment, level. Everything is
    checked against the schema and the question; a value the question does not contain is dropped (heights,
    waste) or rejects the parse (a name, number, letter or level that is not in the text)."""
    if not isinstance(d, dict):
        return None, "reply is not a JSON object"
    intent = canonical_intent(d.get("intent")) or (_legacy_intent(d) if "intent" not in d else None)
    if intent is None:
        return None, f"unknown intent {d.get('intent')!r}"
    f = fold(question)
    # a room type the question names is never dropped (QA 14 Nov: "how many offices" read as count_room counted all
    # 13 spaces): count_room / area_room_total become the typed intent, or the reading is rejected
    named = _named_types(question)
    unknown = unknown_room_modifier(question)
    if intent == "count_room":
        if len(named) == 1 and next(iter(named)) in _TYPE_TO_COUNT:
            intent = _OBJ_TO_INTENT[_TYPE_TO_COUNT[next(iter(named))]]
        elif named:
            return None, f"count of rooms, but the question names room type(s) {sorted(named)} that no count filters by"
        elif unknown:
            return None, f"count of rooms, but the question names '{unknown}', a room type the vocabulary does not know"
        elif not GENERIC_ROOM.search(strip_quoted(f)):
            return None, "count of rooms of any kind, but the question names no generic room word"
    if intent == "area_room_largest" and re.search(r"\b(which|what|welche\w*|vilk\w*|mika|mita|quel\w*|quale)\b", f):
        intent = "other"                      # "Welcher Raum ist der groesste?" asks which room, not its area
    if intent in ("area_room_total", "area_room_largest"):
        if named and intent == "area_room_total":
            intent, d = "area_rooms_of_type", {**d, "types": sorted(named)}
        elif unknown:
            return None, f"area of all rooms, but the question names '{unknown}', a room type the vocabulary does not know"
    if intent == "area_room_named" and not quotes(question) and not d.get("room_no"):
        nm = d.get("name") or d.get("label")
        t = TK.room_types(nm) - {"room"} if isinstance(nm, str) and fold(nm).strip() in f else set()
        if not t and isinstance(nm, str) and fold(nm).strip() in f:    # "bastun" (the sauna), "keittiota"
            t = {k for k in named if TK._Q_TYPE_RE[k].search(fold(nm))}
        if t:                                   # "kuinka iso kylpyhuone on" -> name 'kylpyhuone' is a room type
            intent, d = "area_rooms_of_type", {**d, "types": sorted(t), "name": None, "label": None}
    if intent == "area_rooms_of_type" and unknown and not named:
        return None, f"the question names '{unknown}', a room type the vocabulary does not know"
    out: dict = {"intent": intent}
    for key in ("name", "room_no", "apartment", "level"):
        v = d.get(key if key != "name" else "name", d.get("label") if key == "name" else None)
        if v is None or v == "":
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = f"{v:g}"
        if not isinstance(v, str) or fold(v).strip() not in f:
            return None, f"{key} {v!r} is not in the question"
        if key == "apartment" and not re.search(r"\b" + re.escape(v.strip()) + r"\b", question, re.I):
            return None, f"apartment letter {v!r} is not a word of the question"
        out[key] = v.strip()
    if intent == "area_room_named" and not (out.get("name") or out.get("room_no")):
        return None, "area_room_named without a room name or number"
    if intent == "area_apartment":
        a = out.get("apartment")
        if not a or not re.fullmatch(r"[A-Za-z]", a):
            return None, "area_apartment needs an apartment letter"
        out["apartment"] = a.upper()
    if intent == "area_rooms_of_type":
        types = _types(d)
        if not types:
            return None, "area_rooms_of_type needs room types from the list"
        out["types"] = types
        m = d.get("material")
        if m not in (None, ""):
            m = MATERIAL_SYN.get(str(m).strip().lower())
            if m is None:
                return None, f"material {d.get('material')!r} is not in the list"
            out["material"] = m
        s = d.get("surface")
        if s not in (None, ""):
            s = SURFACE_SYN.get(str(s).strip().lower())
            if s is None:
                return None, f"surface {d.get('surface')!r} is not in the list"
            s = _surface_in_text(s, f)
            if s:
                out["surface"] = s
        inputs = {}
        w = _num(d.get("waste_pct"), 0, 60)
        if w is not None and ((w == 0 and NO_WASTE.search(f)) or (w > 0 and _in_text(w, f))):
            inputs["waste_pct"] = w
        c = _num(d.get("ceiling_m"), 1.5, 12)
        if c is not None and _in_text(c, f, (1, 100, 1000)):
            inputs["ceiling_m"] = c
        t = _num(d.get("tile_m"), 0.1, 12)
        if t is not None and _in_text(t, f, (1, 100, 1000)):
            inputs["tile_m"] = t
        if inputs:
            out["inputs"] = inputs
        if isinstance(d.get("order"), bool):
            out["order"] = d["order"]
    return out, ""


# --------------------------------------------------------------------------- Query building

def build(reading: dict, question: str) -> Query:
    """The Query the rules would build for this reading. Flags that come from the wording (whole model, exterior,
    compound, a named storey) are the rule parser's own, so both parsers agree on them."""
    base = RuleParser().parse(question)
    base.parser, base.measure, base.obj, base.variant = "llm", None, None, None
    base.label = base.room_no = base.apartment = None
    for k in ("takeoff", "single_room", "toilet_ambiguous", "slab_type"):
        base.extra.pop(k, None)
    intent = reading["intent"]
    measure, obj, variant, _ = INTENTS[intent]
    q = base
    q.extra["llm_parsed"] = True
    q.extra["intent"] = intent
    if measure is None:
        return q
    q.measure, q.obj, q.variant = measure, obj, variant
    qs = quotes(question)
    # a storey is taken as named only when the question quotes it, as the rules do ("on 'Level 2'"); "this floor",
    # "the ground floor" stay unresolved and go to the text route
    if reading.get("level") and any(fold(reading["level"]) == fold(x) for x in qs):
        q.level = reading["level"]
        if q.scope == "drawing":
            q.scope = "level"
        q.extra.pop("unresolved_scope", None)
    elif q.scope == "level" and not q.level:
        q.scope = "drawing"
    if intent == "count_toilet":
        q.extra["toilet_ambiguous"] = True
    if obj == "slab" and re.search(r"floor slabs", fold(question)):
        q.extra["slab_type"] = "FLOOR"
    if intent == "area_room_named":
        q.label, q.room_no = reading.get("name"), reading.get("room_no")
        q.variant = None
        if q.label and TK.room_types(q.label) and not qs:
            pass
    elif intent == "area_apartment":
        q.apartment = reading["apartment"]
    elif intent == "area_rooms_of_type":
        types = list(reading["types"])
        group = None
        if "wet" in types:
            group = "wet"
            types = [t for t in types if t != "wet"] + [t for t in TK.GROUPS["wet"]]
            types = list(dict.fromkeys(types))
        surface = reading.get("surface") or "floor"
        tk = {"types": types, "group": group, "material": reading.get("material"), "surface": surface,
              "aggregation": "sum", "floor_explicit": reading.get("surface") in ("floor", "floor+walls")}
        if reading.get("inputs"):
            tk["inputs"] = dict(reading["inputs"])
        m = TK.SCOPE_ID.search(fold(strip_quoted(question)))
        if m:
            tk["scope_named"] = m.group(0)
            q.extra["unresolved_scope"] = True
        q.extra.pop("compound", None)
        if single_type_asked(fold(question), tk):
            q.extra["single_room"] = True
        q.extra["takeoff"] = tk
    q.extra["reading"] = describe(q)
    return q


OBJ_NAMES = {"door": "doors", "window": "windows", "toilet": "toilets (fixtures)", "wc_room": "WC rooms",
             "sink": "sinks", "washbasin": "wash basins", "shower": "showers", "bathtub": "bathtubs",
             "shower_or_bath": "showers and bathtubs", "bedroom": "bedrooms", "kitchen": "kitchens",
             "bathroom": "bathrooms", "sauna": "saunas", "balcony": "balconies", "room": "rooms",
             "apartment": "apartments", "storey": "storeys", "wall": "walls", "column": "columns", "beam": "beams",
             "stair": "stairs", "slab": "slabs", "scale": "the drawing scale"}


def describe(q: Query) -> str:
    """One plain sentence: what the language model read the question as."""
    tk = q.extra.get("takeoff")
    if not q.ok():
        return "not a measurable question (left to the property lookup)"
    where = ""
    if q.level:
        where = f", on '{q.level}'"
    elif q.scope == "model" or q.extra.get("whole_model"):
        where = ", in the whole model"
    if tk:
        names = ", ".join(TK.TYPE_NAMES[t] for t in tk["types"])
        s = f"area of rooms typed {names}, summed"
        if tk.get("material"):
            s += f", for {tk['material']}"
        if tk.get("surface") and tk["surface"] != "floor":
            s += f", surface {tk['surface']}"
        inp = tk.get("inputs") or {}
        if "waste_pct" in inp:
            s += f", waste {inp['waste_pct']:g}%"
        if "ceiling_m" in inp:
            s += f", height {inp['ceiling_m']:g} m"
        if "tile_m" in inp:
            s += f", tiled to {inp['tile_m']:g} m"
        return s + where
    if q.measure == "count":
        return f"count of {OBJ_NAMES.get(q.obj, q.obj)}{where}"
    if q.measure == "area" and q.obj == "room":
        if q.room_no or q.label:
            return f"area of room {q.room_no or ''} {repr(q.label) if q.label else ''}".replace("  ", " ").strip() + where
        return "total floor area of all rooms" + where
    if q.measure == "area" and q.obj == "apartment":
        return f"area of apartment {q.apartment}"
    if q.measure == "text" and q.obj == "scale":
        return "the scale of the drawing"
    return f"{q.measure} of {OBJ_NAMES.get(q.obj, q.obj)}" + where


# --------------------------------------------------------------------------- the LLM call

MAX_TOKENS = int(os.environ.get("NEMO_PARSE_MAX_TOKENS", "260"))


def extract_json(text):
    from .routes.nemotron import extract_json as ej
    return ej(text)


# The question parse has its own short wall-clock limit including retries (a 429 from a busy shared key must never make
# a question wait); the image estimate keeps NEMO_DEADLINE_S. 0 = no limit (batch runs, tools/paraphrase_bench.py).
PARSE_DEADLINE_S = float(os.environ.get("NEMO_PARSE_DEADLINE_S", "4"))
PARSE_CACHE = N.REPO / "out" / "parse_cache"
_mem: dict = {}


def _parse_key(question: str) -> str:
    """Disk-cache key: the question with whitespace collapsed and trailing punctuation dropped, plus a version of
    the schema (prompt, system text, token limit), so a changed prompt never serves an old reading."""
    import hashlib
    norm = re.sub(r"\s+", " ", question).strip().rstrip("?!. ")
    ver = hashlib.sha256((prompt("") + SYSTEM + str(MAX_TOKENS)).encode()).hexdigest()[:12]
    return hashlib.sha256((ver + "|" + norm).encode()).hexdigest()


def _llm_reading(question: str) -> tuple[dict | None, str, float, float]:
    """(reading, reason, cost, latency): one text-only call, cached in memory and on disk (out/parse_cache) by the
    normalised question. Only a definite outcome is cached (a valid reading, or a reply the validation rejected);
    a timeout, a 429 or a missing key is not, so the next ask tries again."""
    key = _parse_key(question)
    if key in _mem:
        return _mem[key]
    path = PARSE_CACHE / f"{key}.json"
    try:
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            reading, why = d["reading"], d["why"]
            # the cache holds a validated reading; validate it again so a stricter normaliser applies to old entries
            # (the raw reply, kept since 14 Nov, is validated afresh)
            if d.get("raw") is not None:
                reading, why = normalise(d["raw"], question)
            elif reading is not None:
                again = {k: v for k, v in reading.items() if k != "inputs"}
                again.update(reading.get("inputs") or {})
                reading, why = normalise(again, question)
            _mem[key] = (reading, why, d.get("cost", 0.0), d.get("latency", 0.0))
            return _mem[key]
    except (OSError, ValueError, KeyError):
        pass
    if not N.enabled():
        return None, "language model switched off or no key", 0.0, 0.0
    r = N.chat(prompt(question), None, system=SYSTEM, max_tokens=MAX_TOKENS, think=False,
               deadline_s=PARSE_DEADLINE_S)
    if r.get("content") is None:
        return None, f"no reply in time ({r.get('error')})", 0.0, 0.0
    cost = ((r.get("prompt_tokens") or 0) * 3 + (r.get("completion_tokens") or 0) * 15) / 1e6
    lat = r.get("latency_s") or 0.0
    d = extract_json(r.get("content"))
    out = (None, "reply is not JSON", cost, lat) if d is None else (*normalise(d, question), cost, lat)
    _mem[key] = out
    try:
        PARSE_CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"reading": out[0], "why": out[1], "cost": cost, "latency": lat, "raw": d},
                                   ensure_ascii=False, default=str), encoding="utf-8")
    except OSError:
        pass
    return out


def llm_parse(question: str) -> Query:
    """The validated LLM reading as a Query (unparsed Query, parser 'llm', when the reply is invalid or 'other')."""
    reading, why, cost, lat = _llm_reading(question)
    if reading is None:
        q = Query(raw=question, parser="llm")
        q.extra["llm_rejected"] = why
        return q
    q = build(reading, question)
    q.extra["cost_usd"] = cost
    q.extra["llm_latency_s"] = lat
    return q


class LLMQueryParser(QueryParser):
    name = "llm"

    def parse(self, question: str) -> Query:
        return llm_parse(question)


# --------------------------------------------------------------------------- policy

def shape_known(q: Query) -> bool:
    """True when some route accepts this (measure, obj, variant). A parse outside it cannot be answered."""
    if q.extra.get("takeoff") is not None:
        return True
    m, o, v = q.measure, q.obj, q.variant
    if m == "text":
        return True                       # the property routes read the question text itself
    if m == "count":
        return o in COUNT_OBJS
    if m == "area":
        return (o == "room" and (v in ("total", "net_indoor_total", "type_sum", "max") or bool(q.label or q.room_no))) or \
               o == "apartment" or (o == "wall" and v == "side_area") or (o == "room" and bool(q.label))
    if m == "length":
        return o == "wall"
    if m == "volume":
        return o in ("wall", "slab")
    return False


_TYPE_WORDS = None


def _named_types(question: str) -> set[str]:
    f = TK.EXCLUDE.sub(" ", fold(strip_quoted(question)))
    return {t for t, rx in TK._Q_TYPE_RE.items() if t != "wet" and rx.search(f)}


# words for a room of any kind (folded): a count_room reading needs one of them in the question
GENERIC_ROOM = re.compile(r"\brooms?\b|\bspaces?\b|huone(?!isto)\w*|\btil(a|oja|oj\w+|at|aa)\b|zimmer\w*|\braum\w*|"
                          r"\brum(men|met|mene)?\b|\bpieces?\b|\blocal[ei]\b|\bstanz[ae]\b|\bruum\w*|vaerelse\w*")
# "<word> rooms": words that leave the room type open ("all rooms", "numbered rooms", "the indoor spaces")
_ROOM_MOD = re.compile(r"\b([a-z]+)[ -](rooms?|spaces?)\b")
_GENERIC_MODS = set("""a all an and any are as at both different each empty enclosed entire every few for free gross habitable
heated how in individual indoor interior internal its main many more my named net numbered of on open or other our outdoor
exterior external residential separate several single such that the their there these this those total two three four
unheated usable what which whole with your number big bigger biggest large larger largest small smaller smallest
different labelled labeled listed existing remaining have has can finish complete schedule listing including containing showing""".split())


def unknown_room_modifier(question: str) -> str | None:
    """'meeting rooms' before the vocabulary knew it, 'lab rooms', 'server rooms': a word that types the rooms but is
    not a room type of bpqa/takeoff.py. Returns the phrase, or None. Reading it as 'all rooms' would be wrong."""
    f = fold(strip_quoted(question))
    for m in _ROOM_MOD.finditer(f):
        w = m.group(1)
        if w in _GENERIC_MODS or w.isdigit():
            continue
        if any(rx.search(m.group(0)) for rx in TK._Q_TYPE_RE.values()):
            continue
        return m.group(0)
    return None


# casual spellings the rules do not read ("how big r the bedrooms", "how many dooors"): rewritten before parsing
_CASUAL = [(re.compile(r"(?i)\b(r)\b(?=\s+(?:the|there|they|these|those|all|my|our)\b)"), "are"),
           (re.compile(r"(?i)\bhw\s+many\b|\bhowmany\b|\bhow\s+meny\b|\bhow\s+mny\b"), "how many"),
           (re.compile(r"(?i)\bwhats\b"), "what is"),
           (re.compile(r"(?i)\bhow\s+(?:mnay|mant|amny)\b"), "how many"),
           (re.compile(r"(?i)\b(?:motna|mointa|montaa)\b"), "monta"),
           (re.compile(r"(?i)\b(?:kuinak|kunka|kuika)\b"), "kuinka"),
           (re.compile(r"(?i)\b(?:montaako|montakko|mntako)\b"), "montako"),
           (re.compile(r"([^\W\d_])\1{2,}"), r"\1\1")]


def casual(question: str) -> str:
    """The question with casual spellings and stretched letters normalised; unchanged for ordinary text."""
    s = question
    for rx, rep in _CASUAL:
        s = rx.sub(rep, s)
    return re.sub(r"\s{2,}", " ", s).strip() if s != question else question


def weak(q: Query, question: str) -> str | None:
    """Why the rule parse is not good enough to stand alone (the LLM reading is asked for), or None."""
    if not q.ok():
        return "the rules read no measurable question"
    if (q.obj == "room" and q.measure in ("count", "area") and not q.label and not q.room_no
            and q.variant not in ("type_sum", "net_indoor_total") and unknown_room_modifier(question)):
        return BLOCKING[0] + f" ('{unknown_room_modifier(question)}')"
    if q.measure == "count" and UNIT_WORDS.search(fold(strip_quoted(question))):
        return "a count was read, but the question asks in units of area or volume"
    if quotes(question) or '"' in question:
        return None                       # quoted names: the dataset templates, the rules are authoritative
    if q.measure == "count" and q.obj == "apartment" and not APARTMENTS_PLURAL.search(fold(question)):
        return "the apartment is the scope of the question, not the thing counted"
    if not shape_known(q):
        return f"no route answers {q.measure} of {q.obj}"
    if q.measure == "area" and q.obj == "room" and q.variant in ("total", "max") and _named_types(question):
        return BLOCKING[1]
    if q.measure == "count" and q.obj == "room" and _named_types(question) - {"stair"}:
        return BLOCKING[1]
    f = fold(question)
    if q.measure == "area" and q.obj == "apartment" and not q.apartment and APARTMENT_LETTER.search(question):
        return "an apartment letter is named but the rule parse dropped it"
    if q.obj in ("shower", "bathtub") and SHOWER_WORDS.search(f) and BATH_WORDS.search(f):
        return "both showers and bathtubs are named"
    if q.measure == "count" and UNIT_WORDS.search(fold(question)):
        return "a count was read, but the question asks in units of area or volume"
    tk = q.extra.get("takeoff")
    if tk is not None and _unused_numbers(question, tk):
        return "the question gives a height or a waste percentage the rules did not read"
    return None


UNIT_WORDS = re.compile(r"\bm2\b|\bm3\b|\bsqm\b|\bsq\.? ?m\b|square met|cubic met|neliom|neliot|kuutiom|kuutio|kvadratmeter|"
                        r"kubikmeter|\bkvm\b|quadratmeter|kubikmeter|\bmetres\b|\bmeters\b|metrina|\bmetreinä\b")
APARTMENT_LETTER = re.compile(r"\b(?:apartment|flat|wohnung|lagenhet|lägenhet|huoneisto\w*|asunto\w*|appartement|appartamento)\s+([A-Z])\b",
                              re.I)
APARTMENTS_PLURAL = re.compile(r"apartments|flats|residential units|wohnungen|appartements|appartamenti|lagenheter|"
                               r"huoneistoa|huoneistoja|asuntoa|asuntoja|units")
SHOWER_WORDS = re.compile(r"shower|dusche|douche|docce|suihku|dusch")
BATH_WORDS = re.compile(r"bathtub|badewanne|wanne|baignoire|vasch|kylpyamme|ammetta|ammeit|ammeen|badkar|\bbaths?\b|\btubs?\b")
_NUMBER_INPUT = re.compile(r"\d+(?:[.,]\d+)?\s*(?:%|cm\b|mm\b|m\b|metr)")


def _unused_numbers(question: str, tk: dict) -> bool:
    """A takeoff question with a percentage or a length that walls.parse_inputs did not pick up."""
    from .walls import _parse_inputs
    f = fold(strip_quoted(question))
    if not _NUMBER_INPUT.search(f):
        return False
    got = _parse_inputs(question)
    n_given = len(re.findall(_NUMBER_INPUT, f))
    n_read = sum(1 for k in ("waste_pct", "ceiling_m", "tile_m") if got.get(k) is not None)
    return n_read < n_given


# weak() reasons where the rule parse would answer the wrong question (all rooms instead of the rooms of a type): when
# the language model does not give a valid reading either, the router abstains instead of answering the rule parse
BLOCKING = ("the question names a room type the vocabulary does not know; all rooms would be counted or summed",
            "the question names a room type the rule parse ignored")


def read(question: str, use_llm: bool = True) -> Query:
    """Rules first; the LLM reading only for a missing, unanswerable or weak rule parse. The question first goes
    through `casual()` ("how big r the bedrooms", "dooors"); ordinary text is unchanged."""
    raw = question
    question = casual(question)
    q = RuleParser().parse(question)
    q.raw = raw
    why = weak(q, question)
    if why is None or not use_llm:
        if why is not None:
            q.extra["weak"] = why
            if why.startswith(BLOCKING):
                q.extra["blocked"] = why
        return q
    q2 = llm_parse(question)
    if q2.ok():
        q2.extra["rule_parse"] = q.as_dict() if q.ok() else None
        q2.extra["llm_reason"] = why
        q2.raw = raw
        return q2
    q.extra["weak"] = why
    if why.startswith(BLOCKING):
        q.extra["blocked"] = why
    if q2.extra.get("llm_rejected"):
        q.extra["llm_rejected"] = q2.extra["llm_rejected"]
    return q
