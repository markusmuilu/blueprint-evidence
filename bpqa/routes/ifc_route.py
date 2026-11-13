"""IFC route: counts by entity type, areas/lengths/volumes from quantity sets or geometry.

Rules:
  model-level count  = number of entities of the IFC class (subtypes included); stairs count only
                       top-level IfcStair (flights nested inside another stair are parts)
  level count        = entities of the class contained in the storey (or aggregated below it)
  room area          = Qto NetFloorArea when the model carries it, else footprint area of the
                       space geometry (IfcOpenShell triangulation projected to the floor plane)
  wall length / side area / volume, slab volume = sum of Qto quantities on the storey
Space names are matched as "LongName (Name)", "Name" or "LongName".
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as E
import ifcopenshell.util.shape as US
import ifcopenshell.util.unit as UU
from shapely.geometry import Polygon
from shapely.ops import unary_union

from .. import takeoff
from ..answer import Answer, abstain
from ..evidence import PALETTE, VectorCanvas, evidence_path, finish

ROUTE = "ifc"
CLASSES = {"door": ["IfcDoor"], "window": ["IfcWindow"], "wall": ["IfcWall"], "column": ["IfcColumn"],
           "beam": ["IfcBeam"], "stair": ["IfcStair"], "slab": ["IfcSlab"], "room": ["IfcSpace"],
           "sanitary": ["IfcSanitaryTerminal", "IfcFlowTerminal"]}
QTO_KEYS = {
    ("wall", "length"): ["Length"],
    ("wall", "area"): ["NetSideArea", "NetSideAreaLeft", "GrossSideArea"],
    ("wall", "volume"): ["NetVolume", "GrossVolume"],
    ("slab", "volume"): ["NetVolume", "GrossVolume"],
    ("room", "area"): ["NetFloorArea", "GrossFloorArea"],
}


def norm(s) -> str:
    return unicodedata.normalize("NFC", str(s or "")).strip().casefold()


class Model:
    def __init__(self, path: Path):
        self.path = path
        self.m = ifcopenshell.open(str(path))
        self.len_scale = UU.calculate_unit_scale(self.m)
        self.settings = ifcopenshell.geom.settings()
        self.settings.set("use-world-coords", True)
        self._fp = {}
        self.storeys = self.m.by_type("IfcBuildingStorey")

    # -- spatial ------------------------------------------------------------------------
    def storey_of(self, el):
        c = E.get_container(el)
        seen = 0
        while c is not None and not c.is_a("IfcBuildingStorey") and seen < 6:
            c = E.get_aggregate(c) or E.get_container(c)
            seen += 1
        if c is None:
            agg = E.get_aggregate(el)
            if agg is not None and agg.is_a("IfcBuildingStorey"):
                return agg
            if agg is not None:
                return self.storey_of(agg)
        return c

    def find_storey(self, name):
        n = norm(name)
        for s in self.storeys:
            if norm(s.Name) == n or norm(s.LongName if hasattr(s, "LongName") else "") == n:
                return s
        return None

    def entities(self, obj):
        out = []
        for cls in CLASSES.get(obj, []):
            try:
                ents = self.m.by_type(cls)
            except RuntimeError:
                continue
            if cls == "IfcFlowTerminal":
                ents = [e for e in ents if (t := E.get_type(e)) is not None and t.is_a("IfcSanitaryTerminalType")]
            out += ents
            if out:
                break
        if obj == "stair":   # flights aggregated into a parent stair are not separate stairs
            out = [e for e in out if not (E.get_aggregate(e) and E.get_aggregate(e).is_a("IfcStair"))]
        return out

    # -- quantities ---------------------------------------------------------------------
    def qto(self, el, keys):
        for pset, vals in E.get_psets(el, qtos_only=True).items():
            for k in keys:
                if k in vals and isinstance(vals[k], (int, float)):
                    return float(vals[k]), f"{pset}.{k}"
        return None, None

    def is_external(self, el):
        for pset, vals in E.get_psets(el, psets_only=True).items():
            if pset.endswith("Common") and "IsExternal" in vals:
                return bool(vals["IsExternal"])
        return None

    def footprint(self, el):
        if el.id() not in self._fp:
            try:
                sh = ifcopenshell.geom.create_shape(self.settings, el)
                v = sh.geometry.verts
                f = sh.geometry.faces
                tris = []
                for i in range(0, len(f), 3):
                    a, b, c = (f[i] * 3, f[i + 1] * 3, f[i + 2] * 3)
                    p = Polygon([(v[a], v[a + 1]), (v[b], v[b + 1]), (v[c], v[c + 1])])
                    if p.area > 1e-9:
                        tris.append(p)
                fp = unary_union(tris) if tris else None
                self._fp[el.id()] = (fp, sh.geometry)
            except Exception:
                self._fp[el.id()] = (None, None)
        return self._fp[el.id()]

    def space_area(self, sp):
        val, src = self.qto(sp, QTO_KEYS[("room", "area")])
        if val is not None:
            return val * self._area_scale(), src
        fp, geom = self.footprint(sp)
        if geom is not None:
            return US.get_footprint_area(geom), "geometry footprint"
        return None, None

    def _area_scale(self):
        u = UU.get_project_unit(self.m, "AREAUNIT")
        return UU.get_prefix_multiplier(getattr(u, "Prefix", None)) ** 2 if u is not None else 1.0

    def _vol_scale(self):
        u = UU.get_project_unit(self.m, "VOLUMEUNIT")
        return UU.get_prefix_multiplier(getattr(u, "Prefix", None)) ** 3 if u is not None else 1.0


@lru_cache(maxsize=3)
def load(path: str) -> Model:
    return Model(Path(path))


def _external_filter(M, ents, q):
    if q.extra.get("slab_type"):
        ents = [e for e in ents if getattr(e, "PredefinedType", None) == q.extra["slab_type"]]
    want = q.extra.get("external")
    if want is None:
        return ents
    flags = [(e, M.is_external(e)) for e in ents]
    if all(f is None for _, f in flags):
        return None
    return [e for e, f in flags if f is want]


def space_label_variants(sp):
    n, ln = sp.Name or "", getattr(sp, "LongName", None) or ""
    return {norm(f"{ln} ({n})"), norm(n), norm(ln), norm(f"{n} {ln}")}


def storey_of_sheet(M, pdf_path):
    """The storey whose space labels appear most often in the sheet's text layer. Needs at least two
    hits and a clear winner; returns (storey or None, how it was decided)."""
    import pymupdf
    with pymupdf.open(str(pdf_path)) as doc:
        text = " ".join(page.get_text() for page in doc)
    words = set(norm(w) for w in re.split(r"[\s()]+", text) if w)
    full = norm(text)
    hits = {}
    for st in M.storeys:
        n = 0
        for sp in M.entities("room"):
            if M.storey_of(sp) != st:
                continue
            nm = norm(sp.Name)
            if nm and (nm in words or (len(nm) > 3 and nm in full)):
                n += 1
        hits[st] = n
    ranked = sorted(hits.items(), key=lambda kv: -kv[1])
    if not ranked or ranked[0][1] < 2 or (len(ranked) > 1 and ranked[1][1] >= 0.8 * ranked[0][1]):
        return None, "space labels on the sheet do not single out one storey: " + ", ".join(
            f"{st.Name} {n}" for st, n in ranked[:3])
    st, n = ranked[0]
    return st, f"sheet shows storey {st.Name} ({n} of its space labels printed on the sheet)"


def space_type_label(sp) -> str:
    """The space label that names its type: LongName ('Bathroom 1') when it has one, else Name."""
    for s in (getattr(sp, "LongName", None), sp.Name):
        if s and takeoff.room_types(s):
            return s
    return getattr(sp, "LongName", None) or sp.Name or ""


def space_display(sp) -> str:
    n, ln = sp.Name or "", getattr(sp, "LongName", None) or ""
    return f"{ln} ({n})" if ln and n and ln != n else (ln or n)


def answer(ctx, q) -> Answer:
    path = ctx.model_ifc()
    if path is None:
        return abstain(ROUTE, "no IFC model for this drawing")
    M = load(str(path))
    storey = None
    if q.scope == "level" and q.level:
        storey = M.find_storey(q.level)
        if storey is None:
            return abstain(ROUTE, f"storey '{q.level}' not in model")
    sheet_note = ""
    if q.variant == "type_sum" and storey is None and ctx.q.ext == "pdf":
        # a plan sheet shows one storey: find it from the space labels printed on the sheet
        storey, sheet_note = storey_of_sheet(M, ctx.file)
        if storey is None:
            return abstain(ROUTE, f"cannot tell which storey this sheet shows ({sheet_note})")

    used, value, note, unit = [], None, "", ""
    if q.measure == "count":
        if q.obj not in CLASSES:
            return abstain(ROUTE, f"no IFC class for {q.obj}")
        ents = M.entities(q.obj)
        if storey is not None:
            ents = [e for e in ents if M.storey_of(e) == storey]
        ents = _external_filter(M, ents, q)
        if ents is None:
            return abstain(ROUTE, "interior/exterior asked but IsExternal not set in the model")
        used = [(e, None) for e in ents]
        value, unit = len(ents), "count"
        note = f"{len(ents)} x {CLASSES[q.obj][0]}" + (f" on storey {storey.Name}" if storey else " in the model")
    elif q.measure in ("area", "length", "volume"):
        if q.obj == "room":
            spaces = M.entities("room")
            if storey is not None:
                spaces = [s for s in spaces if M.storey_of(s) == storey]
            if q.variant == "type_sum":
                tk = q.extra["takeoff"]
                spaces = [sp for sp in spaces if takeoff.room_types(space_type_label(sp)) & set(tk["types"])]
                if not spaces:
                    names = sorted({space_type_label(sp) for sp in M.entities("room")} - {""})
                    return abstain(ROUTE, f"no IfcSpace of type {', '.join(tk['types'])} "
                                          f"(space names: {', '.join(names[:15])})")
            if q.label and q.variant != "total":
                lab = norm(q.label)
                spaces = [s for s in spaces if lab in space_label_variants(s)]
                if len(spaces) != 1:
                    return abstain(ROUTE, f"space '{q.label}' matched {len(spaces)} spaces")
            srcs = set()
            for s in spaces:
                a, src = M.space_area(s)
                if a is not None:
                    used.append((s, a))
                    srcs.add(src)
            if not used:
                return abstain(ROUTE, "no space areas")
            value, unit = round(sum(a for _, a in used), 3), "m2"
            note = f"sum of {len(used)} IfcSpace areas ({', '.join(sorted(srcs))})"
        elif (q.obj, q.measure) in QTO_KEYS:
            ents = M.entities(q.obj)
            if storey is not None:
                ents = [e for e in ents if M.storey_of(e) == storey]
            ents = _external_filter(M, ents, q)
            if ents is None:
                return abstain(ROUTE, "interior/exterior asked but IsExternal not set in the model")
            scale = {"length": M.len_scale, "area": M._area_scale(), "volume": M._vol_scale()}[q.measure]
            srcs = set()
            for e in ents:
                v, src = M.qto(e, QTO_KEYS[(q.obj, q.measure)])
                if v is not None:
                    used.append((e, v * scale))
                    srcs.add(src)
            if not used:
                return abstain(ROUTE, f"no quantity sets for {q.obj} {q.measure}")
            value = round(sum(v for _, v in used), 3)
            unit = {"length": "m", "area": "m2", "volume": "m3"}[q.measure]
            note = f"sum of {', '.join(sorted(srcs))} over {len(used)}/{len(ents)} {CLASSES[q.obj][0]}"
        else:
            return abstain(ROUTE, f"no IFC rule for {q.obj} {q.measure}")
    else:
        return abstain(ROUTE, f"measure {q.measure} not supported")

    items = [{"guid": e.GlobalId, "name": getattr(e, "Name", None), "value": (round(v, 3) if v is not None else None)} for e, v in used]
    ans = Answer(value=value, unit=unit, route=ROUTE, confidence=0.9, items=items,
                 explanation=f"{value} = {note} in {path.name}")
    if q.variant == "type_sum":
        rows = []
        for e, v in used:
            st = M.storey_of(e)
            rows.append({"label": space_display(e), "area": v, "number": e.Name,
                         "source": f"IfcSpace {M.space_area(e)[1]}" + (f", storey {st.Name}" if st is not None else "")})
        ans.value, ans.items, expl = takeoff.summary(rows, q.extra["takeoff"])
        for it, (e, _) in zip(ans.items, used):
            it["guid"] = e.GlobalId
        ans.explanation = expl + f" Source: {path.name}, {note}" + (f"; {sheet_note}." if sheet_note else ".")
        value = ans.value
    if ctx.evidence:
        ans.evidence = render(ctx, M, storey, used, q, value, unit, note)
    return ans


def render(ctx, M, storey, used, q, value, unit, note):
    """Plan view: storey walls and spaces in grey, the counted/measured elements highlighted."""
    if storey is None and used:
        storey = M.storey_of(used[0][0])
    if storey is None or len(used) > 400:
        return None
    ctx_els = [e for cls in ("IfcWall", "IfcSpace") for e in M.m.by_type(cls) if M.storey_of(e) == storey]
    fps = [(e, M.footprint(e)[0]) for e in ctx_els]
    hl = [(e, v, M.footprint(e)[0]) for e, v in used if M.storey_of(e) == storey]
    geoms = [g for _, g in fps if g is not None] + [g for _, _, g in hl if g is not None]
    if not geoms:
        return None
    b = unary_union(geoms).bounds
    cv = VectorCanvas(b)

    def draw(g, **kw):
        for p in getattr(g, "geoms", [g]):
            if hasattr(p, "exterior"):
                cv.poly(list(p.exterior.coords), **kw)

    for e, g in fps:
        if g is not None:
            draw(g, fill=(235, 235, 235, 255) if e.is_a("IfcWall") else None, outline=(170, 170, 170), width=1)
    for i, (e, v, g) in enumerate(hl):
        if g is None:
            continue
        col = PALETTE[i % len(PALETTE)]
        if q.measure == "count" and not e.is_a("IfcSpace"):
            cv.box(g.bounds, n=i + 1, color=col, pad=0.05)
        else:
            draw(g, fill=col + (80,), outline=col, width=2)
            c = g.representative_point()
            lab = f"{e.Name or ''}" + (f" {v:.2f}" if v is not None else "")
            cv.text((c.x, c.y), lab if q.measure != "count" else f"#{i + 1}", size=11)
    out = evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question)
    shown = sum(1 for _, _, g in hl if g is not None)
    return finish(cv.img, out, ctx.q.question, (takeoff.answer_line(value, len(used), ROUTE) if q.variant == "type_sum" else f"Answer: {value} {unit}   route: ifc (cost $0)"),
                  note=f"{note}. Storey {storey.Name}: {shown} highlighted.")
