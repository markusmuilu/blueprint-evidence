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
    "stair": "stair", "office": "office", "meeting": "meeting room", "room": "generic room",
}
# Groups a question can name. "wet rooms" follows the Finnish usage of marka-tila for the rooms with
# a floor drain that are tiled: bathroom, WC, sauna. A utility room (KHH) is listed but not included.
GROUPS = {"wet": ("bathroom", "wc", "sauna")}

# Drawing labels, matched on the folded label (lower case, accents stripped, U+FFFD dropped).
# A label may name several types ("Wohnen/Essen", "ELUTUBA+KOOK"); each part is matched.
LABEL_RE = {
    "bathroom": r"bad|badezimmer|bain|bains|salle de bains?|salle d'eau|bagno|bagni|bathroom\s*\d*|bath|"
                r"shower(\s*room)?|dusche|douche(ruimte)?|badkamer|badevaerelse|badrum|vannituba|dushiruum|"
                r"kh\d?|kph\d?|ph\d?|pe|pesuh(uone)?\d?|sh\d?|kylpyhuone|pesuhuone|suihkuhuone|ban|bano|aseo|"
                r"salle de douche|doccia|bagno di servizio|ba",
    "wc": r"(m\.|w\.|staff|specimen|public|men'?s|women'?s)?\s*(toilet|toilets|toilette|toiletten|wc\d?|w\.c\.|"
          r"restroom|lavatory|tualett(ruum)?|tualettruum|vessa|sanitair|toilet(ruimte)?|toilettes|gabinetto|"
          r"servizio igienico|servizi igienici)",
    "sauna": r"sauna|s|leiliruum|bastu|loylyhuone|lh",
    "kitchen": r"kuche|kche|kueche|cuisine|cucina|kitchen(ette)?|keuken|kokken|tekokken|kok|kook|keittio|"
               r"keittokomero|k|kk|kt|avok|avokeittio|tupak|coc|cocina|kitchen\s*\d*|kochnische|cuisinette|"
               r"coin cuisine|cucinino|angolo cottura",
    "bedroom": r"schlafzimmer|chambre( a coucher)?|chambre a coucher|camera( da letto)?|camera da letto|"
               r"bedroom\s*\d*|slaapkamer|sovevaerelse|soverum|sovrum|magamistuba|mh\d?|makuuhuone\d?|hab\d?|"
               r"kinderzimmer|elternzimmer|gastezimmer|chambre d'enfants?|chambre parentale|chambre d'amis|"
               r"camera matrimoniale|camera singola|camera doppia|cameretta|br",
    "living": r"wohnen|wohnzimmer|sejour|sjour|salon|soggiorno|living( room)?|woonkamer|stue|opholdsrum|"
              r"elutuba|oh|olohuone|sal|salle de sejour|salotto|wohnraum|wohnstube|stube|"
              r"lounge|sitting( room)?|family room|drawing room|lounge ?/ ?diner|vardagsrum|lr",
    "dining": r"essen|repas|pranzo|dining|eetkamer|spisestue|rt|ruokailu(tila)?|ruokailuh|esszimmer|"
              r"salle a manger|sala da pranzo|dr",
    "hall": r"korridor|flur|diele|eingang|entree|couloir|corridoio|disimpegno|ingresso|corridor|hall|hallway|"
            r"foyer|entrance|gang|esik|et|eteinen|aula|tk|tuulikaappi|dist|kaytava|halle|eingangshalle|vestibule|"
            r"hall d'entree|degagement|anticamera|atrio",
    "utility": r"khh|kodinhoitohuone|buanderie|waschkuche|waschraum|lavanderia|laundry|utility|bijkeuken|"
               r"bryggers|wasruimte|waschen|trocknen|sechoir|essiccatoio",
    "storage": r"reduit|rduit|abstellraum|ripostiglio|storage|store|berging|kast|depot|vh\d?|var\d?|varasto|"
               r"vaatehuone|abiruum|cave|keller|closet|walk-in closet|compartiment cave|vano cantina|kellari|"
               r"kellerabteil|kellerraum|cantina|debarras|dispensa",
    "balcony": r"balkon|balcon|balcone|balcony|loggia|terrasse|terrazza|terrace|sitzplatz|parveke\d?|"
               r"terassi|lasitettu parveke|altan|veranda",
    "stair": r"treppenhaus|treppe|cage d'escalier|vano scala|stair(s|case)?|trappe(rum)?|trepikoda|porras",
    # offices and meeting rooms (QA 14 Nov: "how many offices" on Molio counted all 13 spaces): Danish Kontor,
    # Mode / Moderum (Mode from Møde), and the same words in the other languages of the data
    "office": r"kontor|kontorer|office|offices|buro|bureau|ufficio|toimisto|toimistohuone|tyohuone|arbetsrum|"
              r"kantoor|kabinet|kabinett|cabinet",
    "meeting": r"mode|moderum|modelokale|meeting( room)?|conference( room)?|besprechung(sraum|szimmer)?|"
               r"konferenz(raum)?|sitzungszimmer|salle de reunion|sala riunioni|neuvotteluhuone|neuvottelu|"
               r"kokoushuone|motesrum|konferensrum|vergaderruimte|vergaderzaal|noupidamisruum",
    "room": r"zimmer|piece|pice|stanza|room|raum|local|locale|huone|ruum",
}
_LABEL_RE = {k: re.compile(rf"(?:{v})") for k, v in LABEL_RE.items()}
# a label that starts with a WC word and goes on with a qualifier (who it is for, where it is) is a WC room
WC_HEAD = re.compile(r"(wc|w\.c\.|toilets?|toilette[ns]?|restrooms?|tualettruum)\s+[a-z]")


def _fold_label(s: str) -> str:
    return fold((s or "").replace("�", "")).strip()


def label_parts(name: str) -> list[str]:
    f = _fold_label(name)
    f = re.sub(r"\(.*?\)", " ", f)
    # Spanish IFC codes "1IZQ-BAN", "2DER-HAB3": the room code is the last dash-separated part
    f = re.sub(r"^(kph|kh|ph|sh|pesuh|pe)(wc)$|^(wc)(kph|kh|ph|sh|pesuh|pe)$", lambda m: "/".join(g for g in m.groups() if g), f)
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
        else:
            if WC_HEAD.match(p):                             # "WC Damen West", "Toilet men", "WC Herren"
                out.add("wc")
    if out - {"room"}:
        out.discard("room")
    return out


# ---------------------------------------------------------------- question side

# room-type words in a question (folded text, quotes and exclusion clauses removed). Finnish
# words are stems, so inflected forms match (kylpyhuoneisiin, keittioon, saunaan, vessoihin).
Q_TYPE_RE = {
    "wet": r"wet ?rooms?|wet areas?|markatil|kosteat tilat|kosteisiin tiloihin|nassraum|nassraeume|nassraume|nasszell|"
           r"pieces humides|locali umidi|vatrum|markatila|markatilojen",
    "bathroom": r"bathrooms?|bath rooms?|washrooms?|shower rooms?|kylpyhuone|pesuhuone|suihkuhuone|badezimmer|"
                r"\bbad\b|\bbader\b|salles? de bains?|salles? d'eau|salles? de douche|bagn[oi]\b|vannitub|badevaerelse|badrum",
    "wc": r"toilets?|\bwcs?\b|w\.c\.|restrooms?|lavator(y|ies)|powder rooms?|water closets?|vessa|vesso|"
          r"\bwc-|toilette|gabinett[oi]|servizi igienici|tualett|toalett|\bloos?\b|\bklos?\b",
    "sauna": r"saunas?|saunaan|saunan|saunoihin|saunoj|loylyhuone|saunen|bastu",
    "kitchen": r"kitchens?|kitchenette|keittio|keittokomero|\bkuche|cuisines?|cucin[ae]|cucinin[oi]|\bkoo?gi?\b|\bkook|kokken|\bkok(et|en)?\b",
    "bedroom": r"bedrooms?|makuuhuone|schlafzimmer|kinderzimmer|magamistub|sovevaerelse|chambres?( a coucher)?|camer[ae] da letto|sovrum",
    "living": r"living ?rooms?|lounge|sitting ?rooms?|family ?rooms?|olohuone|wohnzimmer|sejour|soggiorn[oi]|\bsalons?\b|"
              r"salott[oi]\b|vardagsrum",
    "dining": r"dining ?rooms?|esszimmer|salles? a manger|sale da pranzo|sala da pranzo",
    "hall": r"hallways?|corridors?|entrances?|entry|\bhalls?\b|eteinen|eteise|kaytav|korridor|\bflur|couloir|"
            r"corridoi|ingress[oi]\b|vestibules?\b|disimpegn",
    "utility": r"utility rooms?|laundry|kodinhoitohuone|waschkuche|buanderie|lavanderi",
    "storage": r"storage|store ?rooms?|closets?|walk-in|varasto|vaatehuone|abstellraum|reduit|ripostigli|"
               r"cantin[ae]\b|kellerabteil|debarras",
    "balcony": r"balcon(y|ies|s|e|i)?\b|terraces?|parveke|parvekkee|balkon|terrass",
    "office": r"\boffices?\b|\bkontor(er|et|erne)?\b|\bburos?\b|\bbueros?\b|\bbureaux?\b|\buffic[io]\b|toimisto|"
              r"tyohuone|arbetsrum|\bkantor(en)?\b|\bkantoor",
    "meeting": r"meeting ?rooms?|conference ?rooms?|board ?rooms?|moderum|modelokale|besprechungs\w*|konferenzr\w*|"
               r"salles? de reunion|sal[ae] riunion[ei]|neuvotteluhuone|kokoushuone|motesrum|konferensrum|"
               r"vergaderruimte|vergaderzal",
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
                       # "total size of the bedrooms", "makuuhuoneiden koko yhteensa" (genitive + koko = size of;
                       # "koko talo" = the whole house stays out)
                       r"\btotal size\b|\bsizes?\b(?=.*\b(?:combined|together|altogether|in total|summed)\b)|"
                       # "How big is the kitchen?" (QA 14 Nov: wrong on every IFC sheet, read as a room list)
                       r"\bhow (?:big|large) (?:is|are|was)\b|\b(?:kuinka|miten) (?:iso|suuri|suuria|isoja) (?:on|ovat)\b|"
                       r"\bwie gross (?:ist|sind)\b|\bhur stor[at]? (?:ar)\b|"
                       r"\b\w+(?:iden|jen|ien|den|tten|en)\s+koko\b|"
                       # "keittion koko" (genitive singular, the size of the kitchen); "koko talon" stays out
                       r"\b(?!koko\b)\w{4,}n\s+koko\b|"
                       # "kuinka iso kylpyhuone on" (the verb after the room word)
                       r"\b(?:kuinka|miten) (?:iso|suuri|isoja|suuria|isot|suuret)\s+\w+\s+(?:on|ovat)\b|"
                       r"how much|kuinka paljon|paljonko|wie viel|"
                       # one named room: "how big is the kitchen", "what size is the bathroom", "kuinka iso on keittio"
                       r"\bhow (?:big|large) is (?:the|this|that|our)\b|\bwhat(?: size|'s the size| is the size) (?:is|of) (?:the|this|that)\b|"
                       r"\b(?:kuinka|miten) (?:iso|suur)\w* on\b|\bwie gross ist (?:die|das|der)\b")
COUNT_Q = re.compile(r"\b(how many|kuinka monta|montako|wie viele|combien|quant[ie])\b")
NAMED = re.compile(r"\b(named|labell?ed|called|schedule|nimetty|bezeichnung)\b")
SCOPE_ID = re.compile(r"\b(house|building|block|wing|unit|apartment|flat|talo|rakennus|asunto|huoneisto)\s+[a-z0-9]{1,3}\b")
MATERIALS = {
    "tiles": r"\btil(e|es|ing|ed)\b|laatta|laato|laatoi|laatat|laattoj|klinkkeri|fliese|carrelage|piastrell|kakel",
    "flooring": r"flooring|floor covering|floor finish|parquet|laminat|vinyl|carpet|linoleum|lattiapaallyste|"
                r"parketti|laminaatti|vinyyli|\bmatto|bodenbelag|parkett|revetement de sol|pavimento",
    "paint": r"\bpaint|maali|maalaus|farbe|peinture|vernice|pittura",
    "waterproofing": r"waterproof|vedeneristy|membrane|abdichtung|etancheite",
    "screed": r"screed|tasoite|estrich|chape|massaus",
}
_MATERIALS = {k: re.compile(v) for k, v in MATERIALS.items()}
WALLS = re.compile(r"\bwalls?\b|seina|seini|\bwand|\bwande|\bmurs?\b|pareti|parete|\bvagg")
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
    surface = "walls" if walls and not re.search(r"\bfloors?\b|lattia|boden|\bgolv", f) else ("ceiling" if ceiling else "floor")
    if walls and re.search(r"\bfloors?\b|lattia|boden|\bgolv", f):
        surface = "floor+walls"
    group = next((g for g in GROUPS if _Q_TYPE_RE[g].search(f_incl)), None)
    out = {"types": types, "group": group, "material": material, "surface": surface, "aggregation": "sum",
           "floor_explicit": bool(re.search(r"\bfloors?\b|lattia|boden|\bgolv|\bsol\b|pavimento", f))}
    m = SCOPE_ID.search(f)
    if m:
        out["scope_named"] = m.group(0)     # "house A": not resolvable from labels in general
    # Waste for floor-only takeoffs: a stated percentage, or the default when the question says order/buy.
    # "needed"/"required" stays net (the dataset's own flooring questions answer net areas).
    from .walls import parse_inputs, DEFAULT_WASTE_PCT
    inp = parse_inputs(raw)
    if "waste_pct" in inp:
        out["waste_pct"], out["waste_src"] = inp["waste_pct"], "given in the question"
    elif out["material"] and re.search(r"\b(order|buy|purchase|tilata|tilaan|ostaa|bestellen|kaufen|commander|acheter|"
                                       r"ordinare|comprare)\w*", f):
        out["waste_pct"] = DEFAULT_WASTE_PCT
        out["waste_src"] = f"default {DEFAULT_WASTE_PCT:g}% cutting allowance (say 'with 15% waste' or 'no waste' to change)"
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
        how = ("a toilet symbol drawn inside" if tr["confirmed"] else
               "a bathroom name, no WC fixtures in the model" if tr.get("fixture_source") == "model" else
               "a bathroom label, toilet not visible in the drawing")
        s = (f"Takeoff{mat}: sum of net floor areas of the rooms that contain a toilet ({how}){other}, as there is no "
             f"separate WC room on this floor, as drawn inside the walls, no waste allowance")
    if tk.get("waste_pct"):
        s = s.replace(", no waste allowance", f", plus {tk['waste_pct']:g}% waste ({tk.get('waste_src', '')})")
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
    return f"Answer{tag}: {value:.2f} m2 total over {n_rooms} room(s), floor area only   route: {route} (cost €0)"


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


# ---------------------------------------------------------------- room-type counts

# count objects of the query parser -> room type of a drawing label
COUNT_TYPES = {"bedroom": "bedroom", "kitchen": "kitchen", "bathroom": "bathroom", "wc_room": "wc",
               "sauna": "sauna", "balcony": "balcony", "office": "office", "meeting_room": "meeting"}
# Types a generic room label ("Zimmer", "Piece", "Stanza", "Room") may stand for. Swiss plans label a room
# either by its use ("Schlafzimmer", "Chambre a coucher") or generically ("Zimmer", "Piece", "Stanza"). Of the
# 178 floors in the full dataset that use "Piece", one also has a "Chambre a coucher", and 64 label no living room,
# so there the living room is a "Piece" too. A generic room is never counted as one of these, and a zero is
# not claimed.
GENERIC_MAY_BE = {"bedroom", "living"}
WET = {"bathtub", "shower"}


def generic_reason(rooms: list[dict], types, name_key: str = "name") -> str:
    """Reason to abstain when no room is typed as one of `types` but generic rooms exist that may be
    them ('' otherwise): the drawing then does not say which rooms they are."""
    want = set(types)
    if not want <= GENERIC_MAY_BE or any(room_types(r.get(name_key)) & want for r in rooms):
        return ""
    generic = [r for r in rooms if room_types(r.get(name_key)) == {"room"}]
    if not generic:
        return ""
    labs = sorted({str(r.get(name_key)) for r in generic})
    living = [r for r in rooms if "living" in room_types(r.get(name_key))]
    names = " or ".join(TYPE_NAMES[t] for t in sorted(want))
    return (f"no room on this floor is labelled as a {names}; {len(generic)} room(s) carry only the generic label "
            f"{', '.join(repr(x) for x in labs)} (room)" + ("" if living else ", and no living room is labelled either, so "
            "the generic rooms include it") + f". The drawing does not say which of them are {names}s, so the count is "
            f"not given (a model reading the same plan would be guessing)")


def count_room_type(rooms: list[dict], obj: str, fixtures=None, name_key: str = "name") -> dict:
    """'How many bedrooms / bathrooms / WC rooms ...' from room labels on a vector drawing.

    rooms: dicts with a label under name_key (and 'number' where the drawing numbers rooms).
    fixtures: callable room -> set of fixture kinds drawn inside (toilet, washbasin, bathtub, shower), or None.
    Returns {value, rooms, note, estimate} or {value: None, why, final}. final=True means the labels were read
    and the drawing does not carry the answer; the router then does not hand the question to a model."""
    t = COUNT_TYPES[obj]
    name = TYPE_NAMES[t]
    typed = [r for r in rooms if t in room_types(r.get(name_key))]
    inferred = []
    if fixtures is not None and t in ("bathroom", "wc"):
        for r in rooms:
            if str(r.get(name_key) or "").strip() or not r.get("number"):
                continue
            k = fixtures(r)
            if (t == "bathroom" and k & WET) or (t == "wc" and "toilet" in k and not k & WET):
                inferred.append(r)
    used = typed + inferred
    if t in GENERIC_MAY_BE and not used:
        why = generic_reason(rooms, {t}, name_key)
        if why:
            return {"value": None, "why": why, "final": True}
    labs = sorted({str(r.get(name_key)) for r in typed})
    note = f"rooms labelled as {name}" + (f" ({', '.join(labs)})" if labs else "")
    est = []
    if inferred:
        what = f"{len(inferred)} numbered room(s) with no name, typed as {name} from the fixtures drawn inside"
        note = f"{note} plus {what}" if typed else what
        est.append(f"{len(inferred)} room(s) have no name; counted as {name} from their fixtures "
                   + ("(bathtub or shower)" if t == "bathroom" else "(a toilet and no bathtub or shower)"))
    generic = [r for r in rooms if room_types(r.get(name_key)) == {"room"}]
    if t in GENERIC_MAY_BE and generic:
        glabs = sorted({str(r.get(name_key)) for r in generic})
        est.append(f"{len(generic)} room(s) labelled only {', '.join(repr(x) for x in glabs)} (generic room) are not "
                   f"counted; some of them may be {name}s")
        note += f"; {len(generic)} generic room(s) not counted"
    if t == "bathroom" and fixtures is not None:
        wc_like = [r for r in typed if "toilet" in fixtures(r) and not fixtures(r) & WET]
        if wc_like:
            est.append(f"{len(wc_like)} room(s) labelled as bathroom hold a toilet but no bathtub or shower "
                       f"({', '.join(str(r.get('number') or r.get(name_key)) for r in wc_like[:6])}); counted as labelled, "
                       f"though they may be separate WCs")
    if t == "wc" and fixtures is not None:
        wc_like = [r for r in rooms if "bathroom" in room_types(r.get(name_key))
                   and "toilet" in fixtures(r) and not fixtures(r) & WET]
        if wc_like:
            est.append(f"{len(wc_like)} room(s) labelled as bathroom hold a toilet but no bathtub or shower; "
                       f"they may be separate WCs and are not counted")
    if not used and not est:
        note = f"no room labelled as {name} among {len({str(r.get(name_key)) for r in rooms if r.get(name_key)})} room names"
    return {"value": len(used), "rooms": used, "note": note, "estimate": "; ".join(est)}


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
    w = tk.get("waste_pct") or 0
    if w and unit == "m2":
        net, total = total, round(total * (1 + w / 100.0), 2)
        items.append({"totals": {"net_m2": net, "waste_pct": w, "waste_m2": round(total - net, 2), "order_m2": total}})
        expl = f"{total:.2f} {unit} to order = net {net:.2f} ({lines}) + {w:g}% waste {total - net:.2f}. {describe(tk)}."
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


def fixture_room_fallback(rooms: list[dict], tk: dict, kinds_of, name_key: str = "name") -> tuple[list[dict], dict]:
    """Bathrooms asked for on a floor where no room is labelled as one: the rooms with no label at all that hold a
    toilet, bathtub or shower (a numbered room whose name is missing, as the Swiss plans draw wet rooms).
    kinds_of: callable room -> set of fixture kinds drawn inside ('toilet', 'bathtub', 'shower', 'washbasin').
    Returns (rooms, tk) where tk is a copy that records the reading ('fixture_rooms'; interpretation() and
    fallback_reason() state it and the route marks the answer an estimate), or ([], tk) when no room qualifies.
    The same reading as the toilet fallback uses for unlabelled rooms, so the area and the tiling answers agree."""
    if "bathroom" not in tk["types"] or any("bathroom" in room_types(r.get(name_key)) for r in rooms):
        return [], tk
    got = [r for r in rooms if not str(r.get(name_key) or "").strip() and r.get("number")
           and kinds_of(r) & {"toilet", "bathtub", "shower"}]
    if not got:
        return [], tk
    kinds = sorted({k for r in got for k in kinds_of(r) & {"toilet", "bathtub", "shower"}})
    return got, dict(tk, fixture_rooms={"n": len(got), "kinds": kinds})


def _type_word(t: str) -> str:
    return {"wc": "WC room", "bathroom": "bathroom", "hall": "hall", "storage": "storage room"}.get(t, TYPE_NAMES.get(t, t))


def combined_rooms(tk: dict, labels) -> list[tuple[str, int, list[str]]]:
    """[(name, how many used rooms carry it, all its room types)] for used rooms whose name covers an asked type and
    another one at the same time ('ELUTUBA+KOOK' = living room + kitchen in one): their whole area is counted under
    the asked type. Room numbers in the label ('(3-1)', '2.04') are not part of the name."""
    want = set(tk["types"])
    names: dict[str, int] = {}
    for lab in labels:
        base = re.sub(r"\s*\([^)]*\)", "", lab)
        base = re.sub(r"\s+[a-z]?\d+([.-]\d+)*$", "", base, flags=re.I).strip()
        ts = room_types(base) - {"room"}
        if len(ts) > 1 and ts & want:
            names[base] = names.get(base, 0) + 1
    return [(n, c, sorted(room_types(n) - {"room"})) for n, c in names.items()]


def scope_sentence(tk: dict, labels) -> str:
    """Sentences about how the question's room word was read: a singular room type with several matching
    rooms (all summed), and combined rooms (whole area counted). '' when neither applies."""
    labels = list(labels)
    if tk.get("all_rooms"):      # baseboard for every room: no single room type was asked
        return ""
    parts = []
    if tk.get("single_type") and len(labels) > 1:
        t = _type_word(tk["types"][0])
        parts.append(f"The question names one {t}; all {len(labels)} matching rooms are summed "
                     f"(name the room number to pick one).")
    comb = combined_rooms(tk, labels)
    if comb:
        parts.append("Combined room name" + ("s" if len(comb) > 1 else "") + ": "
                     + "; ".join(f"{n}{f' (x{c})' if c > 1 else ''} names {' and '.join(_type_word(t) for t in ts)} in one room"
                                 for n, c, ts in comb[:4])
                     + "; the whole area of such a room is counted under the asked type.")
    return " ".join(parts)


def scope_reason(tk: dict, labels) -> str:
    """Estimate-tier reason for those readings ('' when the answer stands as proven)."""
    labels = list(labels)
    if tk.get("all_rooms"):
        return ""
    why = []
    if tk.get("single_type") and len(labels) > 1:
        why.append(f"the question names one {_type_word(tk['types'][0])} but {len(labels)} rooms match; all are summed")
    comb = combined_rooms(tk, labels)
    if comb:
        why.append(f"combined room name(s) ({', '.join(n for n, _, _ in comb[:3])}): the whole area is counted "
                   f"under the asked type")
    return "; ".join(why)


def interpretation(tk: dict, rows: list[tuple[str, float]]) -> str:
    """The sentence that opens an answer built on the toilet fallback ('' otherwise); scope_sentence() is
    appended for every takeoff. rows: (label, floor area) of the rooms used; rooms of other asked types
    are not named."""
    extra = scope_sentence(tk, [lab for lab, _ in rows])
    fr = tk.get("fixture_rooms")
    if fr:
        named = ", ".join(f"{lab} ({a:.2f} m2)" for lab, a in rows)
        extra = (f"No room on this floor is labelled as a bathroom; using the {fr['n']} unnamed room(s) that hold a "
                 f"{' / '.join(fr['kinds'])} symbol: {named}. " + extra).strip()
    return (_toilet_interpretation(tk, rows) + (" " if extra and tk.get("toilet_rooms") else "") + extra).strip()


def _toilet_interpretation(tk: dict, rows) -> str:
    tr = tk.get("toilet_rooms")
    if not tr:
        return ""
    other = set(tr.get("other_types", []))
    named = [f"{lab} ({a:.2f} m2)" for lab, a in rows if not (room_types(lab) & other)]
    if tr["confirmed"]:
        s = "No separate WC room on this floor; using the rooms that contain a toilet: " + (", ".join(named) or "none") + "."
    elif tr.get("fixture_source") == "model":
        s = ("No separate WC room on this floor and no WC fixtures in the model; using the rooms named as bathrooms "
             "(toilet not confirmed): " + (", ".join(named) or "none") + ".")
    else:
        s = ("No separate WC room on this floor and no toilet symbols drawn; using the rooms labelled as bathrooms "
             "(toilet not confirmed): " + (", ".join(named) or "none") + ".")
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
    elif tr.get("fixture_source") == "model":
        s += " The model carries no WC fixtures to confirm a room with."
    else:
        s += " The drawing has no toilet symbols this route can read, so every room labelled as a bathroom is used."
    return s


def fallback_label(tk: dict) -> str:
    """Short tag for an evidence header ('' when no fallback was used)."""
    if tk.get("toilet_rooms"):
        return "rooms with a toilet, no WC room" if tk["toilet_rooms"].get("fixture_source") != "model" else             "bathrooms used as toilet rooms"
    if tk.get("fixture_rooms"):
        return "unnamed rooms with wet fixtures read as bathrooms"
    return ""


def fallback_reason(tk: dict) -> str:
    """Estimate-tier reason for an answer built on the toilet fallback ('' otherwise)."""
    tr = tk.get("toilet_rooms")
    fr = tk.get("fixture_rooms")
    if fr and not tr:
        return (f"interpretation: no room is labelled as a bathroom, so {fr['n']} unnamed room(s) holding a "
                f"{' / '.join(fr['kinds'])} symbol are read as bathrooms")
    if not tr:
        return ""
    how = ("bathrooms with a toilet symbol inside" if tr["confirmed"] else
           "bathroom names, no WC fixtures in the model" if tr.get("fixture_source") == "model" else
           "bathroom labels, toilet not confirmed")
    if tr["confirmed"] and tr.get("unknown"):
        how += f"; {len(tr['unknown'])} bathroom(s) with nothing drawn inside used on the label alone"
    return "interpretation: no separate WC room, so 'toilets' read as the rooms that contain a toilet (" + how + ")"


# ================================================================ BEGIN scale-assumption (builder BC)
# A size assumption said in the question ("how big are the bedrooms if the doors are 0.9 m wide", "kitchen area,
# scale 1:100 on A3", "the WC is 0.7 m long"): the same assumption the Approximation box sets. router.solve reads it
# before the question is parsed and removes the clause, so the parser (rules and the language-model reader) sees
# only the question itself ("doors" in the clause must not turn an area question into a door count).
# The clause must open after a condition word (if, assuming, with, given ...) or a clause boundary (start, comma,
# semicolon, "and"): "how many doors are 0.9 m wide" asks something and is not read as an assumption.

_NUM = r"(\d+(?:[.,]\d+)?)"
_UNIT = r"(mm|cm|m|metres?|meters?|millimet(?:re|er)s?|centimet(?:re|er)s?)\b"
_LEAD = (r"(?:^|[,;:(]\s*|[.?!]\s+|\s(?:and|if|assuming|assume|supposing|suppose|given|with|when|where|taking|at|"
         r"jos|kun|oletetaan|oletuksella|olettaen)\s+)")
_BE = r"(?:\s+(?:are|is|were|was|be|on|ovat|ovat?)|\s*=)?(?:\s+(?:assumed|taken|set)(?:\s+(?:to\s+be|as|at))?)?(?:\s+(?:about|around|n\.?))?\s+"
_OBJ_RE = {
    "door_m": re.compile(_LEAD + r"(?:(?:that|the|all)\s+)?(?:door\s+openings?|doorways?|doors?|door\s+widths?|"
                         r"oviaukot|oviaukko|ovet|ovi|ovien\s+leveys)(?:\s+widths?)?" + _BE + _NUM + r"\s*" + _UNIT
                         + r"(?:\s+(?:wide|in\s+width|leveit[aä]|leve[aä]t?))?", re.I),
    "wc_m": re.compile(_LEAD + r"(?:(?:that|the|all)\s+)?(?:wc\s+pans?|toilet\s+(?:bowls?|pans?)|wcs?|toilets?|"
                       r"wc-istuimet|wc-istuin|pöntöt|pönttö)(?:\s+lengths?)?" + _BE + _NUM + r"\s*" + _UNIT
                       + r"(?:\s+(?:long|in\s+length|pitk[aä]t?|pitki[aä]))?", re.I),
    "bath_m": re.compile(_LEAD + r"(?:(?:that|the|all)\s+)?(?:bath\s*tubs?|baths?|tubs?|ammeet|amme)(?:\s+lengths?)?"
                         + _BE + _NUM + r"\s*" + _UNIT + r"(?:\s+(?:long|in\s+length|pitk[aä]t?|pitki[aä]))?", re.I),
}
_RATIO_RE = re.compile(_LEAD + r"(?:(?:the|a)\s+)?(?:(?:scale|mittakaava)(?:\s+(?:is|of|=|on))?\s*(?:assumed\s+)?|(?<=at ))"
                       r"1\s*:\s*(\d{1,4})(?:\s*(?:on|at|in|,)?\s*(?:an?\s+)?(?:sheet\s+|paper\s+)?(A[0-4])\b(?:\s+(?:sheet|paper))?)?",
                       re.I)
# "assuming 90 cm doors", "with 0.9 m wide doors": the size before the noun
_OBJ_PRE = {"door_m": re.compile(_LEAD + r"(?:(?:the|all)\s+)?" + _NUM + r"\s*" + _UNIT + r"(?:\s+wide)?\s+(?:door\s+openings?|doorways?|doors?)\b", re.I)}
_SHEET_RE = re.compile(_LEAD + r"(?:(?:the|a)\s+)?(?:sheet|paper)(?:\s+size)?(?:\s+(?:is|=))?\s+(?:an?\s+)?(A[0-4])\b", re.I)


def _metres(num: str, unit: str) -> float:
    v = float(num.replace(",", "."))
    u = unit.lower()
    return v / 1000 if u.startswith("mm") or u.startswith("millim") else v / 100 if u.startswith("c") else v


def _cut(text: str, m) -> str:
    """The text without the matched clause; a sentence end the clause opened with stays (\"area? The WC ...\")."""
    keep = m.group(0)[0] if m.group(0)[:1] in ".?!" else ""
    return text[:m.start()] + keep + text[m.end():]


def parse_assumption(question: str) -> tuple[dict, str]:
    """({"door_m": 0.9, "wc_m": .., "bath_m": .., "ratio": 100, "sheet": "A3"}, the question without the clause).
    Sizes out of range (scale_objects.ASSUME) are not read, so the question is left as it was."""
    from .scale_objects import ASSUME
    said, rest = {}, question
    for key, rx in _OBJ_RE.items():
        m = rx.search(rest) or (_OBJ_PRE[key].search(rest) if key in _OBJ_PRE else None)
        if not m:
            continue
        v = round(_metres(m.group(1), m.group(2)), 2)
        if not ASSUME[key]["lo"] <= v <= ASSUME[key]["hi"]:
            continue
        said[key] = v
        rest = _cut(rest, m)
    m = _RATIO_RE.search(rest)
    if m and 10 <= int(m.group(1)) <= 5000:
        said["ratio"] = int(m.group(1))
        if m.group(2):
            said["sheet"] = m.group(2).upper()
        rest = _cut(rest, m)
    m = _SHEET_RE.search(rest)
    if m and "sheet" not in said:
        said["sheet"] = m.group(1).upper()
        rest = _cut(rest, m)
    if not said:
        return {}, question
    rest = re.sub(r"\s+([?.!,])", r"\1", re.sub(r"\s{2,}", " ", rest)).strip(" ,;:")
    rest = re.sub(r"\b(if|assuming|and|with|given|when|jos)\s*([?.!]?)$", r"\2", rest, flags=re.I).strip(" ,;:")
    if not re.findall(r"\w+", rest):          # nothing left to ask: keep the question whole
        return said, question
    return said, rest
# ================================================================ END scale-assumption (builder BC)
