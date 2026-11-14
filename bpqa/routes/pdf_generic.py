"""Convention-free reading of a vector PDF plan: door swings and printed room labels.

`pdf_vector` recognises Stoa's drawing conventions (room fills, door-opening quads touching swing
arcs). Plans from other offices often have neither: black wall fills, rooms that are only labels,
doors drawn as a quarter circle plus a leaf. For those, two readings need no scale and no style:

  door swings  an open curve that is a circular arc of 60-120 degrees, whose centre (the hinge) is
               the end of a straight leaf of the arc's radius. Radii must agree with the other swings
               on the sheet (0.5-2x their median), and with a known scale lie within 0.5-1.3 m. Two
               swings hinged at the two ends of one opening, free ends meeting, are one double door.
  room labels  text spans equal to the asked label, or naming the asked room type (bedroom, kitchen,
               bath ...), each distinct position one room.

A count of 0 is never answered from these readings: no swing found means the convention is not
recognised, not that the plan has no doors.
"""
from __future__ import annotations

import math
import re

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from .dxf_generic import label_eq, name_classes, text_room_types


def _circle(p1, p2, p3):
    (x1, y1), (x2, y2), (x3, y3) = p1, p2, p3
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-9:
        return None
    ux = ((x1 ** 2 + y1 ** 2) * (y2 - y3) + (x2 ** 2 + y2 ** 2) * (y3 - y1) + (x3 ** 2 + y3 ** 2) * (y1 - y2)) / d
    uy = ((x1 ** 2 + y1 ** 2) * (x3 - x2) + (x2 ** 2 + y2 ** 2) * (x1 - x3) + (x3 ** 2 + y3 ** 2) * (x2 - x1)) / d
    return (ux, uy), math.dist((ux, uy), p1)


def _arc_of(path):
    """(centre, r, span_deg, ends) when an open path is one circular arc, else None."""
    if "c" not in path.ops or len(path.subpaths) != 1 or path.closed:
        return None
    if set(path.ops) - {"c", "l"} or path.ops.count("l") > 0:
        return None
    pts = path.pts
    if len(pts) < 4 or math.dist(pts[0], pts[-1]) < 1e-6:
        return None
    fit = _circle(pts[0], pts[len(pts) // 2], pts[-1])
    if fit is None:
        return None
    c, r = fit
    if r <= 0 or max(abs(math.dist(c, p) - r) for p in pts) > 0.04 * r:
        return None      # not circular (an S-curve, a spline)
    chord = math.dist(pts[0], pts[-1])
    span = math.degrees(2 * math.asin(min(1.0, chord / (2 * r))))
    mid = pts[len(pts) // 2]
    # an arc of more than 180 degrees bulges away from the chord by more than r
    if LineString([pts[0], pts[-1]]).distance(Point(mid)) > r:
        span = 360 - span
    return c, r, span, (pts[0], pts[-1]), pts


def swing_doors(vp, m_per_pt: float | None = None):
    """Boxes (page pt) of door swings, and a sentence on how they were found; (None, reason) if none."""
    arcs = []
    for p in vp.paths:
        if p.kind != "s":
            continue
        a = _arc_of(p)
        if a is not None and 60 <= a[2] <= 120:
            arcs.append(a)
    if not arcs:
        return None, "no circular quarter-arc door swings in the linework"
    # straight segments for the leaf test
    segs = []
    for p in vp.paths:
        if p.kind == "f" or "c" in p.ops:
            continue
        for sp in p.subpaths:
            for a, b in zip(sp, sp[1:]):
                if math.dist(a, b) > 0:
                    segs.append((a, b))
    ends = [Point(a) for a, _ in segs] + [Point(b) for _, b in segs]
    tree = STRtree(ends) if ends else None
    with_leaf = []
    for c, r, span, (e0, e1), apts in arcs:
        ok = False
        if tree is not None:
            for k in tree.query(Point(c).buffer(0.15 * r)):
                a, b = segs[k % len(segs)]
                if abs(math.dist(a, b) - r) <= 0.2 * r:
                    ok = True
                    break
        if ok:
            with_leaf.append((c, r, span, (e0, e1), apts))
    if not with_leaf:
        return None, f"{len(arcs)} quarter arc(s) found but none has a straight leaf from its centre; not door swings"
    radii = sorted(a[1] for a in with_leaf)
    med = radii[len(radii) // 2]
    keep = [a for a in with_leaf if 0.5 * med <= a[1] <= 2.0 * med]
    if m_per_pt:
        keep = [a for a in keep if 0.5 <= a[1] * m_per_pt <= 1.3]
    if not keep:
        return None, "door-swing candidates have implausible sizes"
    # a legend entry: the symbol printed next to the word for door ("Porta", "Door", "Tuer")
    door_words = [s for s in vp.spans if len(s.text) <= 25 and "door" in name_classes(s.text.replace(chr(0xFFFD), "u"), sfb=False)]
    legend = [a for a in keep if any(math.dist(a[0], s.c) < 4 * a[1] for s in door_words)]
    keep = [a for a in keep if a not in legend]
    if not keep:
        return None, "the only door swings are legend symbols"
    used, boxes, n_double = set(), [], 0
    for i, (c, r, span, ends, apts) in enumerate(keep):
        if i in used:
            continue
        used.add(i)
        pts = [c, *apts]
        for j in range(i + 1, len(keep)):
            if j in used:
                continue
            c2, r2, _, ends2, apts2 = keep[j]
            d = math.dist(c, c2)
            if abs(r - r2) > 0.25 * max(r, r2) or abs(d - (r + r2)) > 0.1 * d:
                continue
            if min(math.dist(p, q) for p in ends for q in ends2) < 0.1 * (r + r2):
                used.add(j)
                pts += [c2, *apts2]
                n_double += 1
                break
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    how = (f"{len(keep)} door swing(s): circular arcs of 60-120 degrees with a straight leaf of the same "
           f"radius from the hinge")
    if n_double:
        how += f"; {n_double} pair(s) hinged at both ends of one opening counted as double doors"
    if legend:
        how += f"; {len(legend)} legend symbol(s) next to the word for door left out"
    dropped = len(with_leaf) - len(keep) - len(legend)
    if dropped:
        how += f"; {dropped} arc(s) of another size left out"
    return boxes, how


def _distinct(spans, tol):
    out = []
    for s in spans:
        if all(math.dist(s.c, o.c) > tol for o in out):
            out.append(s)
    return out


LEGEND_RE = re.compile(r"^(legend|legende|legenda|leyenda|selite|selitteet|teckenforklaring|tegnforklaring|"
                       r"signaturen|zeichenerklarung|symbols?|symboles|simboli)\b")


def legend_box(vp):
    """Area under a legend heading: symbols there are explanations, not rooms or doors."""
    from ..query import fold
    W, H = vp.rect.width, vp.rect.height
    heads = [s for s in vp.spans if LEGEND_RE.match(fold(s.text.strip()))]
    return [(h.bbox[0] - 0.02 * W, h.bbox[1] - 0.01 * H, h.bbox[0] + 0.4 * W, h.bbox[3] + 0.25 * H) for h in heads]


def _outside_legend(spans, boxes):
    return [s for s in spans if not any(b[0] <= s.c[0] <= b[2] and b[1] <= s.c[1] <= b[3] for b in boxes)]


def label_spans(vp, label: str):
    tol = 0.01 * max(vp.rect.width, vp.rect.height)
    hits = _outside_legend([s for s in vp.spans if label_eq(s.text, label)], legend_box(vp))
    return _distinct(hits, tol)


def type_spans(vp, rtype: str):
    tol = 0.01 * max(vp.rect.width, vp.rect.height)
    hits = [s for s in vp.spans if len(s.text) <= 40 and not re.search(r"\d+[.,]?\d*\s*m", s.text.lower())
            and rtype in text_room_types(s.text)]
    return _distinct(_outside_legend(hits, legend_box(vp)), tol)


def room_label_spans(vp):
    """Spans that read as a room name of any known type (for the 'enough labels read' gate)."""
    return [s for s in vp.spans if len(s.text) <= 40 and (text_room_types(s.text) - {"room"})]


ROOM_TYPE_OBJ = {"bedroom": "bedroom", "kitchen": "kitchen", "bathroom": "bathroom", "wc_room": "wc",
                 "sauna": "sauna", "balcony": "balcony"}
