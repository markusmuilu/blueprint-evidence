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
from ..answer import Answer, abstain
from ..evidence import PALETTE, VectorCanvas, evidence_path, finish
from .scope_guard import single_room_problem

ROUTE = "dxf-geometry"
NUM_RE = re.compile(r"^[A-Z]?\d+\.\d+$")
AREA_RE = re.compile(r"^([\d.]+)\s*m")
UNIT_RE = re.compile(r"^(Whg|App|Apt|Wohnung|Appartamento|Appartement)\.?\s+([A-Z]{1,2})$")
OUTDOOR = {"balkon", "balcon", "balcone", "loggia", "terrasse", "terrazza", "sitzplatz", "dachterrasse",
           "veranda", "aussenraum"}
SHAFT_VOID = {"schacht", "luftraum", "gaine", "vide", "cavedio", "vuoto", "shaft", "void",
              }


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
        self.texts = texts
        allb = [_bbox(p) for _, p in self.lines if len(p) > 1]
        self.bounds = (min(b[0] for b in allb), min(b[1] for b in allb), max(b[2] for b in allb), max(b[3] for b in allb))

        # Rooms: attach number / name / printed area to the outline that contains them
        rooms = [{"poly": p, "name": None, "number": None, "area_label": None, "unit": None} for p in room_polys]
        for layer, s, xy in texts:
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
            elif (m := AREA_RE.match(s)):
                r["area_label"] = float(m.group(1))
            elif r["name"] is None:
                r["name"] = s
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
        """Numbered indoor room. Rooms printed with a number but no name are ambiguous
        (wet rooms, shafts or balconies); keep them only if they hold a sanitary fixture."""
        if not r["number"] or self.is_outdoor(r):
            return False
        if r["name"]:
            return True
        return any(r["poly"].contains(Point(c)) for c in self.fixture_centroids)


def swiss_wall_rooms(fl, rooms_used):
    """Room records for bpqa/walls.py from Swiss DXF rooms. A floor plan has no heights, so the ceiling
    height is the stated default (walls.py) and door / window heights are stated assumptions. Openings:
    door leaves (DOOR, width = leaf length) within 0.35 m of the room outline (a door counts for the rooms
    on both sides of its wall); windows (grouped WINDOW strips) within 0.5 m along at least 30% of their
    width."""
    from shapely.geometry import Polygon as _P, box as _box
    out = []
    for r in rooms_used:
        poly = r["poly"]
        ops = []
        for d in fl.doors:
            leaf = _P(d).buffer(0)
            if leaf.is_empty or leaf.distance(poly) > 0.35:
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
        out.append({"label": r["name"], "number": r["number"], "floor_area": fl.room_area(r),
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


def answer(ctx, q) -> Answer:
    dxf = ctx.sibling("dxf")
    if dxf is None:
        return abstain(ROUTE, "no DXF sibling")
    if not swiss_scheme(str(dxf)):
        from . import dxf_generic
        return dxf_generic.answer(ctx, q, dxf)
    fl = load(str(dxf))
    items, value, rooms_used, boxes, note = [], None, [], [], ""

    if q.measure == "count":
        table = {"door": [_bbox(p) for p in fl.doors], "window": fl.windows,
                 "toilet": fl.toilets, "washbasin": fl.washbasins, "bathtub": fl.bathtubs, "shower": fl.showers}
        if q.obj in table:
            boxes = table[q.obj] or []
            value = len(boxes)
            note = {"door": "door leaves on layer DOOR", "window": "grouped WINDOW polylines",
                    "toilet": "FIXTURE ellipses without drain", "washbasin": "FIXTURE ellipses with drain circle",
                    "bathtub": "FIXTURE bathtub outlines", "shower": "FIXTURE shower trays"}[q.obj]
        elif q.obj == "apartment":
            value = len(fl.units)
            items = [u["label"] for u in fl.units]
            note = "apartment labels " + ", ".join(items)
        elif q.obj == "room" and q.label:
            rooms_used = [r for r in fl.rooms if r["name"] == q.label]
            value = len(rooms_used)
            note = f"room outlines labelled '{q.label}'"
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
        elif q.variant == "net_indoor_total":
            rooms_used = [r for r in fl.rooms if fl.counts_as_indoor(r) and not fl.is_shaft(r)]
            note = "numbered named indoor rooms (outdoor, shafts and voids excluded)"
        elif q.label:
            rooms_used = [r for r in fl.rooms if r["name"] == q.label]
            note = f"rooms labelled '{q.label}'"
        elif q.variant == "type_sum":
            tk = q.extra["takeoff"]
            rooms_used, generic = takeoff.select(fl.rooms, tk)
            if not rooms_used:
                named = sorted({r["name"] for r in fl.rooms if r["name"]})
                return abstain(ROUTE, f"no room of type {', '.join(tk['types'])} among the {len(named)} room names "
                                      f"on this floor ({', '.join(named[:15])})")
            note = takeoff.describe(tk) + (f"; {len(generic)} generic room(s) ({generic[0]['name']}) not typed, not included"
                                           if generic else "")
        else:
            return abstain(ROUTE, "area query not understood")
        why = single_room_problem(q, rooms_used, lambda r: f"{r['name']} {r['number'] or ''}".strip())
        if why:
            return abstain(ROUTE, why)
        value = round(sum(fl.room_area(r) for r in rooms_used), 2)
    else:
        return abstain(ROUTE, f"measure {q.measure} not supported on DXF")

    if rooms_used:
        items = [{"number": r["number"], "name": r["name"], "area_m2": fl.room_area(r), "printed": r["area_label"]} for r in rooms_used]
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
                 explanation=f"{value} = {note} in {dxf.name}")
    if q.variant == "type_sum":
        rows = [{"label": r["name"], "number": r["number"], "area": fl.room_area(r), "source": "ROOM_OUTLINE polygon"}
                for r in rooms_used]
        ans.value, ans.items, expl = takeoff.summary(rows, q.extra["takeoff"])
        ans.explanation = expl + (" " + note.split("; ", 1)[1] if "generic" in note else "") + f" Source: {dxf.name}."
        tk = q.extra["takeoff"]
        if wallmod.surfaces(tk)[1]:
            wrooms = swiss_wall_rooms(fl, rooms_used)
            ans.value, ans.items, wexpl, totals = wallmod.summary(wrooms, tk, ctx.q.question)
            ans.items.append({"totals": totals})
            ans.explanation = wexpl + f" Source: {dxf.name}."
            ans.tier = "estimate" if not wallmod.parse_inputs(ctx.q.question).get("ceiling_m") else None
            ans.tier_reason = "room height, door and window heights are assumptions (a floor plan does not carry them)" if ans.tier else ""
            if ctx.evidence:
                cv = base_canvas(fl)
                inp = wallmod.parse_inputs(ctx.q.question)
                for r in wrooms:
                    w = wallmod.room_walls(r, inp)
                    for o, d in zip(r["openings"], w["deductions"]):
                        o["deducted"] = d["area"]
                wallmod.draw_rooms(cv, wrooms, PALETTE[0], PALETTE[0], (231, 140, 8),
                                   lambda i, r: f"{i + 1}. {r['label']}: floor {r['floor_area']:.2f}, walls "
                                                f"{wallmod.room_walls(r, inp)['net']:.2f} m²")
                out = evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question)
                ans.evidence = finish(cv.img, out, ctx.q.question, wallmod.answer_line(ans.value, totals, ROUTE, "assumed heights" if ans.tier else ""),
                                      note=(f"Walls = ROOM_OUTLINE (thick blue) x height - openings (amber: doors from DOOR "
                                            f"leaves, windows from WINDOW strips). {wallmod.room_walls(wrooms[0], inp)['height_src'] if wrooms else ''}. "
                                            f"Door height {wallmod.DOOR_H_M:.2f} m, window {wallmod.WINDOW_H_M:.2f} m on a "
                                            f"{wallmod.WINDOW_SILL_M:.2f} m sill: assumptions. Surfaces: {totals['surfaces']}."))
            return ans

    if ctx.evidence:
        cv = base_canvas(fl)
        for i, r in enumerate(rooms_used):
            col = PALETTE[i % len(PALETTE)]
            ext = r["poly"].exterior.coords
            cv.poly(list(ext), fill=col + (70,), outline=col, width=3)
            rp = r["poly"].representative_point()
            lab = f"{r['number'] or ''} {fl.room_area(r):.2f} m²" if q.measure == "area" else f"#{i + 1}"
            if q.variant == "type_sum":
                lab = f"{i + 1}. {r['name']} {fl.room_area(r):.2f} m²"
            cv.text((rp.x, rp.y), lab, size=13, color=(0, 0, 0))
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
        ans.evidence = finish(cv.img, out, ctx.q.question, (takeoff.answer_line(value, len(rooms_used), ROUTE) if q.variant == "type_sum" else f"Answer: {value} {unit}   route: {ROUTE} (cost $0)"),
                              note=(note + (": " + sum_note if sum_note else "")))
    return ans
