"""PDF vector route: counts and areas from the PDF's own linework and text, no DXF or IFC twin.

Used when a PDF has no DXF/IFC sibling (or when the router is told to ignore twins, eval.py
--pdf-only). Scanned PDFs abstain here and go to the raster route.

How each answer is proved (shapes in PDF points, sizes checked in metres via the found scale):
  rooms       closed filled shapes. The room style is found per drawing, not hard-coded: closed
              fills are grouped by stroke (colour, width), and the group whose shapes hold the most
              room labels is the room style. Labels attach to the smallest shape containing them,
              as in the DXF route (number "7.08", name, printed area).
  area        shoelace area of the room shape x (metres per point)^2; abstains without a scale
  apartments  unit labels ("Whg. A", "App. B"); unit outlines are the coloured closed polylines
  doors       thin unfilled four-corner shapes (the door opening across the wall, 0.5-3 m by
              under 0.6 m); among those, the stroke style that touches door-swing arcs
  windows     a filled frame rectangle with two glazing lines along it, or three parallel
              strips with the same extent along the wall (DXF window rule)
  toilets     unfilled ellipses (many-segment closed outlines filling ~pi/4 of their box),
              0.2-0.9 m; with a small drain circle inside they are washbasins
  bathtubs    many-segment rounded rectangles 1.2-2.3 m long
  showers     unfilled square-ish four-corner shapes 0.6-1.6 m (shower trays)
Everything outside the plan (legend, title block) is ignored: the plan region is the box around
the room shapes, grown by 1.5 m.
"""
from __future__ import annotations

import math
import re
import unicodedata
from functools import lru_cache

from shapely.geometry import LineString, Point, Polygon, box
from shapely.strtree import STRtree

from ..answer import Answer, abstain
from ..evidence import PALETTE, ImageCanvas, evidence_path, finish
from ..pdfvec import PT_M, load_page
from .dxf_swiss import AREA_RE, NUM_RE, OUTDOOR, SHAFT_VOID, UNIT_RE, _window_groups

ROUTE = "pdf-vector"
NOMINAL_M_PER_PT = 100 * PT_M        # size gates only, never areas, when no scale is found


def _poly(pts):
    if len(pts) < 3:
        return None
    try:
        p = Polygon(pts).buffer(0)
    except Exception:
        return None
    if p.is_empty:
        return None
    if p.geom_type == "MultiPolygon":
        p = max(p.geoms, key=lambda g: g.area)
    return p


def _lum(c):
    return None if c is None else 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def _sat(c):
    return 0 if c is None else max(c) - min(c)


def _round_c(c):
    return None if c is None else tuple(round(v, 2) for v in c)


def _rect_dims(pts):
    """Side lengths (long, short) and long-axis angle of a 4-corner shape."""
    p = pts[:4]
    a = math.hypot(p[1][0] - p[0][0], p[1][1] - p[0][1])
    b = math.hypot(p[2][0] - p[1][0], p[2][1] - p[1][1])
    if a >= b:
        ang = math.atan2(p[1][1] - p[0][1], p[1][0] - p[0][0])
    else:
        ang = math.atan2(p[2][1] - p[1][1], p[2][0] - p[1][0])
    return max(a, b), min(a, b), ang


def _four_corner(path):
    sp = path.pts
    if len(path.subpaths) != 1:
        return None
    pts = sp[:-1] if len(sp) == 5 and math.hypot(sp[0][0] - sp[-1][0], sp[0][1] - sp[-1][1]) < 0.05 else sp
    if len(pts) != 4:
        return None
    return pts


def fold_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


def name_eq(pdf_text: str | None, label: str) -> bool:
    """PDF fonts without a unicode map turn umlauts into U+FFFD: 'K�che' matches 'Kuche'."""
    if pdf_text is None:
        return False
    a, b = fold_name(pdf_text), fold_name(label)
    if a == b:
        return True
    if "�" in a and len(a) == len(fold_name(label.replace("ß", "s"))):
        b2 = fold_name(label.replace("ß", "s"))
        return all(x == y or x == "�" for x, y in zip(a, b2))
    if "�" in a and len(a) == len(b):
        return all(x == y or x == "�" for x, y in zip(a, b))
    return False


class PdfFloor:
    """Rooms, units and objects of one vector PDF floor plan, in page points."""

    def __init__(self, pdf_path: str):
        self.vp = vp = load_page(pdf_path)
        self.kind = vp.kind
        self.rooms, self.units, self.doors, self.windows = [], [], [], []
        self.toilets, self.washbasins, self.bathtubs, self.showers = [], [], [], []
        self.fixture_centroids = []
        self.notes = []
        if vp.kind != "vector":
            self.scale = vp.scale()
            return
        page_area = vp.rect.width * vp.rect.height
        spans = [s for s in vp.spans if len(s.text) <= 40]
        self.span_pts = [Point(s.c) for s in spans]

        # -- rooms: closed fills grouped by stroke style; the group holding most labels wins
        groups = {}
        for p in vp.paths:
            if p.fill is None or not p.closed or len(p.pts) < 4:
                continue
            poly = _poly(p.pts)
            if poly is None or poly.area < 4 or poly.area > 0.5 * page_area:
                continue
            sig = ("stroke", _round_c(p.stroke), round(p.width, 2)) if p.stroke is not None else ("fill", _round_c(p.fill))
            groups.setdefault(sig, []).append((p, poly))
        tree = STRtree(self.span_pts) if self.span_pts else None

        def score(items):
            n = 0
            for _, poly in items:
                k = len(tree.query(poly, predicate="contains")) if tree is not None else 0
                n += 1 <= k <= 6
            return n

        scores = {sig: score(items) for sig, items in groups.items()}
        room_items = []
        if scores and max(scores.values()) > 0:
            best = max(scores, key=scores.get)
            best_fills = {_round_c(p.fill) for p, _ in groups[best]}
            chosen = [best] + [s for s in groups if s != best and s[0] == "fill" and s[1] in best_fills and scores[s] > 0]
            self.room_style = best
            for s in chosen:
                room_items.extend(groups[s])
        dedup = []
        for p, poly in room_items:
            if any(abs(poly.area - q.area) < 0.01 * q.area and poly.symmetric_difference(q).area < 0.02 * q.area for _, q in dedup):
                continue
            dedup.append((p, poly))
        self.rooms = [{"poly": poly, "path": p, "name": None, "number": None, "area_label": None, "unit": None,
                       "texts": [], "fill": _round_c(p.fill)} for p, poly in dedup]

        # plan region: box around the room shapes (legend and title block lie outside)
        polys = [r["poly"] for r in self.rooms]
        self.scale = vp.scale(polys=polys)
        mpp = self.scale.m_per_pt if self.scale.known else NOMINAL_M_PER_PT
        self.mpp_gate = mpp
        m = lambda metres: metres / mpp   # metres -> points
        if polys:
            xs0 = min(p.bounds[0] for p in polys); ys0 = min(p.bounds[1] for p in polys)
            xs1 = max(p.bounds[2] for p in polys); ys1 = max(p.bounds[3] for p in polys)
            self.region = box(xs0 - m(1.5), ys0 - m(1.5), xs1 + m(1.5), ys1 + m(1.5))
        else:
            self.region = box(*tuple(vp.rect))
        inreg = lambda r: self.region.contains(Point((r[0] + r[2]) / 2, (r[1] + r[3]) / 2))

        # texts -> rooms (smallest containing shape; else nearest within 0.6 m), as the DXF route
        self.texts = [s for s in spans if inreg(s.bbox)]
        for s in self.texts:
            if UNIT_RE.match(s.text):
                continue
            pt = Point(s.c)
            cands = [r for r in self.rooms if r["poly"].contains(pt)]
            if not cands and self.rooms:
                near = min(self.rooms, key=lambda r: r["poly"].distance(pt))
                cands = [near] if near["poly"].distance(pt) < m(0.6) else []
            if not cands:
                continue
            r = min(cands, key=lambda r: r["poly"].area)
            r["texts"].append(s.text)
            if NUM_RE.match(s.text):
                r["number"] = s.text
            elif (mm := AREA_RE.match(s.text)):
                r["area_label"] = float(mm.group(1))
            elif r["name"] is None:
                r["name"] = s.text

        # units: coloured closed polylines + unit labels
        unit_polys = []
        for p in vp.paths:
            if p.kind == "s" and _sat(p.stroke) > 0.25 and len(p.pts) >= 4:
                a, b = p.pts[0], p.pts[-1]
                if math.hypot(a[0] - b[0], a[1] - b[1]) < m(0.3):
                    poly = _poly(p.pts)
                    if poly is not None and poly.area > m(1) ** 2 * 5:
                        unit_polys.append(poly)
        for s in self.texts:
            mm = UNIT_RE.match(s.text)
            if mm:
                pt = Point(s.c)
                ps = [u for u in unit_polys if u.contains(pt)] or sorted(unit_polys, key=lambda u: u.distance(pt))[:1]
                self.units.append({"letter": mm.group(2), "label": s.text, "label_pt": s.c, "bbox": s.bbox,
                                   "poly": ps[0] if ps else None})
        for r in self.rooms:
            rp = r["poly"].representative_point()
            for u in self.units:
                if u["poly"] is not None and u["poly"].buffer(m(0.05)).contains(rp):
                    r["unit"] = u["letter"]
                    break

        # -- objects ---------------------------------------------------------------------
        room_paths = {id(r["path"]) for r in self.rooms}
        objs = [p for p in vp.paths if id(p) not in room_paths and inreg(p.rect)]
        circles = []
        for p in objs:
            if p.ops and set(p.ops) == {"c"} and len(p.ops) in (4, 8) and len(p.subpaths) == 1:
                w, h = p.rect[2] - p.rect[0], p.rect[3] - p.rect[1]
                if abs(w - h) < 0.15 * max(w, h, 1e-6):
                    circles.append(((p.rect[0] + p.rect[2]) / 2, (p.rect[1] + p.rect[3]) / 2, max(w, h) / 2))
        arcs_ends = []
        for p in objs:
            if p.kind == "s" and "c" in p.ops and len(p.subpaths) == 1 and p.pts:
                a, b = p.pts[0], p.pts[-1]
                if math.hypot(a[0] - b[0], a[1] - b[1]) > m(0.3):     # open arc, not a circle
                    arcs_ends += [Point(a), Point(b)]
        arc_tree = STRtree(arcs_ends) if arcs_ends else None

        door_c, shower_c, frames, segs = [], [], [], []
        for p in objs:
            q4 = _four_corner(p) if p.closed else None
            if q4 is not None:
                L, S, ang = _rect_dims(q4)
                Lm, Sm = L * mpp, S * mpp
                if p.fill is None and p.kind == "s":
                    if 0.5 <= Lm <= 3.2 and 0.02 <= Sm <= 0.6 and L >= 2 * S:
                        door_c.append((p, q4))
                    elif 0.6 <= Sm and Lm <= 1.8 and L <= 1.6 * S:
                        shower_c.append((p, q4))
                elif p.fill is not None and _lum(p.fill) > 0.9 and 0.2 <= Lm <= 8 and Sm <= 0.7 and L >= 1.5 * S:
                    frames.append((p, q4, L, S, ang))
            if p.kind == "s" and not p.closed:
                for sp in p.subpaths:
                    if len(sp) == 2:
                        segs.append(LineString(sp))

        # doors: the stroke style of thin quads that touch swing arcs (sliding doors have none)
        by_sig = {}
        for p, q4 in door_c:
            by_sig.setdefault((_round_c(p.stroke), round(p.width, 2)), []).append((p, q4))
        if by_sig:
            def touches(q4):
                if arc_tree is None:
                    return 0
                return len(arc_tree.query(Polygon(q4).buffer(m(0.08)), predicate="intersects")) > 0
            sig_score = {s: sum(touches(q) for _, q in v) for s, v in by_sig.items()}
            best = max(sig_score, key=lambda s: (sig_score[s], len(by_sig[s])))
            if sig_score[best] > 0 or len(by_sig) == 1:
                self.doors = [_bb(q) for _, q in by_sig[best]]
                self.door_style = best

        # windows: frame rectangle + two glazing lines along it; else three parallel strips
        seg_tree = STRtree(segs) if segs else None
        strips = []
        for p, q4, L, S, ang in frames:
            fp = Polygon(q4).buffer(0.6)
            n = 0
            if seg_tree is not None:
                for i in seg_tree.query(fp, predicate="contains"):
                    sgm = segs[i]
                    (x0, y0), (x1, y1) = sgm.coords
                    la = math.atan2(y1 - y0, x1 - x0)
                    par = abs(math.sin(la - ang)) < 0.05
                    if par and sgm.length >= 0.7 * L:
                        n += 1
            if n >= 2:
                self.windows.append(_bb(q4))
            else:
                strips.append(_bb(q4))
        if strips:
            groups3 = _strip_groups(strips, cross=m(0.6))
            self.windows += [g for g, k in groups3 if k >= 3]

        # sanitary fixtures: many-segment closed outlines
        for p in objs:
            if p.fill is not None and _lum(p.fill) < 0.9:
                continue
            sp = p.pts
            if len(p.subpaths) != 1 or len(sp) < 20 or "c" in p.ops or not p.closed:
                continue
            poly = _poly(sp)
            if poly is None:
                continue
            x0, y0, x1, y1 = poly.bounds
            w, h = (x1 - x0) * mpp, (y1 - y0) * mpp
            fillr = poly.area / max((x1 - x0) * (y1 - y0), 1e-9)
            Lm, Sm = max(w, h), min(w, h)
            if 0.70 <= fillr <= 0.82 and 0.15 <= Sm and Lm <= 0.95:
                drain = any(r * mpp < 0.06 and poly.contains(Point(cx, cy)) for cx, cy, r in circles)
                (self.washbasins if drain else self.toilets).append(poly.bounds)
                self.fixture_centroids.append(poly.centroid.coords[0])
            elif fillr > 0.85 and 1.2 <= Lm <= 2.3 and 0.5 <= Sm <= 1.2:
                self.bathtubs.append(poly.bounds)
                self.fixture_centroids.append(poly.centroid.coords[0])
        for p, q4 in shower_c:
            self.showers.append(_bb(q4))
            self.fixture_centroids.append(Polygon(q4).centroid.coords[0])

    # -- helpers (same meaning as SwissFloor in dxf_swiss.py) --------------------------------
    def room_area(self, r) -> float | None:
        if not self.scale.known:
            return None
        return round(r["poly"].area * self.scale.m_per_pt ** 2, 2)

    def is_outdoor(self, r) -> bool:
        return fold_name(r["name"] or "") in {fold_name(o) for o in OUTDOOR}

    def is_shaft(self, r) -> bool:
        return fold_name(r["name"] or "") in {fold_name(o) for o in SHAFT_VOID}

    def counts_as_indoor(self, r) -> bool:
        if not r["number"] or self.is_outdoor(r):
            return False
        if r["name"]:
            return True
        return any(r["poly"].contains(Point(c)) for c in self.fixture_centroids)


def _bb(pts):
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _strip_groups(bbs, cross):
    """_window_groups from the DXF route, also returning how many strips each group holds."""
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
    return [((min(b[0] for b in g["boxes"]), min(b[1] for b in g["boxes"]),
              max(b[2] for b in g["boxes"]), max(b[3] for b in g["boxes"])), len(g["boxes"])) for g in groups]


@lru_cache(maxsize=8)
def load(pdf_path: str) -> PdfFloor:
    return PdfFloor(pdf_path)


def _match_room_label(fl, label):
    """Rooms whose name is the label; for IFC-style labels 'NAME (number)' also name + number."""
    out = [r for r in fl.rooms if name_eq(r["name"], label)]
    if out:
        return out
    m = re.match(r"^(.*?)\s*\((?:Rum nr\. )?([^()]+)\)$", label)
    if m:
        nm, no = m.group(1), m.group(2)
        out = [r for r in fl.rooms if any(name_eq(t, nm) for t in r["texts"]) and any(name_eq(t, no) for t in r["texts"])]
        if out:
            return out
    return [r for r in fl.rooms if any(name_eq(t, label) for t in r["texts"])]


def answer(ctx, q) -> Answer:
    if ctx.q.ext != "pdf":
        return abstain(ROUTE, "not a PDF")
    fl = load(str(ctx.file))
    if fl.kind != "vector":
        return abstain(ROUTE, f"PDF page is {fl.kind} (no vector linework); left to the raster route")
    sc = fl.scale
    items, value, rooms_used, boxes, note = [], None, [], [], ""

    if q.measure == "count":
        table = {"door": fl.doors, "window": fl.windows, "toilet": fl.toilets, "washbasin": fl.washbasins,
                 "bathtub": fl.bathtubs, "shower": fl.showers}
        if q.scope == "model":
            return abstain(ROUTE, "question is about the whole model; one PDF sheet shows one level")
        if q.obj in table:
            if not fl.rooms:
                return abstain(ROUTE, "no room shapes found, so the plan region is unknown")
            boxes = table[q.obj]
            value = len(boxes)
            note = {"door": "thin unfilled door-opening quads in the door stroke style",
                    "window": "frame rectangles with two glazing lines (or three strips)",
                    "toilet": "ellipse outlines without a drain circle", "washbasin": "ellipse outlines with a drain circle",
                    "bathtub": "rounded-rectangle tub outlines 1.2-2.3 m", "shower": "square shower-tray outlines"}[q.obj]
        elif q.obj == "apartment":
            if not fl.units:
                return abstain(ROUTE, "no apartment labels found")
            value = len(fl.units)
            items = [u["label"] for u in fl.units]
            note = "apartment labels " + ", ".join(items)
        elif q.obj == "room" and q.label:
            rooms_used = _match_room_label(fl, q.label)
            if not rooms_used and not any(r["name"] for r in fl.rooms):
                return abstain(ROUTE, "no named room shapes found")
            value = len(rooms_used)
            note = f"room shapes labelled '{q.label}'"
        else:
            return abstain(ROUTE, f"no PDF rule for count of {q.obj}")
    elif q.measure == "area":
        if not sc.known:
            return abstain(ROUTE, f"scale unknown ({sc.method}: {sc.detail}); areas need a scale")
        if q.room_no:
            rooms_used = [r for r in fl.rooms if r["number"] == q.room_no]
            if len(rooms_used) != 1:
                return abstain(ROUTE, f"room {q.room_no} not found uniquely")
            note = f"shape of room {q.room_no}"
        elif q.obj == "apartment" and q.apartment:
            rooms_used = [r for r in fl.rooms if r["unit"] == q.apartment and fl.counts_as_indoor(r)]
            if not rooms_used:
                return abstain(ROUTE, f"apartment {q.apartment} not found")
            note = f"indoor rooms inside apartment {q.apartment} outline"
        elif q.variant == "net_indoor_total":
            rooms_used = [r for r in fl.rooms if fl.counts_as_indoor(r) and not fl.is_shaft(r)]
            note = "numbered named indoor rooms (outdoor, shafts and voids excluded)"
        elif q.label and q.obj == "room":
            rooms_used = _match_room_label(fl, q.label)
            if not rooms_used:
                return abstain(ROUTE, f"no room labelled '{q.label}'")
            if len(rooms_used) > 1 and re.search(r"\(", q.label):
                return abstain(ROUTE, f"room '{q.label}' not unique")
            note = f"rooms labelled '{q.label}'"
        elif q.obj == "room" and q.variant == "total":
            rooms_used = [r for r in fl.rooms if r["name"]]
            note = "all labelled room shapes"
        else:
            return abstain(ROUTE, "area query not understood")
        if not rooms_used:
            return abstain(ROUTE, "no rooms matched")
        value = round(sum(fl.room_area(r) for r in rooms_used), 2)
    else:
        return abstain(ROUTE, f"measure {q.measure} not supported from a PDF")

    if rooms_used:
        items = [{"number": r["number"], "name": r["name"], "area_m2": fl.room_area(r), "printed": r["area_label"]}
                 for r in rooms_used]
    elif boxes:
        items = [{"n": i + 1, "bbox_pt": [round(v, 1) for v in b]} for i, b in enumerate(boxes)]

    conf = 0.9
    if q.measure == "area":
        conf = round(min(conf, sc.confidence), 2)
    unit = "count" if q.measure == "count" else "m2"
    scale_note = f"scale {sc.method} ({sc.detail})" if sc.known else "scale unknown"
    ans = Answer(value=value, unit=unit, route=ROUTE, confidence=conf, items=items,
                 explanation=f"{value} = {note} in {ctx.file.name}; {scale_note}")

    if ctx.evidence:
        ans.evidence = render_evidence(ctx, fl, q, value, unit, note, rooms_used, boxes)
    return ans


def render_evidence(ctx, fl, q, value, unit, note, rooms_used, boxes):
    """Overlay drawn from the same shapes that produced the answer, on the rendered PDF page."""
    img, mat = fl.vp.render()
    cv = ImageCanvas(img)
    tr = lambda x, y: (lambda p: (p.x, p.y))(__import__("pymupdf").Point(x, y) * mat)
    from ..evidence import font
    for i, r in enumerate(rooms_used):
        col = PALETTE[i % len(PALETTE)]
        pts = [tr(x, y) for x, y in r["poly"].exterior.coords]
        cv.d.polygon(pts, fill=col + (80,), outline=col, width=3)
        rp = r["poly"].representative_point()
        lab = f"{r['number'] or r['name'] or ''} {fl.room_area(r):.2f} m²" if q.measure == "area" else f"#{i + 1}"
        x, y = tr(rp.x, rp.y)
        f = font(15)
        bb = cv.d.textbbox((x, y), lab, font=f, anchor="mm")
        cv.d.rectangle([bb[0] - 2, bb[1] - 1, bb[2] + 2, bb[3] + 1], fill=(255, 255, 255, 220))
        cv.d.text((x, y), lab, fill=(0, 0, 0), font=f, anchor="mm")
    for i, b in enumerate(boxes):
        a, c = tr(b[0], b[1]), tr(b[2], b[3])
        cv.box((min(a[0], c[0]) - 3, min(a[1], c[1]) - 3, max(a[0], c[0]) + 3, max(a[1], c[1]) + 3), n=i + 1,
               color=PALETTE[0], width=3)
    if q.obj == "apartment" and q.measure == "count":
        for i, u in enumerate(fl.units):
            col = PALETTE[i % len(PALETTE)]
            if u["poly"] is not None:
                cv.d.polygon([tr(x, y) for x, y in u["poly"].exterior.coords], fill=col + (45,), outline=col, width=3)
            a, c = tr(u["bbox"][0], u["bbox"][1]), tr(u["bbox"][2], u["bbox"][3])
            cv.box((min(a[0], c[0]) - 3, min(a[1], c[1]) - 3, max(a[0], c[0]) + 3, max(a[1], c[1]) + 3), n=i + 1, color=col)
    # the spans that set the scale are boxed too, so the scale can be checked on the image
    sc = fl.scale
    for k in ("text", "bar"):
        c = sc.candidates.get(k)
        if c and c.get("m_per_pt") and k in sc.method:
            for s in c.get("spans", []):
                a, d = tr(s.bbox[0], s.bbox[1]), tr(s.bbox[2], s.bbox[3])
                cv.d.rectangle([min(a[0], d[0]) - 2, min(a[1], d[1]) - 2, max(a[0], d[0]) + 2, max(a[1], d[1]) + 2],
                               outline=(29, 120, 196), width=2)
    sum_note = ""
    if q.measure == "area" and len(rooms_used) > 1:
        sum_note = " + ".join(f"{fl.room_area(r):.2f}" for r in rooms_used[:25]) + (" + ..." if len(rooms_used) > 25 else "")
    scale_line = f"scale: {sc.method}, {sc.m_per_pt * 100:.4f} cm per pt" if sc.known else "scale: unknown"
    out = evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question)
    return finish(cv.img, out, ctx.q.question, f"Answer: {value} {unit}   route: {ROUTE} (cost $0)",
                  note=note + (": " + sum_note if sum_note else "") + f"; {scale_line} (scale sources boxed blue)")
