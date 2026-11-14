"""Generic DXF route: any layer or block naming scheme, not only the Swiss one.

The Swiss Dwellings DXFs use one fixed convention (ROOM_OUTLINE, ROOM_TEXT, DOOR as 5-vertex leaf
polylines ...). Other offices name layers by AIA/NCS (A-DOOR, A-GLAZ, A-WALL, A-AREA-IDEN, P-FIXT),
by ISO 13567 fields with CI/SfB element codes, or with plain words in their own language (Tueren,
Fenster, Porte, Ovet, Dorrar). This module reads what a file actually contains and answers only
when the asked object class is present in a form it can count:

  1. classify every layer and block name into object classes (door, window, wall, toilet, sink,
     bathtub, shower, sanitary, room, column, furniture, annotation) from name tokens;
  2. if no layer, block or entity type of the asked class exists, abstain and say so. A count of 0
     is never returned: in a file whose scheme we do not know, "found none" is not proof of none;
  3. count with conventions that hold across offices:
       doors     block INSERTs of door blocks (or on door layers), else swing arcs on door layers
                 (an arc of 30-180 degrees, radius 0.4-2 m); two swings hinged at the two ends of
                 one opening are one double door
       windows   window block INSERTs, else window-layer pieces grouped into one window when they
                 are parallel, overlap along the wall and lie within a wall thickness of each other
                 (a window drawn as 2 or 3 glazing lines is one window)
       fixtures  INSERTs of blocks named like the fixture (WC, toilet, lavabo, sink, bath, shower ...)
       columns   closed shapes and circles on column layers, overlapping ones merged
       rooms     closed polylines on room/area layers with the room's text label inside; without
                 room outlines a label count is the number of distinct text labels equal to it
  4. drawing units from $INSUNITS, else from the door-swing radius; areas need known units.

Nothing here is tied to one file: no layer name is matched literally except through the word lists.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path

import ezdxf
from shapely.geometry import Point, Polygon

from .. import takeoff
from ..answer import Answer, abstain
from ..evidence import PALETTE, VectorCanvas, evidence_path, finish
from ..query import fold
from .scope_guard import single_room_problem

ROUTE = "dxf-generic"

# ---------------------------------------------------------------- name vocabulary
# Tokens are matched on the folded name (lower case, accents stripped), split at non-letters and
# at camelCase. A word of 4+ letters matches a token that starts with it ("fenster" in
# "fensterbank" is avoided by the exclusion list below); shorter words must equal the token.
WORDS = {
    "door": ["door", "doors", "dr", "tur", "tuer", "ture", "tuere", "tueren", "turen", "porte", "portes", "porta", "porte",
             "ovi", "ovet", "oven", "dorr", "dorrar", "deur", "deuren", "puerta", "puertas", "dor", "dore",
             "drzwi", "uks", "uksed", "turblatt"],
    "window": ["window", "windows", "wind", "glaz", "glazing", "fenster", "fenetre", "fenetres", "finestra",
               "finestre", "ikkuna", "ikkunat", "fonster", "raam", "ramen", "ventana", "ventanas", "vindu",
               "vinduer", "aken", "akna", "vitrage"],
    "wall": ["wall", "walls", "wand", "waende", "wande", "mur", "murs", "muro", "muri", "seina", "seinat",
             "vagg", "vaggar", "muur", "pared", "vaeg"],
    "toilet": ["wc", "wcs", "toilet", "toilets", "toilette", "klosett", "closet", "klo", "gabinetto", "vaso",
               "istuin", "toalett", "toilette", "watercloset", "bidet"],
    "sink": ["sink", "sinks", "basin", "lavabo", "lavabi", "lavatory", "waschtisch", "waschbecken",
             "lavandino", "lavello", "evier", "allas", "pesuallas", "tiskiallas", "handfat", "tvattstall",
             "wastafel", "spulbecken", "spuele", "lav"],
    "bathtub": ["bathtub", "tub", "bath", "badewanne", "wanne", "baignoire", "vasca", "amme", "kylpyamme",
                "badkar", "ligbad"],
    "shower": ["shower", "showers", "dusche", "douche", "doccia", "suihku", "dusch", "ducha"],
    "sanitary": ["fixt", "fixture", "fixtures", "pfix", "sanr", "sanitary", "sanit", "plumb", "plumbing",
                 "sanitaer", "sanitar", "sanitaire", "sanitari", "saniteetti", "sanitet", "plmb"],
    "room": ["room", "rooms", "area", "areas", "raum", "raeume", "raume", "piece", "pieces", "locale",
             "locali", "stanza", "huone", "huoneet", "tila", "tilat", "rum", "space", "spaces", "ruimte",
             "zone", "raumstempel", "rstempel", "roomname", "roomnames"],
    "column": ["column", "columns", "cols", "col", "stutze", "stuetze", "stuetzen", "poteau", "pilastro",
               "pilar", "pilari", "pelare", "kolom", "saule"],
    "furniture": ["furn", "furniture", "mobel", "moebel", "mobilier", "arredo", "arredi", "kaluste",
                  "kalusteet", "mobler", "eqpm", "equipment"],
    "annotation": ["anno", "text", "note", "notes", "dims", "dim", "dimension", "grid", "axis", "achse",
                   "title", "titl", "titel", "frame", "rahmen", "border", "sheet", "legend", "symb", "patt",
                   "hatch", "hatc", "npl", "nplt", "defpoints", "viewport", "vport", "bema", "bemassung",
                   "cotation", "cote", "quote", "mitat", "elev", "sect", "revs", "tag", "tags"],
}
# modifiers that turn a class layer into an annotation of that class (A-DOOR-IDEN = door tags)
MODIFIERS = {"iden", "text", "anno", "note", "dims", "patt", "hatch", "hatc", "tag", "tags", "nplt", "symb", "elev"}
# CI/SfB element codes in ISO 13567 / BS 1192 layer names ("A-22-WALL", "A--32---"), per element field
SFB = {"21": "wall", "22": "wall", "31": "window", "32": "door", "28": "column", "74": "sanitary"}
FIXTURE_CLASSES = ("toilet", "sink", "bathtub", "shower")
# question object -> the classes that can answer it
OBJ_CLASSES = {
    "door": ("door",), "window": ("window",), "toilet": ("toilet",), "washbasin": ("sink",), "sink": ("sink",),
    "bathtub": ("bathtub",), "shower": ("shower",), "shower_or_bath": ("shower", "bathtub"), "column": ("column",),
}
ROOM_TYPE_OBJ = {"bedroom": "bedroom", "kitchen": "kitchen", "bathroom": "bathroom", "wc_room": "wc",
                 "sauna": "sauna", "balcony": "balcony"}
UNITS_M = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0, 14: 0.1, 10: 0.9144}


def tokens(name: str) -> list[str]:
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", name or "")
    return [t for t in re.split(r"[^a-z0-9]+", fold(s)) if t]


def name_classes(name: str, sfb: bool = True) -> set[str]:
    """Object classes a layer or block name names. 'A-DOOR' -> {door}; 'A-DOOR-IDEN' -> {annotation}."""
    toks = tokens(name)
    out = set()
    for t in toks:
        for cls, words in WORDS.items():
            for w in words:
                if t == w or (len(w) >= 4 and t.startswith(w)):
                    out.add(cls)
                    break
        if sfb and t.isdigit() and len(t) == 2 and t in SFB and len(toks) > 1:
            out.add(SFB[t])
    # ISO 13567 element field written without separators ("A--31---E"): digits after the agent field
    m = re.match(r"^[a-z]-{0,2}(\d{2})", fold(name or ""))
    if sfb and m and m.group(1) in SFB:
        out.add(SFB[m.group(1)])
    if out - {"annotation"} and any(t in MODIFIERS for t in toks[1:]):
        # "A-DOOR-IDEN": door tags, not doors; "A-AREA-IDEN" stays a room-name layer
        out = ({"room"} if "room" in out else set()) | {"annotation"}
    if "furniture" in out:
        out -= {"toilet", "sink", "bathtub", "shower"} if not (out & {"sanitary"}) else set()
    return out


def _fold_text(s: str) -> str:
    return re.sub(r"\s+", " ", fold(s or "")).strip()


def label_eq(text: str, label: str) -> bool:
    a, b = _fold_text(text), _fold_text(label)
    return a == b or a.replace(" ", "") == b.replace(" ", "")


def text_room_types(text: str) -> set[str]:
    """Room types of a label, also trying it with spaces removed ('BED ROOM' -> bedroom)."""
    t = takeoff.room_types(text)
    if not (t - {"room"}):
        t2 = takeoff.room_types(re.sub(r"\s+", "", text or ""))
        if t2 - {"room"}:
            return t2
    return t


# ---------------------------------------------------------------- geometry helpers
def _bbox(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _arc_pts(c, r, a0, a1, n=12):
    if a1 < a0:
        a1 += 360
    return [(c[0] + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             c[1] + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


class GenericFloor:
    """What one DXF contains, by object class, whatever its naming scheme."""

    def __init__(self, path: Path):
        doc = ezdxf.readfile(str(path))
        msp = doc.modelspace()
        self.layer_names = [l.dxf.name for l in doc.layers]
        self.layer_cls = {n: name_classes(n) for n in self.layer_names}
        self.block_cls = {b.name: name_classes(b.name) for b in doc.blocks
                          if not b.name.startswith("*") and not b.is_any_layout}
        self.insunits = int(doc.header.get("$INSUNITS", 0) or 0)
        self.lines = []          # (layer, pts) for the base canvas
        self.circles = []        # (layer, centre, r)
        self.arcs = []           # dicts: layer, c, r, a0, a1, span
        self.inserts = []        # dicts: layer, block, pt, bbox
        self.closed = []         # (layer, pts) closed polylines
        self.pieces = {}         # layer -> list of point lists (lines and polylines)
        self.texts = []          # (layer, text, (x, y))
        self.entity_layers = {}
        for e in msp:
            self._add(e)
        allb = [_bbox(p) for _, p in self.lines if len(p) > 1]
        allb += [(c[0] - r, c[1] - r, c[0] + r, c[1] + r) for _, c, r in self.circles]
        allb += [i["bbox"] for i in self.inserts if i["bbox"]]
        self.bounds = ((min(b[0] for b in allb), min(b[1] for b in allb), max(b[2] for b in allb),
                        max(b[3] for b in allb)) if allb else (0, 0, 1, 1))
        self.m_per_unit, self.units_source = self._units()

    def _add(self, e):
        t, layer = e.dxftype(), e.dxf.get("layer", "0")
        self.entity_layers[layer] = self.entity_layers.get(layer, 0) + 1
        try:
            if t == "LINE":
                p = [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
                self.lines.append((layer, p))
                self.pieces.setdefault(layer, []).append(p)
            elif t in ("LWPOLYLINE", "POLYLINE"):
                if t == "LWPOLYLINE":
                    raw = list(e.get_points("xyb"))
                    pts = [(x, y) for x, y, _ in raw]
                    bulge = any(abs(b) > 1e-6 for _, _, b in raw)
                    closed = e.closed
                else:
                    pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                    bulge = any(abs(v.dxf.get("bulge", 0)) > 1e-6 for v in e.vertices)
                    closed = e.is_closed
                if bulge:   # a swing drawn as a polyline arc: flatten it and keep it as an arc candidate
                    from ezdxf import path as epath
                    fl = [(v.x, v.y) for v in epath.make_path(e).flattening(0.01 * max(1e-9, _diag(pts)))]
                    self.lines.append((layer, fl))
                    arc = _fit_arc(fl)
                    if arc is not None and len(pts) <= 3:
                        self.arcs.append(dict(layer=layer, **arc))
                    else:
                        self.pieces.setdefault(layer, []).append(pts)
                    return
                if len(pts) >= 2:
                    if closed or (len(pts) > 3 and math.dist(pts[0], pts[-1]) < 1e-6 * max(1.0, _diag(pts))):
                        self.closed.append((layer, pts))
                        self.lines.append((layer, pts + pts[:1]))
                    else:
                        self.lines.append((layer, pts))
                    self.pieces.setdefault(layer, []).append(pts)
            elif t == "ARC":
                c = (e.dxf.center.x, e.dxf.center.y)
                a0, a1 = e.dxf.start_angle % 360, e.dxf.end_angle % 360
                span = (a1 - a0) % 360 or 360
                self.arcs.append(dict(layer=layer, c=c, r=e.dxf.radius, a0=a0, a1=a1, span=span))
                self.lines.append((layer, _arc_pts(c, e.dxf.radius, a0, a0 + span)))
            elif t == "CIRCLE":
                self.circles.append((layer, (e.dxf.center.x, e.dxf.center.y), e.dxf.radius))
            elif t == "TEXT":
                use_align = e.dxf.get("halign", 0) or e.dxf.get("valign", 0)
                p = e.dxf.align_point if use_align else e.dxf.insert
                self.texts.append((layer, e.dxf.text.strip(), (p.x, p.y)))
            elif t == "MTEXT":
                p = e.dxf.insert
                for k, line in enumerate(e.plain_text().split("\n")):
                    if line.strip():
                        self.texts.append((layer, line.strip(), (p.x, p.y - k * e.dxf.get("char_height", 0))))
            elif t == "INSERT":
                bb = None
                try:
                    from ezdxf import bbox as ebbox
                    ext = ebbox.extents([e], fast=True)
                    if ext.has_data:
                        bb = (ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)
                except Exception:
                    bb = None
                ip = (e.dxf.insert.x, e.dxf.insert.y)
                self.inserts.append(dict(layer=layer, block=e.dxf.name, pt=ip, bbox=bb or (ip[0], ip[1], ip[0], ip[1])))
                for a in e.attribs:
                    if a.dxf.text.strip():
                        self.texts.append((a.dxf.get("layer", layer), a.dxf.text.strip(), (a.dxf.insert.x, a.dxf.insert.y)))
                if bb:
                    x0, y0, x1, y1 = bb
                    self.lines.append((layer, [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]))
            elif t == "HATCH":
                pass
        except Exception:      # one odd entity must not take the file down
            return

    # -- scheme ---------------------------------------------------------------------------
    def layers_of(self, cls: str) -> list[str]:
        return [n for n, c in self.layer_cls.items() if cls in c and "annotation" not in c]

    def blocks_of(self, cls: str) -> set[str]:
        return {n for n, c in self.block_cls.items() if cls in c and "annotation" not in c}

    def evidence_for(self, cls: str) -> dict:
        """Where objects of a class could be in this file: layers (with entity counts) and blocks."""
        lays = {n: self.entity_layers.get(n, 0) for n in self.layers_of(cls)}
        blks = self.blocks_of(cls)
        ins = [i for i in self.inserts if i["block"] in blks]
        return {"layers": lays, "blocks": sorted(blks), "inserts": len(ins)}

    def _units(self):
        """Metres per drawing unit. $INSUNITS is used unless the door swings contradict it (many
        files carry the template default): a door leaf is 0.55-1.25 m wide in every convention."""
        door_layers = set(self.layers_of("door"))
        radii = sorted(a["r"] for a in self.arcs if a["layer"] in door_layers and 30 <= a["span"] <= 180)
        r = radii[len(radii) // 2] if radii else None
        hdr = UNITS_M.get(self.insunits)
        if hdr is not None and (r is None or 0.4 <= r * hdr <= 2.0):
            return hdr, f"$INSUNITS={self.insunits}"
        if r is not None:
            for mpu, nm in ((0.001, "mm"), (0.01, "cm"), (1.0, "m"), (0.0254, "inch"), (0.3048, "ft")):
                if 0.55 <= r * mpu <= 1.25:
                    why = f"door swing radius {r:g} read as {nm}"
                    if hdr is not None:
                        why += f" ($INSUNITS={self.insunits} would make it {r * hdr:g} m)"
                    return mpu, why
        if hdr is not None:
            return None, f"$INSUNITS={self.insunits} contradicts the door swing radius {r:g}"
        return None, "units unknown ($INSUNITS unset, no door swings)"

    def m(self, metres: float) -> float | None:
        """metres -> drawing units (None if units unknown)."""
        return None if self.m_per_unit is None else metres / self.m_per_unit

    # -- objects --------------------------------------------------------------------------
    def doors(self):
        """(boxes, how) or (None, reason)."""
        lays = set(self.layers_of("door"))
        blks = self.blocks_of("door")
        ins = [i for i in self.inserts if i["block"] in blks or (i["layer"] in lays and not
               (self.block_cls.get(i["block"], set()) - {"door"}))]
        if ins:
            return [i["bbox"] for i in ins], f"{len(ins)} door block insert(s) ({', '.join(sorted({i['block'] for i in ins})[:5])})"
        arcs = [a for a in self.arcs if a["layer"] in lays and 30 <= a["span"] <= 180]
        if self.m_per_unit is not None:
            arcs = [a for a in arcs if 0.4 <= a["r"] * self.m_per_unit <= 2.0]
        if not arcs:
            if not lays and not blks:
                return None, "no door layer, door block or door entity in this DXF"
            n = sum(self.entity_layers.get(l, 0) for l in lays)
            return None, (f"door layer(s) {', '.join(sorted(lays))} hold {n} entities but no swing arcs or door "
                          "blocks; door convention not recognised")
        # double doors: two swings whose hinges are the two ends of one opening (hinge distance about
        # r1 + r2) and whose free ends meet in the middle
        used, boxes, n_double = set(), [], 0
        for i, a in enumerate(arcs):
            if i in used:
                continue
            ea = _arc_ends(a)
            pair = None
            for j in range(i + 1, len(arcs)):
                if j in used:
                    continue
                b = arcs[j]
                d = math.dist(a["c"], b["c"])
                if abs(a["r"] - b["r"]) > 0.25 * max(a["r"], b["r"]) or abs(d - (a["r"] + b["r"])) > 0.1 * d:
                    continue
                eb = _arc_ends(b)
                if min(math.dist(p, q) for p in ea for q in eb) < 0.1 * (a["r"] + b["r"]):
                    pair = j
                    break
            pts = _arc_pts(a["c"], a["r"], a["a0"], a["a0"] + a["span"]) + [a["c"]]
            if pair is not None:
                used.add(pair)
                b = arcs[pair]
                pts += _arc_pts(b["c"], b["r"], b["a0"], b["a0"] + b["span"]) + [b["c"]]
                n_double += 1
            used.add(i)
            boxes.append(_bbox(pts))
        how = f"{len(arcs)} door swing arc(s) on {', '.join(sorted({a['layer'] for a in arcs}))}"
        if n_double:
            how += f", {n_double} pair(s) hinged at both ends of one opening counted as double doors"
        return boxes, how

    def windows(self):
        lays = set(self.layers_of("window"))
        blks = self.blocks_of("window")
        ins = [i for i in self.inserts if i["block"] in blks or (i["layer"] in lays and not
               (self.block_cls.get(i["block"], set()) - {"window"}))]
        if ins:
            return [i["bbox"] for i in ins], f"{len(ins)} window block insert(s) ({', '.join(sorted({i['block'] for i in ins})[:5])})"
        pieces = [p for l in lays for p in self.pieces.get(l, [])]
        if not pieces:
            if not lays and not blks:
                return None, "no window layer, window block or window entity in this DXF"
            return None, f"window layer(s) {', '.join(sorted(lays))} hold no lines or polylines; convention not recognised"
        minlen = self.m(0.3) or 0.0
        wall = self.m(0.6)
        segs = []
        for p in pieces:
            for s in _strips(p):
                if s["len"] >= minlen:
                    segs.append(s)
        if not segs:
            return None, "window layer pieces are all too short to be windows"
        if wall is None:   # units unknown: a wall thickness of half the median piece length
            wall = 0.5 * sorted(s["len"] for s in segs)[len(segs) // 2]
        groups = _group_parallel(segs, wall)
        boxes = [_bbox([q for s in g for q in s["pts"]]) for g in groups]
        sizes = sorted({len(g) for g in groups})
        return boxes, (f"{len(segs)} window-layer piece(s) on {', '.join(sorted(lays))} grouped into {len(groups)} "
                       f"window(s) (parallel, overlapping along the wall, within a wall thickness; "
                       f"{'/'.join(map(str, sizes))} piece(s) per window)")

    def fixtures(self, classes):
        names = set().union(*(self.blocks_of(c) for c in classes))
        lay_named = set().union(*(set(self.layers_of(c)) for c in classes))
        ins = [i for i in self.inserts if i["block"] in names or (i["layer"] in lay_named and not
               (self.block_cls.get(i["block"], set()) - set(classes) - {"sanitary"}))]
        if ins:
            return [i["bbox"] for i in ins], f"{len(ins)} block insert(s) named as {'/'.join(classes)} ({', '.join(sorted({i['block'] for i in ins})[:5])})"
        san = self.layers_of("sanitary")
        why = f"no block or layer named as {'/'.join(classes)}"
        if san:
            why += f"; sanitary layer(s) {', '.join(san[:4])} exist but their shapes are not typed, so they cannot be told apart"
        return None, why

    def columns(self):
        lays = set(self.layers_of("column"))
        shapes = [Polygon(p).buffer(0) for l, p in self.closed if l in lays and len(p) >= 3]
        shapes += [Point(c).buffer(r) for l, c, r in self.circles if l in lays]
        shapes += [Polygon([(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])]) for b in
                   (i["bbox"] for i in self.inserts if i["layer"] in lays or i["block"] in self.blocks_of("column"))
                   if b[2] > b[0] and b[3] > b[1]]
        shapes = [s for s in shapes if not s.is_empty]
        if not shapes:
            return None, ("no column layer or column block in this DXF" if not lays else
                          f"column layer(s) {', '.join(sorted(lays))} hold no closed shapes")
        merged = _merge(shapes)
        return [g.bounds for g in merged], f"{len(shapes)} closed shape(s) on {', '.join(sorted(lays))}, {len(merged)} after merging overlaps"

    # -- rooms ----------------------------------------------------------------------------
    def label_texts(self):
        """Texts that can be room labels: not on dimension, grid or title layers."""
        out = []
        for layer, s, xy in self.texts:
            c = self.layer_cls.get(layer, set())
            if "annotation" in c and "room" not in c and not _is_plain_anno(layer):
                continue
            if re.fullmatch(r"[\d\s.,:+\-=/x]*", s):    # dimensions, levels, grid numbers
                continue
            out.append((layer, s, xy))
        return out

    def room_polys(self):
        lays = set(self.layers_of("room"))
        polys = []
        for l, p in self.closed:
            if l in lays and len(p) >= 3:
                g = Polygon(p).buffer(0)
                if not g.is_empty and g.area > 0:
                    polys.append(g if g.geom_type == "Polygon" else max(g.geoms, key=lambda x: x.area))
        return polys

    def rooms(self):
        """Room outlines with their labels attached (smallest outline containing the text)."""
        polys = self.room_polys()
        rooms = [{"poly": p, "texts": [], "name": None} for p in polys]
        for layer, s, xy in self.label_texts():
            pt = Point(xy)
            cands = [r for r in rooms if r["poly"].contains(pt)]
            if not cands:
                continue
            r = min(cands, key=lambda r: r["poly"].area)
            r["texts"].append(s)
            if r["name"] is None and not re.search(r"\d+(\.\d+)?\s*m", s.lower()):
                r["name"] = s
        return rooms


def _is_plain_anno(layer: str) -> bool:
    """A general annotation layer (ANNO, TEXT, A-ANNO-TEXT) can hold room names; dims, grids and title
    blocks cannot."""
    toks = set(tokens(layer))
    bad = {"dims", "dim", "dimension", "grid", "axis", "achse", "title", "titl", "titel", "frame", "rahmen",
           "border", "sheet", "legend", "bema", "bemassung", "cotation", "cote", "mitat", "revs", "defpoints",
           "viewport", "vport", "elev", "sect", "patt", "hatch", "hatc", "tag", "tags"}
    return not (toks & bad)


def _diag(pts):
    b = _bbox(pts)
    return math.hypot(b[2] - b[0], b[3] - b[1])


def _fit_arc(pts):
    """Circle through first, middle, last point of a flattened polyline arc."""
    if len(pts) < 3:
        return None
    (x1, y1), (x2, y2), (x3, y3) = pts[0], pts[len(pts) // 2], pts[-1]
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return None
    ux = ((x1 ** 2 + y1 ** 2) * (y2 - y3) + (x2 ** 2 + y2 ** 2) * (y3 - y1) + (x3 ** 2 + y3 ** 2) * (y1 - y2)) / d
    uy = ((x1 ** 2 + y1 ** 2) * (x3 - x2) + (x2 ** 2 + y2 ** 2) * (x1 - x3) + (x3 ** 2 + y3 ** 2) * (x2 - x1)) / d
    r = math.dist((ux, uy), (x1, y1))
    a0 = math.degrees(math.atan2(y1 - uy, x1 - ux)) % 360
    am = math.degrees(math.atan2(y2 - uy, x2 - ux)) % 360
    a1 = math.degrees(math.atan2(y3 - uy, x3 - ux)) % 360
    span = (a1 - a0) % 360
    if not ((am - a0) % 360 < span):      # went the other way round
        a0, a1 = a1, a0
        span = (a1 - a0) % 360
    return {"c": (ux, uy), "r": r, "a0": a0, "a1": a1, "span": span}


def _arc_ends(a):
    return [(a["c"][0] + a["r"] * math.cos(math.radians(t)), a["c"][1] + a["r"] * math.sin(math.radians(t)))
            for t in (a["a0"], a["a0"] + a["span"])]


def _strips(pts):
    """A window piece as straight strips: each segment of a line or open polyline; a closed
    rectangle-like polyline as its long centre line."""
    out = []
    closed = len(pts) >= 4 and math.dist(pts[0], pts[-1]) < 1e-9 * max(1.0, _diag(pts))
    if len(pts) >= 3 and (closed or len(pts) in (4, 5)):
        poly = Polygon(pts).buffer(0)
        if not poly.is_empty and poly.area > 0:
            mrr = list(poly.minimum_rotated_rectangle.exterior.coords)[:4]
            a, b, c = mrr[0], mrr[1], mrr[2]
            if math.dist(a, b) >= math.dist(b, c):
                p0, p1 = ((a[0] + mrr[3][0]) / 2, (a[1] + mrr[3][1]) / 2), ((b[0] + c[0]) / 2, (b[1] + c[1]) / 2)
            else:
                p0, p1 = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), ((c[0] + mrr[3][0]) / 2, (c[1] + mrr[3][1]) / 2)
            return [_strip(p0, p1, pts)]
    for p0, p1 in zip(pts, pts[1:]):
        if math.dist(p0, p1) > 0:
            out.append(_strip(p0, p1, [p0, p1]))
    return out


def _strip(p0, p1, pts):
    ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0]) % math.pi
    return {"p0": p0, "p1": p1, "ang": ang, "len": math.dist(p0, p1), "pts": pts}


def _group_parallel(segs, wall):
    """Union-find: strips join when parallel (within 3 degrees), overlapping >= 60% of the shorter one
    along their direction, and less than a wall thickness apart across it."""
    parent = list(range(len(segs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(segs):
        ux, uy = math.cos(a["ang"]), math.sin(a["ang"])
        ai = sorted((a["p0"][0] * ux + a["p0"][1] * uy, a["p1"][0] * ux + a["p1"][1] * uy))
        ca = (-a["p0"][0] * uy + a["p0"][1] * ux)
        for j in range(i + 1, len(segs)):
            b = segs[j]
            dang = abs(a["ang"] - b["ang"])
            if min(dang, math.pi - dang) > math.radians(3):
                continue
            bi = sorted((b["p0"][0] * ux + b["p0"][1] * uy, b["p1"][0] * ux + b["p1"][1] * uy))
            ov = min(ai[1], bi[1]) - max(ai[0], bi[0])
            if ov < 0.6 * min(ai[1] - ai[0], bi[1] - bi[0]):
                continue
            cb = (-((b["p0"][0] + b["p1"][0]) / 2) * uy + ((b["p0"][1] + b["p1"][1]) / 2) * ux)
            if abs(cb - ca) > wall:
                continue
            parent[find(i)] = find(j)
    out = {}
    for i, s in enumerate(segs):
        out.setdefault(find(i), []).append(s)
    return list(out.values())


def _merge(shapes):
    from shapely.ops import unary_union
    u = unary_union(shapes)
    return list(u.geoms) if hasattr(u, "geoms") else [u]


@lru_cache(maxsize=8)
def load(path: str) -> GenericFloor:
    return GenericFloor(Path(path))


def _layer_summary(fl: GenericFloor, n=14) -> str:
    used = sorted(fl.entity_layers, key=lambda l: -fl.entity_layers[l])
    return ", ".join(used[:n]) + (" ..." if len(used) > n else "")


# ---------------------------------------------------------------- answering
def answer(ctx, q, dxf: Path | None = None) -> Answer:
    dxf = dxf or ctx.sibling("dxf")
    if dxf is None:
        return abstain(ROUTE, "no DXF")
    fl = load(str(dxf))
    boxes, rooms_used, note, conf, label_pts = None, [], "", 0.85, []

    if q.measure == "count":
        if q.obj in OBJ_CLASSES:
            classes = OBJ_CLASSES[q.obj]
            if q.obj == "door":
                boxes, note = fl.doors()
            elif q.obj == "window":
                boxes, note = fl.windows()
            elif q.obj == "column":
                boxes, note = fl.columns()
            else:
                boxes, note = fl.fixtures(classes)
            if boxes is None:
                return abstain(ROUTE, f"{note} (layers: {_layer_summary(fl)})")
            if not boxes:
                return abstain(ROUTE, f"no {q.obj} found; in a DXF whose scheme is not the Swiss one a 0 is not proof")
            value = len(boxes)
        elif q.obj == "room" and q.label:
            rooms = fl.rooms()
            named = [r for r in rooms if r["texts"]]
            if named:
                rooms_used = [r for r in named if any(label_eq(t, q.label) for t in r["texts"])]
                value = len(rooms_used)
                note = f"closed room outline(s) on {', '.join(fl.layers_of('room')[:3])} holding the label '{q.label}'"
            else:
                hits = _distinct([(s, xy) for _, s, xy in fl.label_texts() if label_eq(s, q.label)], fl)
                label_pts = hits
                value = len(hits)
                note = f"text label(s) '{q.label}' in the plan (no closed room outlines in this file, so each label is one room)"
                conf = 0.75
            if value == 0:
                labs = sorted({s for _, s, _ in fl.label_texts()})[:15]
                return abstain(ROUTE, f"no room labelled '{q.label}' among the {len(labs)} labels read ({', '.join(labs)})")
        elif q.obj in ROOM_TYPE_OBJ:
            rtype = ROOM_TYPE_OBJ[q.obj]
            rooms = [r for r in fl.rooms() if r["texts"]]
            if rooms:
                rooms_used = [r for r in rooms if any(rtype in text_room_types(t) for t in r["texts"])]
                value = len(rooms_used)
                note = f"room outlines whose label names a {rtype}"
            else:
                hits = _distinct([(s, xy) for _, s, xy in fl.label_texts() if rtype in text_room_types(s)], fl)
                label_pts = hits
                value = len(hits)
                note = f"text labels naming a {rtype} (no closed room outlines, each label is one room)"
                conf = 0.7
            if value == 0:
                return abstain(ROUTE, f"no room label naming a {rtype}; a 0 from labels is not proof")
        else:
            return abstain(ROUTE, f"no generic DXF rule for count of {q.obj}")
    elif q.measure == "area" and q.obj == "room":
        if fl.m_per_unit is None:
            return abstain(ROUTE, f"drawing units unknown ({fl.units_source}); areas need units")
        rooms = [r for r in fl.rooms() if r["texts"]]
        if not rooms:
            return abstain(ROUTE, f"no closed room outlines with a label on a room/area layer (layers: {_layer_summary(fl)})")
        if q.label:
            rooms_used = [r for r in rooms if any(label_eq(t, q.label) for t in r["texts"])]
            if not rooms_used:
                return abstain(ROUTE, f"no room outline labelled '{q.label}'")
            note = f"room outline(s) labelled '{q.label}'"
        elif q.variant == "type_sum":
            tk = q.extra["takeoff"]
            for r in rooms:
                r["name"] = next((t for t in r["texts"] if text_room_types(t) - {"room"}), r["name"])
            rooms_used, _ = takeoff.select(rooms, tk)
            if not rooms_used:
                return abstain(ROUTE, f"no room of type {', '.join(tk['types'])}")
            note = takeoff.describe(tk)
        else:
            return abstain(ROUTE, "area question not specific enough for a generic DXF (no room label)")
        why = single_room_problem(q, rooms_used, lambda r: r["name"])
        if why:
            return abstain(ROUTE, why)
        value = round(sum(r["poly"].area * fl.m_per_unit ** 2 for r in rooms_used), 2)
        note += f"; units {fl.units_source}"
    else:
        return abstain(ROUTE, f"{q.measure} of {q.obj} not supported on a generic DXF")

    unit = "count" if q.measure == "count" else "m2"
    items = []
    if rooms_used:
        items = [{"label": r["name"], "texts": r["texts"][:4],
                  "area_m2": (round(r["poly"].area * fl.m_per_unit ** 2, 2) if fl.m_per_unit else None)} for r in rooms_used]
    elif boxes:
        items = [{"n": i + 1, "bbox": [round(v, 2) for v in b]} for i, b in enumerate(boxes)]
    elif label_pts:
        items = [{"n": i + 1, "text": s, "at": [round(v, 2) for v in xy]} for i, (s, xy) in enumerate(label_pts)]
    ans = Answer(value=value, unit=unit, route=ROUTE, confidence=conf, items=items,
                 explanation=f"{value} = {note} in {dxf.name}")
    if ctx.evidence:
        cv = VectorCanvas(fl.bounds)
        for layer, p in fl.lines:
            c = fl.layer_cls.get(layer, set())
            cv.line(p, color=(90, 90, 90) if "wall" in c else (180, 180, 180), width=2 if "wall" in c else 1)
        for _, c, r in fl.circles:
            cv.circle(c, r, color=(190, 190, 190))
        for _, s, xy in fl.label_texts():      # room labels in grey, so a label count can be checked
            cv.text(xy, s, size=10, color=(120, 120, 120), anchor="ls", bg=False)
        for i, r in enumerate(rooms_used):
            col = PALETTE[i % len(PALETTE)]
            cv.poly(list(r["poly"].exterior.coords), fill=col + (70,), outline=col, width=3)
            rp = r["poly"].representative_point()
            lab = f"#{i + 1} {r['name'] or ''}" + (f" {r['poly'].area * fl.m_per_unit ** 2:.2f} m²" if unit == "m2" else "")
            cv.text((rp.x, rp.y), lab, size=13)
        pad = (fl.m(0.08) or 0.0)
        for i, b in enumerate(boxes or []):
            cv.box(b, n=i + 1, pad=pad)
        for i, (s, xy) in enumerate(label_pts):
            cv.text(xy, f"{i + 1}: {s}", size=14, color=PALETTE[0], anchor="ls")
        out = evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question)
        ans.evidence = finish(cv.img, out, ctx.q.question, f"Answer: {value} {unit}   route: {ROUTE} (cost $0)", note=note)
    return ans


def _distinct(hits, fl):
    """The same label drawn twice at one spot (shadow text, attribute + text) counts once."""
    tol = fl.m(0.5) or 0.02 * max(fl.bounds[2] - fl.bounds[0], fl.bounds[3] - fl.bounds[1])
    out = []
    for s, xy in hits:
        if all(math.dist(xy, p) > tol for _, p in out):
            out.append((s, xy))
    return out
