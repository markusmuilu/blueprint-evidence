"""Quantity takeoff: "how many m2 of tiling for the bathrooms?" -> a room-type area sum.

Two halves, both rules, no model:

  parse_takeoff(question)  question -> {types, material, surface, aggregation}. EN and FI first
                           (with the Finnish plan abbreviations KH, PH, WC, S, KPH, MH, OH, K, KK,
                           ET, VH, PESUH, KHH), plus the DE / FR / IT room words that the Swiss
                           drawings use.
  room_types(name)         a room label on a drawing -> the set of room types it names. Labels
                           in the data are Swiss (Bad, Kuche, Bain, Bagno ...), IFC (Bathroom,
                           Toilet, TUALETTRUUM, doucheruimte, 1IZQ-BAN ...) and Finnish
                           abbreviations on the CubiCasa scans.

What a takeoff answer is: the sum of the FLOOR areas of the matching rooms, net as drawn (inside
the walls), with no allowance for cutting waste. Wall areas need room heights, which a floor plan
does not carry, so wall questions abstain and say why. Every answer states this interpretation.
"""
from __future__ import annotations

import re

from .query import fold

# ---------------------------------------------------------------- room types

TYPE_NAMES = {
    "bathroom": "bathroom", "wc": "WC / toilet room", "sauna": "sauna", "kitchen": "kitchen",
    "bedroom": "bedroom", "living": "living room", "dining": "dining", "hall": "hall / corridor",
    "utility": "utility / laundry", "storage": "storage / closet", "balcony": "balcony / terrace",
    "stair": "stair", "room": "generic room",
}
# Groups a question can name. "wet rooms" follows the Finnish usage of marka-tila for the rooms with
# a floor drain that are tiled: bathroom, WC, sauna. A utility room (KHH) is listed but not included.
GROUPS = {"wet": ("bathroom", "wc", "sauna")}

# Drawing labels, matched on the folded label (lower case, accents stripped, U+FFFD dropped).
# A label may name several types ("Wohnen/Essen", "ELUTUBA+KOOK"); each part is matched.
LABEL_RE = {
    "bathroom": r"bad|badezimmer|bain|bains|salle de bains?|salle d'eau|bagno|bagni|bathroom\s*\d*|bath|"
                r"shower(\s*room)?|dusche|douche(ruimte)?|badkamer|badevaerelse|badrum|vannituba|dushiruum|"
                r"kh\d?|kph\d?|ph\d?|pesuh(uone)?\d?|sh\d?|kylpyhuone|pesuhuone|suihkuhuone|ban|bano|aseo",
    "wc": r"(m\.|w\.|staff|specimen|public|men'?s|women'?s)?\s*(toilet|toilets|toilette|toiletten|wc\d?|w\.c\.|"
          r"restroom|lavatory|tualett(ruum)?|tualettruum|vessa|sanitair|toilet(ruimte)?)",
    "sauna": r"sauna|s|leiliruum|bastu|loylyhuone|lh",
    "kitchen": r"kuche|kche|kueche|cuisine|cucina|kitchen(ette)?|keuken|kokken|tekokken|kok|kook|keittio|"
               r"keittokomero|k|kk|kt|avok|avokeittio|tupak|coc|cocina|kitchen\s*\d*",
    "bedroom": r"schlafzimmer|chambre( a coucher)?|chambre a coucher|camera( da letto)?|camera da letto|"
               r"bedroom\s*\d*|slaapkamer|sovevaerelse|soverum|sovrum|magamistuba|mh\d?|makuuhuone\d?|hab\d?",
    "living": r"wohnen|wohnzimmer|sejour|sjour|salon|soggiorno|living( room)?|woonkamer|stue|opholdsrum|"
              r"elutuba|oh|olohuone|sal",
    "dining": r"essen|repas|pranzo|dining|eetkamer|spisestue|rt|ruokailu(tila)?|ruokailuh",
    "hall": r"korridor|flur|diele|eingang|entree|couloir|corridoio|disimpegno|ingresso|corridor|hall|hallway|"
            r"foyer|entrance|gang|esik|et|eteinen|aula|tk|tuulikaappi|dist|kaytava",
    "utility": r"khh|kodinhoitohuone|buanderie|waschkuche|waschraum|lavanderia|laundry|utility|bijkeuken|"
               r"bryggers|wasruimte",
    "storage": r"reduit|rduit|abstellraum|ripostiglio|storage|store|berging|kast|depot|vh\d?|var\d?|varasto|"
               r"vaatehuone|abiruum|cave|keller|closet|walk-in closet|compartiment cave|vano cantina|kellari",
    "balcony": r"balkon|balcon|balcone|balcony|loggia|terrasse|terrazza|terrace|sitzplatz|parveke\d?|"
               r"terassi|lasitettu parveke|altan|veranda",
    "stair": r"treppenhaus|treppe|cage d'escalier|vano scala|stair(s|case)?|trappe(rum)?|trepikoda|porras",
    "room": r"zimmer|piece|pice|stanza|room|raum|local|locale|huone|ruum",
}
_LABEL_RE = {k: re.compile(rf"(?:{v})") for k, v in LABEL_RE.items()}


def _fold_label(s: str) -> str:
    return fold((s or "").replace("�", "")).strip()


def label_parts(name: str) -> list[str]:
    f = _fold_label(name)
    f = re.sub(r"\(.*?\)", " ", f)
    # Spanish IFC codes "1IZQ-BAN", "2DER-HAB3": the room code is the last dash-separated part
    parts = [p.strip() for p in re.split(r"[/+,&;]|\s-\s|(?<=\w)-(?=[a-z])", f) if p.strip()]
    return parts or ([f] if f else [])


def room_types(name: str | None) -> set[str]:
    """Room types a drawing label names. 'Wohnen/Essen' -> {living, dining}; unknown -> empty."""
    out = set()
    for p in label_parts(name or ""):
        p = re.sub(r"^\d+\s*", "", p).strip(" .")          # "1izq" prefixes, "2 bad"
        p = re.sub(r"\s*\d+$", "", p).strip(" .")           # "bedroom 2", "mh3"
        for t, rx in _LABEL_RE.items():
            if rx.fullmatch(p):
                out.add(t)
                break
    if out - {"room"}:
        out.discard("room")
    return out


# ---------------------------------------------------------------- question side

# room-type words in a question (folded text, quotes and exclusion clauses removed). Finnish
# words are stems, so inflected forms match (kylpyhuoneisiin, keittioon, saunaan, vessoihin).
Q_TYPE_RE = {
    "wet": r"wet ?rooms?|wet areas?|markatil|kosteat tilat|kosteisiin tiloihin|nassraum|nassraeume|nasszell|"
           r"pieces humides|locali umidi",
    "bathroom": r"bathrooms?|bath rooms?|washrooms?|shower rooms?|kylpyhuone|pesuhuone|suihkuhuone|badezimmer|"
                r"\bbad\b|\bbader\b|salles? de bains?|salles? d'eau|bagn[oi]\b",
    "wc": r"toilets?|\bwcs?\b|w\.c\.|restrooms?|lavator(y|ies)|powder rooms?|water closets?|vessa|vesso|"
          r"\bwc-|toilette",
    "sauna": r"saunas?|saunaan|saunan|saunoihin|saunoj|loylyhuone",
    "kitchen": r"kitchens?|kitchenette|keittio|keittokomero|\bkuche|cuisines?|cucin[ae]",
    "bedroom": r"bedrooms?|makuuhuone|schlafzimmer|chambres?( a coucher)?|camer[ae] da letto",
    "living": r"living ?rooms?|lounge|olohuone|wohnzimmer|sejour|soggiorno",
    "hall": r"hallways?|corridors?|entrances?|entry|\bhalls?\b|eteinen|eteise|kaytav|korridor|\bflur|couloir",
    "utility": r"utility rooms?|laundry|kodinhoitohuone|waschkuche|buanderie",
    "storage": r"storage|store ?rooms?|closets?|walk-in|varasto|vaatehuone|abstellraum|reduit",
    "balcony": r"balcon(y|ies)|terraces?|parveke|parvekkee|balkon|terrass",
}
_Q_TYPE_RE = {k: re.compile(v) for k, v in Q_TYPE_RE.items()}
# Finnish plan abbreviations, matched case-sensitively on the original question
# ("KH:n", "MH:t", "WC-tila" allowed). Single letters (S, K) only count in an area question.
ABBR = {"KH": "bathroom", "KPH": "bathroom", "PH": "bathroom", "PESUH": "bathroom", "SH": "bathroom",
        "WC": "wc", "S": "sauna", "MH": "bedroom", "OH": "living", "K": "kitchen", "KK": "kitchen",
        "ET": "hall", "TK": "hall", "VH": "storage", "VAR": "storage", "KHH": "utility", "RT": "dining"}
ABBR_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(sorted(ABBR, key=len, reverse=True)) + r")\d?(?=$|[\s,.;:?!)/+-])")

# (fold() applies NFKD, so "m²" arrives here as "m2")
AREA_UNIT = re.compile(r"\bm2\b|\bm\^2|square ?met|\bsq\.? ?m\b|\bsqm\b|sq\.? ?ft|square f(ee|oo)t|nelio|"
                       r"quadratmeter|metres? carres|metri quadr")
# plural "areas" only with a sum word ("how large areas do the bedrooms have combined"): "restrooms along with
# their areas" lists rooms, it is not a takeoff
# "how big/large are the bedrooms in total", "kuinka isoja alueita makuuhuoneet ovat yhteensa": size words count as
# an area only next to a sum word (QA 14 Nov: the Finnish phrasing fell through to Nemotron)
AREA_WORD = re.compile(r"pinta-ala|\barea\b|\bareas\b(?=.*\b(?:combined|together|altogether|in total|summed)\b)|flache|surface|superficie|\bala\b|lattiapinta|lattiaala|floor ?space|"
                       r"\bhow (?:big|large)\b(?=.*\b(?:combined|together|altogether|in total|summed)\b)|"
                       r"\b(?:kuinka|miten) (?:iso|suur)\w*\b(?=.*\byhteensa\b)|\balue(?:ita|et|en|iden)?\b(?=.*\byhteensa\b)|"
                       r"how much|kuinka paljon|paljonko|wie viel")
COUNT_Q = re.compile(r"\b(how many|kuinka monta|montako|wie viele|combien|quant[ie])\b")
NAMED = re.compile(r"\b(named|labell?ed|called|schedule|nimetty|bezeichnung)\b")
SCOPE_ID = re.compile(r"\b(house|building|block|wing|unit|apartment|flat|talo|rakennus|asunto|huoneisto)\s+[a-z0-9]{1,3}\b")
MATERIALS = {
    "tiles": r"\btil(e|es|ing|ed)\b|laatta|laato|laatoi|laatat|laattoj|klinkkeri|fliese|carrelage|piastrell|kakel",
    "flooring": r"flooring|floor covering|floor finish|parquet|laminate|vinyl|carpet|linoleum|lattiapaallyste|"
                r"parketti|laminaatti|vinyyli|\bmatto|bodenbelag|parkett|revetement de sol|pavimento",
    "paint": r"\bpaint|maali|maalaus|farbe|peinture|vernice|pittura",
    "waterproofing": r"waterproof|vedeneristy|membrane|abdichtung|etancheite",
    "screed": r"screed|tasoite|estrich|chape|massaus",
}
_MATERIALS = {k: re.compile(v) for k, v in MATERIALS.items()}
WALLS = re.compile(r"\bwalls?\b|seina|seiniin|\bwand|\bwande|\bmurs?\b|pareti|parete")
CEILING = re.compile(r"ceilings?|\bkatto|kattoon|katon|kattoj|\bdecke|plafond|soffitt")
EXCLUDE = re.compile(r"\b(except|excluding|without|apart from|other than|ohne|ausser|sauf|hors|sans|esclus[oi]|"
                     r"tranne|paitsi|lukuun ottamatta|pois lukien|ilman)\b[^)?.;]*")
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"|«[^»]*»|„[^“”]*[“”]|“[^”]*”")


def parse_takeoff(question: str) -> dict | None:
    """Takeoff interpretation of a question, or None when it is not a room-type area question."""
    raw = QUOTED.sub(" ", question)
    f = fold(raw)
    f_incl = EXCLUDE.sub(" ", f)
    material = next((m for m, rx in _MATERIALS.items() if rx.search(f)), None)
    if NAMED.search(f):
        return None          # "rooms named RESTROOM": a label filter, not a room type
    # "how many bathrooms ... and the area of each" is a count; an area unit or a material makes
    # "how many m2 of tiles ..." an area question
    area = material or AREA_UNIT.search(f) or (AREA_WORD.search(f) and not COUNT_Q.search(f))
    if not area:
        return None
    types: list[str] = []
    for t, rx in _Q_TYPE_RE.items():
        if rx.search(f_incl):
            types += list(GROUPS.get(t, (t,)))
    for m in ABBR_RE.finditer(EXCLUDE.sub(" ", raw)):
        types.append(ABBR[m.group(1)])
    types = list(dict.fromkeys(types))
    if not types:
        return None
    f_surf = re.sub(r"ceiling heights?|room heights?|huonekorkeus|raumhohe", " ", f)   # an input, not a surface
    walls, ceiling = bool(WALLS.search(f_surf)), bool(CEILING.search(f_surf))
    surface = "walls" if walls and not re.search(r"\bfloors?\b|lattia", f) else ("ceiling" if ceiling else "floor")
    if walls and re.search(r"\bfloors?\b|lattia", f):
        surface = "floor+walls"
    group = next((g for g in GROUPS if _Q_TYPE_RE[g].search(f_incl)), None)
    out = {"types": types, "group": group, "material": material, "surface": surface, "aggregation": "sum",
           "floor_explicit": bool(re.search(r"\bfloors?\b|lattia|\bboden|\bsol\b|pavimento", f))}
    m = SCOPE_ID.search(f)
    if m:
        out["scope_named"] = m.group(0)     # "house A": not resolvable from labels in general
    return out


def describe(tk: dict) -> str:
    """One sentence that says how the question was read; goes into every takeoff answer."""
    names = ", ".join(TYPE_NAMES[t] for t in tk["types"])
    grp = f" ('{tk['group']} rooms' read as {names})" if tk.get("group") else ""
    tr = tk.get("toilet_rooms")
    mat = f" for {tk['material']}" if tk.get("material") else ""
    s = f"Takeoff{mat}: sum of net floor areas of rooms typed {names}{grp}, as drawn inside the walls, no waste allowance"
    if tr:
        other = "".join(f" and rooms typed {TYPE_NAMES[t]}" for t in tr.get("other_types", []))
        how = ("a toilet symbol drawn inside" if tr["confirmed"] else "a bathroom label, toilet not visible in the drawing")
        s = (f"Takeoff{mat}: sum of net floor areas of the rooms that contain a toilet ({how}){other}, as there is no "
             f"separate WC room on this floor, as drawn inside the walls, no waste allowance")
    if tk.get("surface") == "ceiling":
        s += "; ceiling area taken equal to floor area"
    if tk.get("material") == "paint" and tk.get("surface") == "floor":
        s += "; paint usually also covers walls, which need room heights and are not included"
    if "wc" in tk["types"] and "bathroom" not in tk["types"]:
        s += "; 'toilets' read as separate WC rooms (bathrooms with a WC seat not included)"
    return s


def wall_reason(tk: dict) -> str:
    return ("wall area needs room outlines with heights and the wall openings; this route measures floor areas "
            "only (wall takeoffs: IFC models, Swiss DXF floors, and scans with a scale from printed room areas)")


def answer_line(value, n_rooms: int, route: str, estimate: str = "") -> str:
    tag = f" [ESTIMATE: {estimate}]" if estimate else ""
    return f"Answer{tag}: {value:.2f} m2 total over {n_rooms} room(s), floor area only   route: {route} (cost $0)"


def matches(name: str | None, tk: dict) -> bool:
    return bool(room_types(name) & set(tk["types"]))


# ---------------------------------------------------------------- shared answer building

# routes that measure wall surfaces (perimeter x height - openings, bpqa/walls.py); any other route's
# takeoff answer is withdrawn by the router when walls are asked
WALL_ROUTES = {"ifc", "dxf-geometry", "raster-area"}


def needs_walls(tk: dict) -> bool:
    from .walls import surfaces
    return surfaces(tk)[1]


def precheck(tk: dict) -> str | None:
    """Reason to abstain before any route looks at the drawing, or None. Wall surfaces are measured by the
    routes in WALL_ROUTES (bpqa/walls.py); the router withdraws other routes' floor-only answers."""
    return None


def select(rooms: list[dict], tk: dict, name_key: str = "name") -> tuple[list[dict], list[dict]]:
    """Rooms whose label names one of the asked types; and the labelled rooms left out as generic
    ('Zimmer', 'Room 3'), which are listed in the answer so the reader can see what was not counted."""
    want = set(tk["types"])
    used, generic = [], []
    for r in rooms:
        ts = room_types(r.get(name_key))
        if ts & want:
            used.append(r)
        elif ts == {"room"}:
            generic.append(r)
    return used, generic


def summary(rows: list[dict], tk: dict, unit: str = "m2") -> tuple[float, list[dict], str]:
    """rows: {label, area, source[, number]} -> (total, items, explanation). Items read like a
    takeoff sheet: one line per room with its area and where the number came from, then the total."""
    total = round(sum(r["area"] for r in rows), 2)
    items = []
    for i, r in enumerate(rows):
        it = {"n": i + 1, "room": r["label"], "type": ", ".join(sorted(room_types(r["label"]) & set(tk["types"]))),
              "area_m2": round(r["area"], 2), "source": r["source"]}
        if r.get("number"):
            it["number"] = r["number"]
        items.append(it)
    def tag(r):
        no = r.get("number")
        return f"{r['label']} ({no})" if no and no not in r["label"] else r["label"]
    lines = " + ".join(f"{tag(r)} {r['area']:.2f}" for r in rows[:20]) + (" + ..." if len(rows) > 20 else "")
    expl = f"{total:.2f} {unit} = {lines}. {describe(tk)}."
    head = interpretation(tk, [((f"{r['label']} {r['number']}" if r.get("number") and str(r["number"]) not in str(r["label"])
                                 else r["label"]), r["area"]) for r in rows])
    return total, items, (head + " " + expl) if head else expl


# ---------------------------------------------------------------- "the toilets" on a floor without a WC room

# Many dwellings have no separate WC room: the toilet is in the bathroom (Bad, KPH, Badezimmer). Someone
# asking for "tiling for the toilets" on such a floor means the rooms with a toilet in them. The labels
# say which rooms are bathrooms; a toilet symbol drawn inside the room (where the route reads fixture
# geometry) confirms it. The answer states the reading and is an estimate, because it rests on it.

def toilet_fallback(rooms: list[dict], tk: dict, name_key: str = "name", has_toilet=None
                    ) -> tuple[list[dict], list[dict], dict]:
    """select() for a takeoff, with "toilets" resolved when the floor has no separate WC room.

    has_toilet: callable room -> True (a toilet symbol inside), False (sanitary symbols inside, none a
    toilet), None (nothing drawn inside the room: unknown); or has_toilet=None when the floor carries no
    toilet symbols the route can read (scans read by labels, drawings without typed symbols).
    Returns (rooms used, generic rooms, interpretation). The interpretation is tk itself unless the
    fallback was needed; then a copy whose types name bathrooms in place of WC rooms and whose
    'toilet_rooms' entry records the reading (the route marks the answer an estimate, see
    fallback_reason). Unchanged cases: the question does not ask for toilets, it also asks for
    bathrooms, or a room labelled as a separate WC exists (WC rooms only; describe() says that
    bathrooms with a toilet are not included).

    Rooms used by the fallback: rooms labelled as a bathroom, except those whose drawn fixtures show
    no toilet (a bathroom with only a tub and a basin, next to a separate one with the WC); a bathroom
    with nothing drawn inside is used on its label and named as not confirmed. Plus rooms that carry
    no label at all but hold a toilet symbol (a numbered room whose name is missing). A room labelled
    as something else (a corridor) is never used."""
    used, generic = select(rooms, tk, name_key)
    types = tk["types"]
    if "wc" not in types or "bathroom" in types:
        return used, generic, tk
    if any("wc" in room_types(r.get(name_key)) for r in used):
        return used, generic, tk
    rest = [r for r in rooms if not any(r is u for u in used)]
    baths = [r for r in rest if "bathroom" in room_types(r.get(name_key))]
    left_out: list[dict] = []
    unlabelled: list[dict] = []
    unknown: list[dict] = []
    if has_toilet is not None:
        seen = [(r, has_toilet(r)) for r in baths]
        keep = [r for r, t in seen if t is True]
        unknown = [r for r, t in seen if t is None]
        left_out = [r for r, t in seen if t is False]
        unlabelled = [r for r in rest if not str(r.get(name_key) or "").strip() and has_toilet(r) is True]
        keep = [r for r in baths if any(r is k for k in keep + unknown)]      # drawing order
    else:
        keep = baths
    tk2 = dict(tk, types=[("bathroom" if t == "wc" else t) for t in types])
    def lab(r):           # label with the room number when the drawing has one ("Bad 4.05")
        name, no = str(r.get(name_key) or ""), str(r.get("number") or "")
        return f"{name} {no}".strip() if no and no not in name else name
    why = ""
    if not keep and not unlabelled:
        if not baths:
            why = "no separate WC room, no bathroom label" + (" and no unlabelled room with a toilet symbol"
                                                               if has_toilet is not None else "") + " on this floor"
        else:
            why = (f"no separate WC room, and none of the {len(baths)} bathroom(s) ({', '.join(lab(r) for r in baths[:6])}) "
                   f"has a toilet symbol drawn inside")
    tk2["toilet_rooms"] = {"confirmed": has_toilet is not None, "rooms": [lab(r) for r in keep],
                           "unlabelled": len(unlabelled), "left_out": [lab(r) for r in left_out],
                           "unknown": [lab(r) for r in unknown],
                           "other_types": [t for t in types if t != "wc"], "why": why}
    return used + keep + unlabelled, generic, tk2


def interpretation(tk: dict, rows: list[tuple[str, float]]) -> str:
    """The sentence that opens an answer built on the toilet fallback ('' otherwise).
    rows: (label, floor area) of the rooms used; rooms of other asked types are not named."""
    tr = tk.get("toilet_rooms")
    if not tr:
        return ""
    other = set(tr.get("other_types", []))
    named = [f"{lab} ({a:.2f} m2)" for lab, a in rows if not (room_types(lab) & other)]
    s = "No separate WC room on this floor; using the rooms that contain a toilet: " + (", ".join(named) or "none") + "."
    boxed = "" if tr.get("no_boxes") else " (boxed on the evidence image)"   # IFC evidence images do not box fixtures
    if tr["confirmed"]:
        if tr.get("unknown"):
            s += (f" {', '.join(tr['unknown'][:8])}: no fixtures drawn inside, used on the bathroom label (toilet not "
                  f"confirmed); the others have a toilet symbol drawn inside{boxed}.")
        else:
            s += f" Each has a toilet symbol drawn inside it{boxed}."
        if tr.get("unlabelled"):
            s += f" {tr['unlabelled']} of them carr{'ies' if tr['unlabelled'] == 1 else 'y'} no room name, only the toilet symbol."
        if tr["left_out"]:
            s += f" Left out, no toilet drawn inside: {', '.join(tr['left_out'][:8])}."
    else:
        s += " The drawing has no toilet symbols this route can read, so every room labelled as a bathroom is used."
    return s


def fallback_reason(tk: dict) -> str:
    """Estimate-tier reason for an answer built on the toilet fallback ('' otherwise)."""
    tr = tk.get("toilet_rooms")
    if not tr:
        return ""
    how = "bathrooms with a toilet symbol inside" if tr["confirmed"] else "bathroom labels, toilet not confirmed"
    if tr["confirmed"] and tr.get("unknown"):
        how += f"; {len(tr['unknown'])} bathroom(s) with nothing drawn inside used on the label alone"
    return "interpretation: no separate WC room, so 'toilets' read as the rooms that contain a toilet (" + how + ")"
