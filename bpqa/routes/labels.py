"""Count rooms by their printed labels from cached OCR tokens.

Labels are the Finnish real-estate abbreviations (MH, K, KH ...), a few Swedish words, and plain
room words in English and German (BEDROOM, KITCHEN, SCHLAFZIMMER, KUCHE ...). A token is split into
parts on + / , ( ) and spaces ("OH+RT+K" -> OH, RT, K; two-word labels such as "BED ROOM" are joined
first) and each part is matched against the words for the asked room type. Every matched part is one
room, boxed and numbered on the evidence image.

The English and German words (6+ letters) also match with one OCR error: one letter substituted,
missing or extra ("BEDROCM3", "BATHROON"). Such a read is marked `fuzzy`; the route then treats the
plan's OCR as error-prone (a count that needs a fuzzy read, or a zero on such a plan, is an estimate).
The short Finnish abbreviations never match fuzzily.
"""
from __future__ import annotations

import re

# part regexes per room type, matched against an upper-cased, accent-folded part
ROOM_PATTERNS = {
    "bedroom": r"MH\d?|MHH|MAKUUH(UONE)?\d?|SOVRUM|SOV\d?|BR\d?",   # BR, BR1: English listing plans
    "kitchen": r"K|KK|KT|KEI?T+I*O?|KETT+IO|KEITTOKOMERO|AVOK|AVOKEITTIO|TUPAK|TUPAKEITTIO|APUK|APUKEITTIO|KOK|KOKSVRA",
    "sauna": r"S|SAUNA|LH|LOYLYH(UONE)?|BASTU",
    "wc_room": r"WC\d?",
    "bathroom": r"KH\d?|KPH\d?|PH\d?|PE|PESUH(UONE)?\d?|SH\d?|KYLPYH(UONE)?|SUIHKUH(UONE)?|BADRUM|BAD|DUSCH|BA|BATH",   # BA, BATH: listing plans
    "balcony": r"PARVEKE\d?|PARV|LASIT(ETTU)?PARVEKE|LPARVEKE|BALKONG",
}


# Plain room words (English, German), whole parts after norm(); a trailing room number is allowed.
# Not included on purpose: BATH, SHOWER, DUSCHE (also printed next to the fixture symbol), generic
# ROOM / ZIMMER (no type).
ROOM_WORDS = {
    "bedroom": ("BEDROOM", "BEDRM", "SCHLAFZIMMER", "SCHLAFEN", "KINDERZIMMER", "ELTERNZIMMER", "GASTZIMMER",
                "MAKUUHUONE"),
    "kitchen": ("KITCHEN", "KITCHENETTE", "KUCHE", "KUECHE", "KOCHEN", "KOCHNISCHE", "WOHNKUCHE", "KEITTIO"),
    "sauna": ("SAUNA",),
    "wc_room": ("TOILET", "TOILETS", "LAVATORY", "CLOAKROOM", "GASTEWC"),
    "bathroom": ("BATHROOM", "SHOWERROOM", "WASHROOM", "ENSUITE", "BADEZIMMER", "DUSCHBAD", "PESUHUONE", "KYLPYHUONE",
                 "SUIHKUHUONE"),
    "balcony": ("BALCONY", "BALKON", "LOGGIA"),
}
# two-word labels joined before splitting ("BED ROOM" -> BEDROOM, "LIVING ROOM" -> LIVINGROOM)
JOIN_RE = r"\b(BED|BATH|SHOWER|WASH|LIVING|DINING|SITTING|UTILITY|STORE|BOX|CLOAK|FAMILY|DRAWING|BREAKFAST)\s+(ROOM)\b"
FUZZY_MIN = 6          # words this long match with one OCR error


def norm(s: str) -> str:
    s = s.upper().replace("Ä", "A").replace("Ö", "O").replace("Å", "A").replace("Ü", "U").replace("ß", "SS")
    s = re.sub(r"\bEN\s*-?\s*SUITE\b", "ENSUITE", s)
    return re.sub(JOIN_RE, r"\1\2", s)


def _edit1(a: str, b: str) -> bool:
    """True when a and b differ by exactly one substitution, insertion or deletion."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b):
        a, b = b, a
    i = next((k for k in range(len(a)) if a[k] != b[k]), len(a))
    return a[i:] == b[i + 1:]


def _stem(part: str) -> str:
    return re.sub(r"\d+\.?$|\.$", "", part)


# a combined bathroom + WC label whose separator the OCR dropped ("KPH / WC" read "KHWC", "KPHWC")
COMBO_RE = re.compile(r"(KPH|KH|PH|SH|PESUH|PE)(WC)|(WC)(KPH|KH|PH|SH|PESUH|PE)")


def _split_combo(p: str) -> list[str]:
    m = COMBO_RE.fullmatch(p)
    return [g for g in m.groups() if g] if m else [p]


def parts(text: str) -> list[str]:
    t = norm(text)
    t = re.sub(r"\(.*?\)", " ", t)           # "MH (VARAUS)" -> "MH"
    # "W.C" -> "WC", but two words joined by a dot stay two ("KEITTIO.RUOKAILU" -> KEITTIO, RUOKAILU)
    t = re.sub(r"([A-Z]+)\.(?=[A-Z])", lambda m: m.group(1) + (" " if len(m.group(1)) >= 3 and len(m.string[m.end():].split(None, 1)[0].split(".")[0]) >= 3 else ""), t)
    ps = [p for p in re.split(r"[+/,;&\s()\[\]=]+", t) if p]
    ps = [q for p in ps for q in _split_combo(p)]
    # apartment-type summaries ("3H+K+S", "1H+KK 32.5 m2") describe the flat, they are not room labels
    if any(re.fullmatch(r"\d*H", p) for p in ps) or re.match(r"\d\s*H\s*K", t):
        return []
    return ps


def exact(obj: str, part: str) -> bool:
    pat = ROOM_PATTERNS.get(obj)
    if pat and re.fullmatch(f"(?:{pat})\.?", part):
        return True
    return _stem(part) in ROOM_WORDS.get(obj, ())


def fuzzy(obj: str, part: str) -> str | None:
    """The English/German word of this room type that `part` misses by one OCR error, if any. A part that
    reads exactly as any room or plan word is never fuzzy."""
    st = _stem(part)
    if len(st) < FUZZY_MIN - 1 or not st.isalpha() or any(exact(o, part) for o in ROOM_PATTERNS) or _plan_word(part):
        return None
    hits = [w for o in ROOM_PATTERNS for w in ROOM_WORDS.get(o, ()) if len(w) >= FUZZY_MIN and _edit1(st, w)]
    own = [w for w in hits if w in ROOM_WORDS.get(obj, ())]
    return own[0] if own and len(hits) == len(own) else None    # ambiguous between types: no match


def full_word(obj: str, part: str) -> bool:
    """`part` is an English or German room word of this type read exactly (BEDROOM, KUCHE), not an
    abbreviation and not a one-error read."""
    return _stem(part) in ROOM_WORDS.get(obj, ())


def match(obj: str, part: str) -> bool:
    return exact(obj, part) or fuzzy(obj, part) is not None


def quad_box(q):
    xs = [p[0] for p in q]
    ys = [p[1] for p in q]
    return min(xs), min(ys), max(xs), max(ys)


def is_label(text: str) -> bool:
    return any(match(o, p) for p in parts(text) for o in ROOM_PATTERNS)


def _overlap(b, c, thr):
    ix = max(0, min(b[2], c[2]) - max(b[0], c[0]))
    iy = max(0, min(b[3], c[3]) - max(b[1], c[1]))
    a1 = max((b[2] - b[0]) * (b[3] - b[1]), 1e-6)
    a2 = max((c[2] - c[0]) * (c[3] - c[1]), 1e-6)
    return ix * iy > thr * min(a1, a2)


# common plan words (rooms that are not asked about), used to decide whether an image is mirrored and
# whether enough of the plan's labels were readable for a zero to mean something
PLAN_WORDS = (r"RUOKAILU|RUOKAILUTILA|RUOKAH(UONE)?|OH|ET|KHH|VH|AULA|RT|TK|ETEINEN|OLOHUONE|VAR|VARASTO|TERASSI|KERROS|PUKUH(UONE)?|TEKN|"
              r"TUULIKAAPPI|TK|ASKH|LVH|IV|VAATEH(UONE)?|KUISTI|TAKKA|PORRAS|KELLARI|ALAKERTA|YLAKERTA|PARVI|"
              # English
              r"LIVINGROOM|LOUNGE|DININGROOM|SITTINGROOM|FAMILYROOM|DRAWINGROOM|HALL|HALLWAY|LANDING|LOBBY|"
              r"CORRIDOR|ENTRANCE|PORCH|STORE|STOREROOM|STORAGE|CLOSET|PANTRY|UTILITY|UTILITYROOM|BOXROOM|"
              r"STUDY|OFFICE|GARAGE|TERRACE|PATIO|DINING|LIVING|"
              # German
              r"WOHNEN|WOHNZIMMER|ESSEN|ESSZIMMER|FLUR|DIELE|ABSTELLRAUM|ABSTELLKAMMER|GARDEROBE|ARBEITSZIMMER|"
              r"BURO|KELLER|TERRASSE|VORRAUM|TREPPENHAUS|HAUSWIRTSCHAFTSRAUM")


def _plan_word(part: str) -> bool:
    return bool(re.fullmatch(f"(?:{PLAN_WORDS})\d?\.?", part))


def is_word(text: str) -> bool:
    return is_label(text) or any(_plan_word(p) for p in parts(text))


def mirrored(tokens, min_conf=0.5) -> bool:
    """True when the left-right mirrored reads find clearly more plan vocabulary than the normal ones.

    Mirrored garbage sometimes spells a short label ("SH+KL"), so the mirrored side has to win on
    vocabulary by a wide margin and also on overall read confidence.
    """
    words = {False: 0.0, True: 0.0}
    allc = {False: 0.0, True: 0.0}
    for t in tokens:
        m = bool(t.get("mirror"))
        if len(t["text"]) >= 2:
            allc[m] += t["conf"] ** 4
        if t["conf"] >= min_conf and is_word(t["text"]):
            words[m] += t["conf"]
    return words[True] > 2 * words[False] + 2 and allc[True] > allc[False]


def dedupe(tokens, iou_thr=0.3, min_conf=0.5, prefer=None):
    """One reading per text location across the four rotation passes.

    The same label is read once per orientation, and the wrong orientations produce confident
    garbage (an upside-down "MH" reads "HW"). Among overlapping readings, a reading that is a
    known room abbreviation wins (or a reading `prefer` accepts); otherwise the most confident one.
    """
    prefer = prefer or is_label
    m = mirrored(tokens, min_conf)
    tokens = [t for t in tokens if bool(t.get("mirror")) == m]
    keep = []
    order = sorted((t for t in tokens if t["conf"] >= min_conf), key=lambda t: (not prefer(t["text"]), -t["conf"]))
    for t in order:
        b = quad_box(t["quad"])
        if not any(_overlap(b, quad_box(k["quad"]), iou_thr) for k in keep):
            keep.append(t)
    return keep


def read_labels(tokens, min_conf=0.5) -> list[dict]:
    """The plan's room labels and plan words, one reading per text location across all rotations (and the
    mirror when the plan is mirrored). The one reading every route shares: find_rooms, the consistency checks
    and the area route's "labels read" lists all start from dedupe()."""
    return [t for t in dedupe(tokens, min_conf=min_conf) if is_word(t["text"])]


def clean_token(text: str) -> bool:
    """A label token: every part is a room/plan word or a bare number (area), no mixed codes like "TM5ES2,S"."""
    ps = parts(text)
    if not ps or len(ps) > 4:
        return False
    for p in ps:
        if re.fullmatch(r"[\d.,]+(M2|M)?", p):
            continue
        if not (any(match(o, p) for o in ROOM_PATTERNS) or _plan_word(p)):
            if re.search(r"\d", p) or len(ps) > 1:
                return False
    return True


def find_rooms(tokens, obj, min_conf=0.5):
    hits = []
    for t in dedupe(tokens, min_conf=min_conf):
        if not clean_token(t["text"]):
            continue
        for p in parts(t["text"]):
            if match(obj, p):
                hits.append({"text": t["text"], "part": p, "conf": t["conf"], "box": quad_box(t["quad"]), "rot": t["rot"],
                             "fuzzy": fuzzy(obj, p)})
    # one label read twice side by side (two overlapping text lines) is one room
    out = []
    for h in sorted(hits, key=lambda h: -h["conf"]):
        b = h["box"]
        size = max(b[2] - b[0], b[3] - b[1])
        if any(o["part"] == h["part"] and abs((b[0] + b[2]) - (o["box"][0] + o["box"][2])) / 2 < 0.8 * size
               and abs((b[1] + b[3]) - (o["box"][1] + o["box"][3])) / 2 < 0.8 * size for o in out):
            continue
        out.append(h)
    return out


def fuzzy_reads(tokens, min_conf=0.5) -> list[tuple[str, str]]:
    """(token text, room word) for every room word on the plan that was read only with one OCR error."""
    out = []
    for t in dedupe(tokens, min_conf=min_conf):
        if not clean_token(t["text"]):
            continue
        for p in parts(t["text"]):
            for o in ROOM_PATTERNS:
                w = fuzzy(o, p)
                if w:
                    out.append((t["text"], w))
    return out


def _squash(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", norm(text))


def label_reads(tokens, label: str, min_conf=0.5) -> list[dict]:
    """Readings of one named room label ("GARAGE", "Bed room"), compared without spaces and punctuation; a
    trailing room number is allowed ("GARAGE 2"). Labels of 6+ letters also match with one OCR error
    (`fuzzy` is then the label). One location read twice counts once (dedupe)."""
    want = _squash(label)
    if not want:
        return []

    def hit(text):
        got = _squash(text)
        stem = re.sub(r"\d+$", "", got) if not want[-1].isdigit() else got
        if got == want or stem == want:
            return "exact"
        if len(want) >= FUZZY_MIN and want.isalpha() and _edit1(stem, want):
            return "fuzzy"
        return None

    out = []
    for t in dedupe(tokens, min_conf=min_conf, prefer=lambda x: hit(x) is not None):
        h = hit(t["text"])
        if h:
            out.append({"text": t["text"], "part": want, "conf": t["conf"], "box": quad_box(t["quad"]), "rot": t["rot"],
                        "fuzzy": label if h == "fuzzy" else None})
    return out
