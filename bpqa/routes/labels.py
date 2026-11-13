"""Count rooms by their printed labels (Finnish real-estate abbreviations) from cached OCR tokens.

A token is split into parts on + / , ( ) and spaces ("OH+RT+K" -> OH, RT, K) and each part is
matched against the abbreviations for the asked room type. Every matched part is one room, boxed
and numbered on the evidence image.
"""
from __future__ import annotations

import re

# part regexes per room type, matched against an upper-cased, accent-folded part
ROOM_PATTERNS = {
    "bedroom": r"MH\d?|MHH|MAKUUH(UONE)?\d?|SOVRUM|SOV\d?",
    "kitchen": r"K|KK|KT|KEI?T+I*O?|KETT+IO|KEITTOKOMERO|AVOK|AVOKEITTIO|TUPAK|TUPAKEITTIO|APUK|APUKEITTIO|KOK|KOKSVRA",
    "sauna": r"S|SAUNA|LH|LOYLYH(UONE)?|BASTU",
    "wc_room": r"WC\d?",
    "bathroom": r"KH\d?|KPH\d?|PH\d?|PESUH(UONE)?\d?|SH\d?|KYLPYH(UONE)?|SUIHKUH(UONE)?|BADRUM|BAD|DUSCH",
    "balcony": r"PARVEKE\d?|PARV|LASIT(ETTU)?PARVEKE|LPARVEKE|BALKONG",
}


def norm(s: str) -> str:
    s = s.upper().replace("Ä", "A").replace("Ö", "O").replace("Å", "A")
    return s


def parts(text: str) -> list[str]:
    t = norm(text)
    t = re.sub(r"\(.*?\)", " ", t)           # "MH (VARAUS)" -> "MH"
    t = re.sub(r"(?<=[A-Z])\.(?=[A-Z])", "", t)  # "W.C" -> "WC"
    ps = [p for p in re.split(r"[+/,;&\s()\[\]=]+", t) if p]
    # apartment-type summaries ("3H+K+S", "1H+KK 32.5 m2") describe the flat, they are not room labels
    if any(re.fullmatch(r"\d*H", p) for p in ps) or re.match(r"\d\s*H\s*K", t):
        return []
    return ps


def match(obj: str, part: str) -> bool:
    pat = ROOM_PATTERNS.get(obj)
    return bool(pat and re.fullmatch(f"(?:{pat})\.?", part))


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


# common plan words, used only to decide whether an image is mirrored
PLAN_WORDS = (r"OH|ET|KHH|VH|AULA|RT|TK|ETEINEN|OLOHUONE|VAR|VARASTO|TERASSI|KERROS|PUKUH(UONE)?|TEKN|"
              r"TUULIKAAPPI|TK|ASKH|LVH|IV|VAATEH(UONE)?|KUISTI|TAKKA|PORRAS|KELLARI|ALAKERTA|YLAKERTA|PARVI")


def is_word(text: str) -> bool:
    return is_label(text) or any(re.fullmatch(f"(?:{PLAN_WORDS})\d?\.?", p) for p in parts(text))


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


def dedupe(tokens, iou_thr=0.3, min_conf=0.5):
    """One reading per text location across the four rotation passes.

    The same label is read once per orientation, and the wrong orientations produce confident
    garbage (an upside-down "MH" reads "HW"). Among overlapping readings, a reading that is a
    known room abbreviation wins; otherwise the most confident one.
    """
    m = mirrored(tokens, min_conf)
    tokens = [t for t in tokens if bool(t.get("mirror")) == m]
    keep = []
    order = sorted((t for t in tokens if t["conf"] >= min_conf), key=lambda t: (not is_label(t["text"]), -t["conf"]))
    for t in order:
        b = quad_box(t["quad"])
        if not any(_overlap(b, quad_box(k["quad"]), iou_thr) for k in keep):
            keep.append(t)
    return keep


def clean_token(text: str) -> bool:
    """A label token: every part is a room/plan word or a bare number (area), no mixed codes like "TM5ES2,S"."""
    ps = parts(text)
    if not ps or len(ps) > 4:
        return False
    for p in ps:
        if re.fullmatch(r"[\d.,]+(M2|M)?", p):
            continue
        if not (any(match(o, p) for o in ROOM_PATTERNS) or re.fullmatch(f"(?:{PLAN_WORDS})\d?\.?", p)):
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
                hits.append({"text": t["text"], "part": p, "conf": t["conf"], "box": quad_box(t["quad"]), "rot": t["rot"]})
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
