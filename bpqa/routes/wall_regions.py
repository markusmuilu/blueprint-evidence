"""Rooms from walls: regions enclosed by wall linework, with the room labels attached.

A plan that has no room outlines (no room layer, no room fills) still shows its rooms: wall lines
enclose them and a text label sits inside each. This module is pure geometry, shared by the generic
DXF route and the generic PDF route; it knows nothing about file formats or layer names.

  1. the wall lines are thickened by 2.5 cm (so lines that touch or miss by a hair still close a region) and
     removed from a box around the drawing; what is left is free space, one piece per enclosed room
     plus the outside;
  2. door openings leave gaps in the wall lines. Where door swings are drawn, the closed leaf of
     each swing (hinge to the arc's free end) seals its opening. Any gap left is bridged by "opening"
     the free space with a disc
     of growing radius (erode, then grow back with sharp corners), which cuts it at every neck
     narrower than the disc and leaves rooms wider than that untouched. The smallest radius that
     gives one region per label is used;
  3. each label is attached to the region that holds its centre.

The mapping is clean when every label lies in an enclosed region (not the outside) and no region
holds two different labels. Only a clean mapping may be called proven. When it is not clean the
result says why: labels outside every region, regions that hold several labels (a room that did not
close), or no bridging limit that separates the rooms.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import LineString, MultiLineString, Point, box
from shapely.strtree import STRtree

# widest gap bridged between wall ends, metres, tried in turn: a door opening is 0.7-1.2 m, a double door ~1.8 m
BRIDGE_M = (0.0, 1.0, 1.3, 1.8)
# with no known units the gaps are fractions of the drawing's longer side
BRIDGE_FRAC = (0.0, 0.02, 0.03, 0.04)
ALIGN_DEG = 25             # a bridge must continue the wall: within this angle of each wall end's direction
MAX_SEGMENTS = 40000       # beyond this the linework is not a plan's walls (a survey, a hatch): do not try


@dataclass
class Result:
    regions: list = field(default_factory=list)   # dicts: poly, labels (indices into the labels given)
    radius: float = 0.0                           # opening radius used, drawing units
    radius_m: float | None = None
    clean: bool = False
    problems: list = field(default_factory=list)  # plain sentences: why it is not clean
    outside: list = field(default_factory=list)   # label indices not in any enclosed region
    n_free: int = 0                               # enclosed pieces of free space (labelled or not)
    why_none: str = ""


def _segments(lines):
    out = []
    for p in lines:
        pts = [(float(x), float(y)) for x, y in p]
        out += [(a, b) for a, b in zip(pts, pts[1:]) if a != b]
    return out


def _pieces(geom):
    if geom.is_empty:
        return []
    return [g for g in (geom.geoms if hasattr(geom, "geoms") else [geom]) if g.geom_type == "Polygon" and g.area > 0]


def build(lines, labels, m_per_unit: float | None, closers=()) -> Result:
    """`lines`: polylines (lists of (x, y)) of wall-like linework. `labels`: dicts with "c" (centre, x y) and
    "text". `closers`: extra segments ((x, y), (x, y)) that seal door openings (a door swing's hinge to the
    free end of its arc is the closed leaf, which lies in the wall line). Returns a Result; regions hold the
    indices of the labels inside them."""
    res = Result()
    segs = _segments(lines)
    if not segs:
        res.why_none = "no wall linework"
        return res
    segs = segs + [(tuple(a), tuple(b)) for a, b in closers if tuple(a) != tuple(b)]
    if len(segs) > MAX_SEGMENTS:
        res.why_none = f"{len(segs)} wall line segments: too many to be the walls of one plan"
        return res
    if not labels:
        res.why_none = "no room labels"
        return res
    xs = [v for s in segs for v in (s[0][0], s[1][0])]
    ys = [v for s in segs for v in (s[0][1], s[1][1])]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    side = max(x1 - x0, y1 - y0)
    if side <= 0:
        res.why_none = "wall linework has no extent"
        return res
    unit = lambda m, frac: (m / m_per_unit) if m_per_unit else frac * side
    eps = unit(0.025, 0.0004)       # lines that miss each other by under 5 cm still close a region
    pad = unit(1.0, 0.02)
    env = box(x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    ring = env.exterior
    gaps = [unit(m, f) for m, f in zip(BRIDGE_M, BRIDGE_FRAC)]
    best = None
    for k, g in enumerate(gaps):
        use = segs + (_bridges(segs, g, eps) if g else [])
        barrier = MultiLineString([LineString(s) for s in use]).buffer(eps, cap_style=3, join_style=2)
        free = env.difference(barrier)
        pieces = []
        for poly in _pieces(free):
            poly = poly.buffer(eps, join_style=2)        # give back the hair the barrier took
            if not poly.intersects(ring):                # connected to the outside: not a closed room
                pieces.append(poly)
        regions = [{"poly": poly, "labels": []} for poly in pieces]
        outside = []
        for i, lb in enumerate(labels):
            hit = [r for r in regions if r["poly"].contains(Point(lb["c"]))]
            if not hit and lb.get("anchor"):
                hit = [r for r in regions if r["poly"].contains(Point(lb["anchor"]))]
            if hit:
                min(hit, key=lambda r: r["poly"].area)["labels"].append(i)
            else:
                outside.append(i)
        multi = [r for r in regions if len(r["labels"]) > 1]
        clean = not outside and not multi and any(r["labels"] for r in regions)
        score = (len(labels) - len(outside) - sum(len(r["labels"]) - 1 for r in multi), -k)
        cand = (clean, score, k, regions, outside, multi, len(pieces))
        if clean:
            best = cand
            break
        if best is None or score > best[1]:
            best = cand
    clean, _, k, regions, outside, multi, n_free = best
    res.radius, res.radius_m = gaps[k], (BRIDGE_M[k] if m_per_unit else None)
    res.n_free = n_free
    res.outside = outside
    res.regions = [r for r in regions if r["labels"]]
    res.clean = clean
    if outside:
        res.problems.append(f"{len(outside)} label(s) lie outside every closed region "
                            f"({', '.join(sorted({labels[i]['text'] for i in outside})[:6])})")
    if multi:
        res.problems.append(f"{len(multi)} region(s) hold more than one label "
                            f"({'; '.join(' + '.join(labels[i]['text'] for i in r['labels'][:3]) for r in multi[:3])})")
    if not res.regions:
        res.problems.append("no closed region holds a label")
    return res


def _dir(a, b):
    d = math.dist(a, b)
    return ((b[0] - a[0]) / d, (b[1] - a[1]) / d) if d else (0.0, 0.0)


def _bridges(segs, gap, eps):
    """Segments that close gaps in the wall linework: free wall ends (touching no other segment) are joined to the
    free end of another wall that continues the same line (both within ALIGN_DEG of the line between them) when they
    are at most `gap` apart. Each end is used once, nearest pairs first."""
    lines = [LineString(s) for s in segs]
    tree = STRtree(lines)
    ends = []                                           # (point, outward direction, segment index)
    for i, (a, b) in enumerate(segs):
        for p, q in ((a, b), (b, a)):
            near = [j for j in tree.query(Point(p).buffer(3 * eps)) if j != i and lines[j].distance(Point(p)) <= 3 * eps]
            if not near and math.dist(p, q) > 0:
                ends.append((p, _dir(q, p), i))
    cos = math.cos(math.radians(ALIGN_DEG))
    pairs = []
    for x in range(len(ends)):
        for y in range(x + 1, len(ends)):
            (p, dp, i), (q, dq, j) = ends[x], ends[y]
            d = math.dist(p, q)
            if i == j or d > gap or d <= 0:
                continue
            v = _dir(p, q)
            if dp[0] * v[0] + dp[1] * v[1] >= cos and dq[0] * -v[0] + dq[1] * -v[1] >= cos:
                pairs.append((d, x, y))
    used, out = set(), []
    for d, x, y in sorted(pairs):
        if x not in used and y not in used:
            used |= {x, y}
            out.append((ends[x][0], ends[y][0]))
    return out


def describe(res: Result, m_per_unit: float | None, sealed_by_doors: bool = False) -> str:
    """One sentence on how the regions were found, for the explanation."""
    if res.radius_m:
        bridged = f"wall gaps up to {res.radius_m:.1f} m between collinear wall ends bridged"
        if sealed_by_doors:
            bridged = "door openings sealed by their swings' closed leaf; " + bridged
    elif res.radius_m == 0 or m_per_unit:
        bridged = "door openings sealed by their swings' closed leaf" if sealed_by_doors else "walls closed without bridging"
    else:
        bridged = "wall gaps bridged at a fixed fraction of the sheet"
    return (f"{len(res.regions)} region(s) enclosed by wall lines ({bridged}), each holding "
            f"{'exactly one room label' if res.clean else 'room label(s)'}")
