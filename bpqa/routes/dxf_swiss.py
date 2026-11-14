"""DXF geometry route (Swiss Dwellings).

A question asked about a Swiss PDF/PNG is answered from the sibling DXF of the same drawing:
the layers carry the semantics (ROOM_OUTLINE, ROOM_TEXT, DOOR, WINDOW, FIXTURE, UNIT_OUTLINE),
so every answer is a deterministic computation over named entities.

Rules (measured on the dev split, see README):
  doors       = 5-vertex door-leaf polylines on DOOR (arcs over-count: double doors, sliding doors)
  windows     = connected groups of WINDOW polylines (each window is drawn as 3 parallel lines)
  apartments  = unit labels ("Whg. A", "App. B"), one per apartment
  toilets     = 48-vertex FIXTURE ellipses without a drain circle inside
  washbasins  = 48-vertex FIXTURE ellipses with a drain circle inside
  bathtubs    = 69-vertex FIXTURE outlines; showers = 5-vertex FIXTURE outlines
  room area   = ROOM_OUTLINE polygon containing the room's number label (shoelace area)
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import ezdxf
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from .. import takeoff
from .. import walls as wallmod
from .. import baseboard
from ..answer import Answer, abstain, estimate
from ..evidence import PALETTE, VectorCanvas, evidence_path, finish, record_steps
from .scope_guard import ambiguous_room, single_room_problem

ROUTE = "dxf-geometry"
TOILET_COL = (200, 30, 160)     # toilet symbols that confirm a bathroom as a room with a toilet (evidence)
NUM_RE = re.compile(r"^[A-Z]?\d+\.\d+$")
AREA_RE = re.compile(r"^([\d.]+)\s*m")
UNIT_RE = re.compile(r"^(Whg|App|Apt|Wohnung|Appartamento|Appartement)\.?\s+([A-Z]{1,2})$")
OUTDOOR = {"balkon", "balcon", "balcone", "loggia", "terrasse", "terrazza", "sitzplatz", "dachterrasse",
           "veranda", "aussenraum"}
SHAFT_VOID = {"schacht", "luftraum", "gaine", "vide", "cavedio", "vuoto", "shaft", "void",
              }
# What a Swiss door count counts: one closed door outline on layer DOOR per door opening. A double door is drawn
# as one outline across the opening with two swing arcs, so it counts once (checked on 60 Swiss DXFs: 89 outlines
# wider than 1.4 m, 68 of them with two arcs at the outline). The count itself is unchanged.
SWISS_DOOR_CONVENTION = "counted per doorway: a double door is one DOOR outline (with two swing arcs), so it counts once"

# A room printed with a number but no name counts as indoor when it holds a sanitary fixture or is wider
# than this (the short side of its minimum rotated rectangle): a shaft is narrow, a room one walks into is
# not. Chosen on the full dataset's dev split (net indoor totals 92.3% -> 94.9%, apartment totals 94.3% ->
# 96.2%; 0.8 to 1.2 m is a plateau; see CHANGELOG 2026-11-14 route-misses).
UNNAMED_MIN_WIDTH_M = 1.0


def short_side(poly) -> float:
    """Short side of the polygon's minimum rotated rectangle (its width), in the polygon's units."""
    xs = list(poly.minimum_rotated_rectangle.exterior.coords)
    return min(((xs[i][0] - xs[i + 1][0]) ** 2 + (xs[i][1] - xs[i + 1][1]) ** 2) ** 0.5 for i in range(4))


def _text_box(e, s, xy):
    """Approximate world box of a TEXT entity: cap height x 0.6 per character, placed by its alignment."""
    h = float(e.dxf.get("height", 0.2) or 0.2)
    w = 0.6 * h * max(len(s), 1) * float(e.dxf.get("width", 1.0) or 1.0)
    ha, va = e.dxf.get("halign", 0), e.dxf.get("valign", 0)
    x0 = xy[0] - w * {1: 0.5, 2: 1.0, 4: 0.5}.get(ha, 0.0)
    y0 = xy[1] - h * {2: 0.5, 3: 1.0}.get(va, 0.0)
    if ha == 4:                       # "middle": centred both ways
        y0 = xy[1] - h * 0.5
    return (x0, y0, x0 + w, y0 + h)


def draw_room_texts(cv, r) -> None:
    """The room's own TEXT entities (name, number, printed area) at their DXF positions, so the label step of the
    evidence points at text that is visible on the image (the base canvas draws linework only)."""
    for key, s in (("name_box", r.get("name")), ("number_box", r.get("number")), ("area_box", r.get("area_text"))):
        b = r.get(key)
        if b is None or not s:
            continue
        size = max(9, round((b[3] - b[1]) * cv.s * 0.95))
        cv.text(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), s, size=size, color=(60, 60, 60), bg=False)


def label_point(r):
    """Where the route's own room label goes: the representative point, moved below the room's name and number
    when it would cover them."""
    rp = r["poly"].representative_point()
    boxes = [b for b in (r.get("name_box"), r.get("number_box")) if b is not None]
    if not boxes:
        return rp.x, rp.y
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    if x0 - 0.8 <= rp.x <= x1 + 0.8 and y0 - 0.3 <= rp.y <= y1 + 0.3:
        return (x0 + x1) / 2, y0 - 0.35
    return rp.x, rp.y


def room_steps(cv, fl, r, types=None) -> list[dict]:
    """The reasoning for one room, in evidence pixels: the label read inside the outline (and the room type it
    names), where the area comes from (the ROOM_OUTLINE polygon; a printed area is the cross-check), and the
    outline itself."""
    def px(b):
        a, c = cv.px(b[0], b[3]), cv.px(b[2], b[1])
        return [a[0], a[1], c[0], c[1]]
    area = fl.room_area(r)
    steps = []
    boxes = [px(b) for b in (r.get("name_box"), r.get("number_box")) if b is not None]
    if boxes:
        name = r["name"] or "unnamed"
        tt = sorted(takeoff.room_types(r["name"] or ""))
        if types:
            tt = sorted(set(tt) & set(types)) or tt
        steps.append({"kind": "label", "title": "Label read",
                      "text": name + (f" ({r['number']})" if r["number"] else ""),
                      "note": f"read as {', '.join(tt)}" if tt else "room name inside the outline",
                      "rect": [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]})
    if r.get("area_box") is not None:
        agree = abs(r["area_label"] - area) <= 0.02 * max(r["area_label"], 1e-9)
        steps.append({"kind": "value", "title": "Size printed", "text": r["area_text"],
                      "note": f"cross-check: {'agrees with' if agree else 'differs from'} the outline's {area:.2f} m2",
                      "cap": f"printed {r['area_label']:g} m2",
                      "rect": px(r["area_box"])})
    steps.append({"kind": "region", "title": "Room outline", "text": f"{area:.2f} m2", "cap": f"outline {area:.2f} m2",
                  "note": "computed from the ROOM_OUTLINE polygon" + ("" if r.get("area_box") is not None else "; no area printed"),
                  "pts": [cv.px(x, y) for x, y in r["poly"].exterior.coords]})
    return steps


def _pts(e):
    return [(float(x), float(y)) for x, y in e.get_points("xy")]


class SwissFloor:
    def __init__(self, dxf_path: Path):
        msp = ezdxf.readfile(str(dxf_path)).modelspace()
        self.lines = []        # (layer, pts) for the base drawing
        self.circles = []
        self.rooms = []        # dicts: poly, name, number, area_label, unit
        self.units = []        # dicts: poly, letter, label_pt
        self.doors, self.windows_raw, self.fixtures, self.fix_circles = [], [], [], []
        texts = []
        tboxes = []            # world box of each TEXT, approximate (height x characters), for the evidence steps
        room_polys = []
        unit_polys = []
        for e in msp:
            t, layer = e.dxftype(), e.dxf.layer
            if t == "LWPOLYLINE":
                p = _pts(e)
                self.lines.append((layer, p + p[:1] if e.closed else p))
                if layer == "ROOM_OUTLINE" and len(p) > 2:
                    room_polys.append(Polygon(p).buffer(0))
                elif layer == "UNIT_OUTLINE" and len(p) > 2:
                    unit_polys.append(Polygon(p).buffer(0))
                elif layer == "DOOR" and len(p) == 5:
                    self.doors.append(p)
                elif layer == "WINDOW":
                    self.windows_raw.append(p)
                elif layer == "FIXTURE" and len(p) > 2:
                    self.fixtures.append(p)
            elif t == "CIRCLE":
                c = (e.dxf.center.x, e.dxf.center.y)
                self.circles.append((layer, c, e.dxf.radius))
                if layer == "FIXTURE":
                    self.fix_circles.append((c, e.dxf.radius))
            elif t == "ARC":
                pts = [(v.x, v.y) for v in e.flattening(0.02)]
                self.lines.append((layer, pts))
            elif t == "TEXT":
                use_align = e.dxf.get("halign", 0) or e.dxf.get("valign", 0)
                p = e.dxf.align_point if use_align else e.dxf.insert
                texts.append((layer, e.dxf.text.strip(), (p.x, p.y)))
                tboxes.append(_text_box(e, e.dxf.text.strip(), (p.x, p.y)))
        self.texts = texts
        allb = [_bbox(p) for _, p in self.lines if len(p) > 1]
        self.bounds = (min(b[0] for b in allb), min(b[1] for b in allb), max(b[2] for b in allb), max(b[3] for b in allb))

        # Rooms: attach number / name / printed area to the outline that contains them
        rooms = [{"poly": p, "name": None, "number": None, "area_label": None, "unit": None} for p in room_polys]
        for (layer, s, xy), tb in zip(texts, tboxes):
            if layer != "ROOM_TEXT" or UNIT_RE.match(s):
                continue
            pt = Point(xy)
            cands = [r for r in rooms if r["poly"].contains(pt)]
            if not cands:
                near = sorted(rooms, key=lambda r: r["poly"].distance(pt))
                cands = [near[0]] if near and near[0]["poly"].distance(pt) < 0.6 else []
            if not cands:
                continue
            r = min(cands, key=lambda r: r["poly"].area)
            if NUM_RE.match(s):
                r["number"] = s
                r["number_box"] = tb
            elif (m := AREA_RE.match(s)):
                r["area_label"] = float(m.group(1))
                r["area_text"], r["area_box"] = s, tb
            elif r["name"] is None:
                r["name"] = s
                r["name_box"] = tb
        self.rooms = rooms

        # Apartments: one label per unit; attach outline and rooms
        for layer, s, xy in texts:
            m = UNIT_RE.match(s)
            if m:
                pt = Point(xy)
                polys = [u for u in unit_polys if u.contains(pt)] or sorted(unit_polys, key=lambda u: u.distance(pt))[:1]
                self.units.append({"letter": m.group(2), "label": s, "label_pt": xy, "poly": polys[0] if polys else None})
        for r in rooms:
            rp = r["poly"].representative_point()
            for u in self.units:
                if u["poly"] is not None and u["poly"].buffer(0.05).contains(rp):
                    r["unit"] = u["letter"]
                    break

        # Windows: union-find groups of overlapping WINDOW polylines
        rects = [p for p in self.windows_raw if len(p) > 2]
        glazing = [p for p in self.windows_raw if len(p) == 2]
        if glazing and len(glazing) == 2 * len(rects):
            self.windows = [_bbox(p) for p in rects]     # frame rectangle + two glazing lines
        else:
            self.windows = _window_groups([_bbox(p) for p in self.windows_raw])
        self.window_check = len(self.windows) * 3 == len(self.windows_raw)

        self.fixture_centroids = [tuple(Polygon(p).centroid.coords[0]) for p in self.fixtures]
        # Sanitary fixtures by drawing template
        self.toilets, self.washbasins, self.bathtubs, self.showers = [], [], [], []
        for p in self.fixtures:
            n = len(p)
            if n == 48:
                poly = Polygon(p)
                has_drain = any(r < 0.06 and poly.contains(Point(c)) for c, r in self.fix_circles)
                (self.washbasins if has_drain else self.toilets).append(poly.bounds)
            elif n == 69:
                self.bathtubs.append(Polygon(p).bounds)
            elif n == 5:
                self.showers.append(Polygon(p).bounds)

    # -- helpers ---------------------------------------------------------------------------
    def room_area(self, r) -> float:
        return round(r["poly"].area, 2)

    def is_outdoor(self, r) -> bool:
        return (r["name"] or "").lower() in OUTDOOR

    def is_shaft(self, r) -> bool:
        return (r["name"] or "").lower() in SHAFT_VOID

    def counts_as_indoor(self, r) -> bool:
        """Numbered indoor room. Rooms printed with a number but no name are ambiguous (wet rooms,
        small storage, shafts); keep them if they hold a sanitary fixture or are wider than
        UNNAMED_MIN_WIDTH_M (narrower unnamed rooms without a fixture are shafts)."""
        if not r["number"] or self.is_outdoor(r):
            return False
        if r["name"]:
            return True
        return (short_side(r["poly"]) > UNNAMED_MIN_WIDTH_M
                or any(r["poly"].contains(Point(c)) for c in self.fixture_centroids))


def swiss_has_toilet(fl):
    """Room -> True when a toilet symbol (FIXTURE ellipse without drain) has its centre inside the room
    outline and the room is larger than the symbol (a sliver of shaft around the bowl is not the room);
    False when other FIXTURE shapes are inside but no toilet; None when nothing is drawn inside.
    None for the whole floor when it has no toilet symbols at all (nothing to confirm a bathroom with)."""
    if not fl.toilets:
        return None
    syms = [(Point((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), (b[2] - b[0]) * (b[3] - b[1])) for b in fl.toilets]
    others = [Point(c) for c in fl.fixture_centroids]

    def test(r):
        if any(r["poly"].area > a and r["poly"].contains(c) for c, a in syms):
            return True
        return False if any(r["poly"].contains(c) for c in others) else None
    return test


def swiss_room_fixtures(fl, r) -> set:
    """Fixture kinds drawn inside a room outline: toilet, washbasin, bathtub, shower."""
    out = set()
    for kind, boxes in (("toilet", fl.toilets), ("washbasin", fl.washbasins), ("bathtub", fl.bathtubs),
                        ("shower", fl.showers)):
        if any(r["poly"].contains(Point((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)) for b in boxes):
            out.add(kind)
    return out


# A door leaf is drawn across its wall's thickness, so a door in a room's own wall touches that room's outline (the
# inner wall face). Doors 0.1 to 0.35 m away belong to a neighbouring room (stair hall, store) and stay out.
DOOR_ON_OUTLINE_M = 0.03


def swiss_wall_rooms(fl, rooms_used):
    """Room records for bpqa/walls.py from Swiss DXF rooms. A floor plan has no heights, so the ceiling
    height is the stated default (walls.py) and door / window heights are stated assumptions. Openings:
    door leaves (DOOR, width = leaf length) that touch the room outline (a door in the wall between two rooms
    counts for both; a door in a neighbouring room's wall, 0.1 to 0.35 m away across the wall, does not); windows (grouped WINDOW strips) within 0.5 m along at least 30% of their
    width."""
    from shapely.geometry import Polygon as _P, box as _box
    out = []
    for r in rooms_used:
        poly = r["poly"]
        ops = []
        for d in fl.doors:
            leaf = _P(d).buffer(0)
            if leaf.is_empty or leaf.distance(poly) > DOOR_ON_OUTLINE_M:
                continue
            mrr = leaf.minimum_rotated_rectangle
            xs = list(mrr.exterior.coords)
            width = max(((xs[i][0] - xs[i + 1][0]) ** 2 + (xs[i][1] - xs[i + 1][1]) ** 2) ** 0.5 for i in range(4))
            ops.append({"kind": "door", "id": None, "width": width, "z0": 0.0, "z1": wallmod.DOOR_H_M,
                        "src": f"door leaf width from DOOR polyline; height ASSUMED {wallmod.DOOR_H_M:.2f} m",
                        "bbox": leaf.bounds})
        for b in fl.windows:
            g = _box(*b)
            if g.distance(poly) > 0.5:
                continue
            width = max(b[2] - b[0], b[3] - b[1])
            if poly.boundary.intersection(g.buffer(0.5)).length - 1.0 < 0.5 * width:
                continue
            ops.append({"kind": "window", "id": None, "width": width, "z0": wallmod.WINDOW_SILL_M,
                        "z1": wallmod.WINDOW_SILL_M + wallmod.WINDOW_H_M,
                        "src": f"window width from WINDOW strips; height ASSUMED {wallmod.WINDOW_H_M:.2f} m on a "
                               f"{wallmod.WINDOW_SILL_M:.2f} m sill", "bbox": b})
        out.append({"label": r["name"] or "unlabelled room", "number": r["number"], "floor_area": fl.room_area(r),
                    "floor_src": "ROOM_OUTLINE polygon", "poly": poly, "perimeter": wallmod.ring_length(poly),
                    "perimeter_src": "ROOM_OUTLINE polygon", "height": None, "height_src": "", "openings": ops})
    return out


def _bbox(p):
    xs, ys = [x for x, _ in p], [y for _, y in p]
    return (min(xs), min(ys), max(xs), max(ys))


def _window_groups(bbs, cross=0.6):
    """A window is drawn as three parallel strips spanning the wall thickness, with (nearly) the
    same extent along the wall. Neighbouring windows share an end edge, so plain overlap would
    merge them; strips join a group only when their along-wall intervals overlap by >= 80%."""
    groups = []
    for b in bbs:
        horiz = (b[2] - b[0]) >= (b[3] - b[1])
        lo, hi, c = (b[0], b[2], (b[1] + b[3]) / 2) if horiz else (b[1], b[3], (b[0] + b[2]) / 2)
        best = None
        for g in groups:
            if g["h"] != horiz or abs(g["c"] - c) >= cross or len(g["boxes"]) >= 3:
                continue
            inter = min(g["hi"], hi) - max(g["lo"], lo)
            union = max(g["hi"], hi) - min(g["lo"], lo)
            if union > 0 and inter / union >= 0.8:
                best = g
                break
        if best:
            best["boxes"].append(b)
        else:
            groups.append({"h": horiz, "lo": lo, "hi": hi, "c": c, "boxes": [b]})
    return [(min(b[0] for b in g["boxes"]), min(b[1] for b in g["boxes"]),
             max(b[2] for b in g["boxes"]), max(b[3] for b in g["boxes"])) for g in groups]


def _groups(geoms):
    parent = list(range(len(geoms)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(geoms)):
        for j in range(i + 1, len(geoms)):
            if geoms[i].intersects(geoms[j]):
                parent[find(i)] = find(j)
    out = {}
    for i, g in enumerate(geoms):
        out.setdefault(find(i), []).append(g)
    return [unary_union(v) for v in out.values()]


# Swiss plans draw bathroom basins as FIXTURE ellipses with a drain circle (the washbasin rule, 99.9% on dev). A
# kitchen sink is not told apart: kitchens only hold 4-vertex FIXTURE rectangles, the same shape as cookers and
# fridges. The dataset has no sink question on Swiss plans, so the count is an estimate that states this.
SINK_REASON = ("'sinks' read as the washbasins: FIXTURE ellipses with a drain circle, the rule that counts washbasins on "
               "these plans; kitchen sinks are not told apart from other 4-vertex FIXTURE rectangles, so they are not included")

SWISS_LAYERS = {"ROOM_OUTLINE", "ROOM_TEXT"}        # the convention this route's rules were built on
SWISS_OBJECT_LAYERS = {"DOOR", "WINDOW", "FIXTURE"}


@lru_cache(maxsize=32)
def swiss_scheme(dxf_path: str) -> bool:
    """True when the DXF follows the Swiss Dwellings layer convention: room outlines and room texts
    on ROOM_OUTLINE / ROOM_TEXT and object layers named DOOR / WINDOW / FIXTURE. Other files go to
    the generic DXF route, which abstains rather than returning a count of 0 from absent layers."""
    doc = ezdxf.readfile(dxf_path)
    used = {e.dxf.layer for e in doc.modelspace()}
    table = {l.dxf.name for l in doc.layers}
    return SWISS_LAYERS <= used and SWISS_OBJECT_LAYERS <= table


@lru_cache(maxsize=8)
def load(dxf_path: str) -> SwissFloor:
    return SwissFloor(Path(dxf_path))


def base_canvas(fl: SwissFloor) -> VectorCanvas:
    cv = VectorCanvas(fl.bounds)
    for layer, p in fl.lines:
        col = {"WALL": (90, 90, 90), "UNIT_OUTLINE": (200, 200, 200)}.get(layer, (175, 175, 175))
        cv.line(p, color=col, width=2 if layer == "WALL" else 1)
    for layer, c, r in fl.circles:
        cv.circle(c, r)
    return cv


# rooms that count as a "Zimmer" in a room count: living rooms, bedrooms and generic rooms (the Swiss listing
# convention); kitchen, bath, WC, corridor, storage, balcony and stair are listed but not counted
ZIMMER_TYPES = {"living", "bedroom", "room", "dining", "office"}


def apartment_room_count(fl, letter: str | None) -> dict:
    """'How many rooms in apartment A', 'Wieviele Zimmer hat die Wohnung?': the rooms inside one apartment outline whose
    label is a living room, bedroom or generic room. Without a letter the floor must have exactly one apartment."""
    units = sorted({r["unit"] for r in fl.rooms if r.get("unit")})
    if not units:
        return {"why": "no apartment outlines on this floor, so rooms cannot be assigned to an apartment"}

    def habitable(u):
        return [r for r in fl.rooms if r.get("unit") == u and fl.counts_as_indoor(r)
                and takeoff.room_types(r["name"]) & ZIMMER_TYPES]
    if letter is None:
        if len(units) != 1:
            per = ", ".join(f"{u}: {len(habitable(u))}" for u in units)
            return {"why": f"this floor has {len(units)} apartments ({', '.join(units)}); name one. Rooms counted as "
                           f"living room, bedroom or generic room per apartment: {per}", "final": True}
        letter = units[0]
    if letter not in units:
        return {"why": f"apartment {letter} not found on this floor (apartments: {', '.join(units)})", "final": True}
    used = habitable(letter)
    others = sorted({r["name"] for r in fl.rooms if r.get("unit") == letter and r["name"] and r not in used})
    names = ", ".join(sorted(r["name"] for r in used))
    return {"rooms": used, "value": len(used),
            "note": f"rooms inside apartment {letter} labelled as living room, bedroom or generic room ({names}); "
                    f"not counted: {', '.join(others) or 'none'}",
            "estimate": "a room count follows a convention: living rooms and bedrooms are counted, kitchen, bathrooms, "
                        "corridor, storage and balconies are not"}


def answer(ctx, q) -> Answer:
    dxf = ctx.sibling("dxf")
    if dxf is None:
        return abstain(ROUTE, "no DXF sibling")
    if not swiss_scheme(str(dxf)):
        if q.extra.get("apartment_scope"):
            return abstain(ROUTE, "this DXF has no apartment outlines, so rooms cannot be assigned to one apartment")
        from . import dxf_generic
        return dxf_generic.answer(ctx, q, dxf)
    fl = load(str(dxf))
    items, value, rooms_used, boxes, note = [], None, [], [], ""
    tier_reason = ""  # set when a count rests on an interpretation (room types inferred, generic rooms left out)
    tku = {}          # the takeoff interpretation the answer is built from (toilet fallback, bpqa/takeoff.py)

    if q.measure == "count":
        table = {"door": [_bbox(p) for p in fl.doors], "window": fl.windows,
                 "toilet": fl.toilets, "washbasin": fl.washbasins, "bathtub": fl.bathtubs, "shower": fl.showers,
                 "sink": fl.washbasins}
        if q.obj in table:
            boxes = table[q.obj] or []
            value = len(boxes)
            note = {"door": "door outlines on layer DOOR", "window": "grouped WINDOW polylines",
                    "toilet": "FIXTURE ellipses without drain", "washbasin": "FIXTURE ellipses with drain circle",
                    "bathtub": "FIXTURE bathtub outlines", "shower": "FIXTURE shower trays",
                    "sink": "FIXTURE ellipses with drain circle (washbasins)"}[q.obj]
            if q.obj == "sink":
                tier_reason = SINK_REASON
            if q.obj == "toilet" and q.extra.get("toilet_ambiguous"):
                note += (" ('toilets' read as WC seats, the Swiss plans' convention for 'toilets (WC)'; "
                         "rooms are not counted)")
        elif q.obj == "apartment":
            value = len(fl.units)
            items = [u["label"] for u in fl.units]
            note = "apartment labels " + ", ".join(items)
        elif q.obj == "room" and q.label:
            rooms_used = [r for r in fl.rooms if r["name"] == q.label]
            value = len(rooms_used)
            note = f"room outlines labelled '{q.label}'"
        elif q.obj == "room" and q.extra.get("apartment_scope"):
            rc = apartment_room_count(fl, q.apartment)
            if rc.get("why"):
                a = abstain(ROUTE, rc["why"])
                a.final = rc.get("final", False)
                return a
            rooms_used, value, note, tier_reason = rc["rooms"], rc["value"], rc["note"], rc["estimate"]
        elif q.obj in takeoff.COUNT_TYPES:
            rt = takeoff.count_room_type(fl.rooms, q.obj, fixtures=lambda r: swiss_room_fixtures(fl, r))
            if rt["value"] is None:
                a = abstain(ROUTE, rt["why"])
                a.final = rt.get("final", False)
                return a
            rooms_used, value, note = rt["rooms"], rt["value"], rt["note"]
            tier_reason = rt.get("estimate", "")
        else:
            return abstain(ROUTE, f"no DXF rule for count of {q.obj}")
    elif q.measure == "area":
        if q.room_no:
            rooms_used = [r for r in fl.rooms if r["number"] == q.room_no]
            if len(rooms_used) != 1:
                return abstain(ROUTE, f"room {q.room_no} not found uniquely")
            note = f"outline of room {q.room_no}"
        elif q.obj == "apartment" and q.apartment:
            rooms_used = [r for r in fl.rooms if r["unit"] == q.apartment and fl.counts_as_indoor(r)]
            if not rooms_used:
                return abstain(ROUTE, f"apartment {q.apartment} not found")
            note = f"indoor rooms inside apartment {q.apartment} outline"
        elif q.variant in ("net_indoor_total", "total"):
            rooms_used = [r for r in fl.rooms if fl.counts_as_indoor(r) and not fl.is_shaft(r)]
            units = sorted({r["unit"] for r in rooms_used if r.get("unit")})
            if q.extra.get("one_apartment") and len(units) >= 2:
                # "the area of the apartment" on a floor of several flats: name one (as for the room count)
                per = ", ".join(f"{u}: {sum(fl.room_area(r) for r in rooms_used if r.get('unit') == u):.2f} m2" for u in units)
                a = abstain(ROUTE, f"this floor has {len(units)} apartments ({', '.join(units)}); name one. Indoor room "
                                   f"area per apartment: {per}")
                a.final = True
                return a
            note = "numbered indoor rooms (named, or unnamed with a fixture inside or wider than 1 m; outdoor, shafts and voids excluded)"
        elif q.variant == "max":
            indoor = [r for r in fl.rooms if fl.counts_as_indoor(r) and not fl.is_shaft(r)]
            if not indoor:
                return abstain(ROUTE, "no indoor room outlines on this floor")
            big = max(indoor, key=fl.room_area)
            rooms_used = [big]
            note = (f"largest of the {len(indoor)} indoor room outlines: {big['name'] or 'unnamed room'} {big['number'] or ''}"
                    + (f" (apartment {big['unit']})" if big.get("unit") else "")).strip()
        elif q.label:
            rooms_used = [r for r in fl.rooms if r["name"] == q.label]
            if q.extra.get("label_from_text"):
                # a name written in the question without quotes ("area of the gym"): any case, and a name not on
                # this floor is not a room of 0 m2
                rooms_used = [r for r in fl.rooms if (r["name"] or "").strip().lower() == q.label.strip().lower()]
                if not rooms_used:
                    named = sorted({r["name"] for r in fl.rooms if r["name"]})
                    return abstain(ROUTE, f"no room named '{q.label}' on this floor (room names: {', '.join(named[:15])})")
            note = f"rooms labelled '{q.label}'"
        elif q.variant == "type_sum":
            tk = q.extra["takeoff"]
            if tk.get("all_rooms"):         # baseboard for every indoor room (bpqa/baseboard.py)
                rooms_used = baseboard.select_rooms([r for r in fl.rooms if fl.counts_as_indoor(r) and not fl.is_shaft(r)], tk)
                generic = []
            else:
                rooms_used, generic, tk = takeoff.toilet_fallback(fl.rooms, tk, has_toilet=swiss_has_toilet(fl))
            tku = tk
            if not rooms_used:
                rooms_used, tk = takeoff.fixture_room_fallback(fl.rooms, tk, lambda r: swiss_room_fixtures(fl, r))
                tku = tk
            if not rooms_used:
                named = sorted({r["name"] for r in fl.rooms if r["name"]})
                gen = takeoff.generic_reason(fl.rooms, tk["types"])
                if gen:
                    a = abstain(ROUTE, gen + f" (room names: {', '.join(named[:15])})")
                    a.final = True
                    return a
                why = (tk.get("toilet_rooms") or {}).get("why")
                return abstain(ROUTE, (why + f" (room names: {', '.join(named[:15])})") if why else
                               f"no room of type {', '.join(tk['types'])} among the {len(named)} room names "
                               f"on this floor ({', '.join(named[:15])})")
            note = takeoff.describe(tk) + ("; no room is labelled as a bathroom, unnamed rooms holding a toilet, bathtub or "
                                           "shower used" if tk.get("fixture_rooms") else "") + (f"; {len(generic)} generic room(s) ({generic[0]['name']}) not typed, not included"
                                           if generic else "")
        else:
            return abstain(ROUTE, "area query not understood")
        why = single_room_problem(q, rooms_used, lambda r: f"{r['name']} {r['number'] or ''}".strip())
        if why:
            return ambiguous_room(ROUTE, why)
        value = round(sum(fl.room_area(r) for r in rooms_used), 2)
    else:
        return abstain(ROUTE, f"measure {q.measure} not supported on DXF")

    if rooms_used:
        items = [{"number": r["number"], "name": r["name"], "area_m2": fl.room_area(r), "printed": r["area_label"],
                  **({"unit": r["unit"]} if r.get("unit") else {})} for r in rooms_used]
    elif boxes:
        items = [{"n": i + 1, "bbox": [round(v, 2) for v in b]} for i, b in enumerate(boxes)]

    # Confidence: printed area labels agree with polygon areas -> high
    conf = 0.95
    printed = [r for r in rooms_used if r["area_label"] is not None]
    if q.obj == "window" and not fl.window_check:
        conf = 0.6
    if printed and any(abs(r["area_label"] - fl.room_area(r)) > 0.02 * r["area_label"] for r in printed):
        conf = 0.7
    unit = "count" if q.measure == "count" else "m2"
    ans = Answer(value=value, unit=unit, route=ROUTE, confidence=conf, items=items,
                 explanation=f"{value} = {note} in {dxf.name}" + (f"; {SWISS_DOOR_CONVENTION}" if q.measure == "count" and q.obj == "door" else ""))
    if tier_reason:
        estimate(ans, tier_reason)
    if q.variant == "type_sum":
        rows = [{"label": r["name"] or "unlabelled room", "number": r["number"], "area": fl.room_area(r), "source": "ROOM_OUTLINE polygon"}
                for r in rooms_used]
        tk = tku or q.extra["takeoff"]
        ans.value, ans.items, expl = takeoff.summary(rows, tk)
        ans.explanation = expl + (" " + note.split("; ", 1)[1] if "generic" in note else "") + f" Source: {dxf.name}."
        fb = takeoff.fallback_reason(tk)
        sr = takeoff.scope_reason(tk, [r["label"] for r in rows])
        if wallmod.surfaces(tk)[1]:
            wrooms = swiss_wall_rooms(fl, rooms_used)
            ans.value, ans.items, wexpl, totals = wallmod.summary(wrooms, tk, ctx.q.question)
            ans.unit = totals.get("unit", ans.unit)
            ans.items.append({"totals": totals})
            ans.explanation = wexpl + f" Source: {dxf.name}."
            ans.tier = "estimate" if not wallmod.parse_inputs(ctx.q.question, tk).get("ceiling_m") and not baseboard.is_baseboard(tk) else None
            ans.tier_reason = "room height, door and window heights are assumptions (a floor plan does not carry them)" if ans.tier else ""
            if fb:
                estimate(ans, fb)
            if sr:
                estimate(ans, sr)
            if ctx.evidence:
                cv = base_canvas(fl)
                if fb:
                    for b in fl.toilets:            # the toilet symbols that confirm the rooms, boxed
                        cv.box(b, n=None, color=TOILET_COL)
                inp = wallmod.parse_inputs(ctx.q.question, tk)
                for r in wrooms:
                    w = wallmod.room_walls(r, inp)
                    for o, d in zip(r["openings"], w["deductions"]):
                        o["deducted"] = d["area"]
                wallmod.draw_rooms(cv, wrooms, PALETTE[0], PALETTE[0], (231, 140, 8),
                                   lambda i, r: wallmod.room_caption(i, r, inp),
                                   label_at=lambda i, r: label_point(rooms_used[i]))
                for i, r in enumerate(rooms_used):
                    draw_room_texts(cv, r)
                    record_steps(cv.img, i + 1, room_steps(cv, fl, r, tk.get("types")))
                out = evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question)
                est = "; ".join(x for x in ("assumed heights" if ans.tier and "room height" in ans.tier_reason else "",
                                             takeoff.fallback_label(tk) if fb else "") if x)
                bb_note = (f"Baseboard = ROOM_OUTLINE (thick blue) minus door openings (amber boxes: DOOR leaf widths, in m); windows "
                           f"do not interrupt it. Surfaces: {totals['surfaces']}.") if baseboard.is_baseboard(tk) else ""
                ans.evidence = finish(cv.img, out, ctx.q.question, wallmod.answer_line(ans.value, totals, ROUTE, est),
                                      note=bb_note or ((takeoff.interpretation(tk, [(f"{r['label']} {r['number'] or ''}".strip(), r["floor_area"]) for r in wrooms]) + " ") if fb else "") + (f"Walls = ROOM_OUTLINE (thick blue) x height - openings (amber: doors from DOOR "
                                            f"leaves, windows from WINDOW strips). {wallmod.room_walls(wrooms[0], inp)['height_src'] if wrooms else ''}. "
                                            f"Door height {wallmod.DOOR_H_M:.2f} m, window {wallmod.WINDOW_H_M:.2f} m on a "
                                            f"{wallmod.WINDOW_SILL_M:.2f} m sill: assumptions. Surfaces: {totals['surfaces']}."))
            return ans
        if fb:
            estimate(ans, fb)
        if sr:
            estimate(ans, sr)

    if ctx.evidence:
        cv = base_canvas(fl)
        for i, r in enumerate(rooms_used):
            col = PALETTE[i % len(PALETTE)]
            ext = r["poly"].exterior.coords
            cv.poly(list(ext), fill=col + (70,), outline=col, width=3)
            lab = f"{r['number'] or ''} {fl.room_area(r):.2f} m²" if q.measure == "area" else f"#{i + 1}"
            if q.variant == "type_sum":
                lab = f"{i + 1}. {r['name'] or 'unlabelled'} {fl.room_area(r):.2f} m²"
            if q.measure == "area":
                draw_room_texts(cv, r)
            cv.text(label_point(r) if q.measure == "area" else tuple(r["poly"].representative_point().coords[0]), lab, size=13, color=(0, 0, 0))
            if q.measure == "area":
                record_steps(cv.img, i + 1, room_steps(cv, fl, r, (tku or q.extra.get("takeoff") or {}).get("types")))
        for i, b in enumerate(boxes):
            cv.box(b, n=i + 1)
        if q.obj == "apartment" and q.measure == "count":
            for i, u in enumerate(fl.units):
                col = PALETTE[i % len(PALETTE)]
                if u["poly"] is not None and hasattr(u["poly"], "exterior"):
                    cv.poly(list(u["poly"].exterior.coords), fill=col + (45,), outline=col, width=3)
                cv.text(u["label_pt"], f"{i + 1}: {u['label']}", size=15, color=col)
        sum_note = ""
        if q.measure == "area" and len(rooms_used) > 1:
            sum_note = " + ".join(f"{fl.room_area(r):.2f}" for r in rooms_used[:25]) + (" + ..." if len(rooms_used) > 25 else "")
        out = evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question)
        if tku.get("toilet_rooms") or tku.get("fixture_rooms"):
            for b in fl.toilets:                    # the toilet symbols that confirm the rooms, boxed
                cv.box(b, n=None, color=TOILET_COL)
            note = takeoff.interpretation(tku, [(f"{r['name'] or 'unlabelled room'} {r['number'] or ''}".strip(), fl.room_area(r))
                                                for r in rooms_used]) + " " + note
        ans.evidence = finish(cv.img, out, ctx.q.question, (takeoff.answer_line(value, len(rooms_used), ROUTE, takeoff.fallback_label(tku))
                                                            if q.variant == "type_sum" else f"Answer: {value} {unit}   route: {ROUTE} (cost €0)"),
                              note=(note + (": " + sum_note if sum_note else "")))
    return ans
