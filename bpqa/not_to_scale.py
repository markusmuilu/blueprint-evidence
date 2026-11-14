"""Notes printed on a drawing saying it is not to scale ("EI MITTAKAAVASSA", "NOT TO SCALE", "NTS").

A pixel scale taken from doors or objects, or any area or length measured from the drawing, rests on the drawing
being to scale. Printed areas stay what they are (values the drawing states); a measured one gets a warning.

find(tokens) reads the OCR tokens of every pass (text read in any orientation): a token whose letters, with spaces,
punctuation and accents removed, contain one of the phrases (a phrase of 10 or more letters also matches with one OCR
error: "OHUEELLINEN" for "OHJEELLINEN"), or that is the bare abbreviation NTS.
"""
from __future__ import annotations

import re

# squashed phrases: upper case, a-z only, accents folded (ä -> A, ß -> SS)
PHRASES = (
    "EIMITTAKAAVASSA", "EIOLEMITTAKAAVASSA", "OHJEELLINEN", "SUUNTAAANTAVA",          # Finnish
    "NOTTOSCALE", "NOTDRAWNTOSCALE", "NOTTOSCALEDRAWING", "NOSCALE",                    # English
    "UNMASSSTABLICH", "NICHTMASSSTABSGETREU", "NICHTMASSSTABLICH", "OHNEMASSSTAB",      # German
    "EJSKALENLIG", "EJISKALA",                                          # Swedish
    "PASALECHELLE", "NONALECHELLE", "NONINSCALA", "NIETOPSCHAAL",                        # French, Italian, Dutch
)
ABBREVIATIONS = ("NTS", "NTS.")


def squash(text: str) -> str:
    t = (text or "").upper().replace("ß", "SS")
    for a, b in (("Ä", "A"), ("Ö", "O"), ("Ü", "U"), ("À", "A"), ("É", "E"), ("È", "E"),
                 ("Å", "A"), ("Ø", "O"), ("Æ", "AE"), ("Ê", "E"), ("Ç", "C")):
        t = t.replace(a, b)
    return re.sub(r"[^A-Z]", "", t)


def _within_one(a: str, b: str) -> bool:
    """Edit distance of a and b at most 1."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    s, l = (a, b) if len(a) < len(b) else (b, a)
    return any(l[:i] + l[i + 1:] == s for i in range(len(l)))


def hit(text: str) -> bool:
    raw = (text or "").strip().upper().replace(" ", "")
    if raw in ("NTS", "N.T.S", "N.T.S.", "NTS."):
        return True
    sq = squash(text)
    for p in PHRASES:
        if p in sq:
            return True
        if len(p) >= 10:
            n = len(p)
            if any(_within_one(sq[i:i + k], p) for k in (n - 1, n, n + 1) for i in range(0, max(0, len(sq) - k) + 1)):
                return True
    return False


def find(tokens, min_conf: float = 0.5) -> list[dict]:
    """[{"text", "box": (x0, y0, x1, y1), "conf"}] one per location (the same note read in several orientations
    is kept once, the most confident read)."""
    from .routes.labels import _overlap, quad_box
    out = []
    for t in sorted((t for t in tokens or [] if t["conf"] >= min_conf and hit(t["text"])), key=lambda t: -t["conf"]):
        b = quad_box(t["quad"])
        if not any(_overlap(b, o["box"], 0.3) for o in out):
            out.append({"text": t["text"].strip(), "box": b, "conf": t["conf"]})
    return out


def warning(notes: list[dict], what: str = "this approximation") -> str:
    quote = notes[0]["text"]
    return f"The drawing says it is not to scale (\"{quote}\"): {what} may be off."
