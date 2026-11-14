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
from collections import Counter
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
from .. import walls as wallmod
from .. import qualifiers
from ..answer import Answer, abstain, estimate
from ..evidence import PALETTE, VectorCanvas, evidence_path, finish
from .scope_guard import ambiguous_room, single_room_problem

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


def qualifier_abstain(why: str, n: int, q, storey) -> Answer:
    """The model was read and does not record what the qualifier asks for: final, no model guesses."""
    a = abstain(ROUTE, why + ("" if "in total" in why or "in the model" in why else
                              f"; {n} {q.obj}s{' on storey ' + storey.Name if storey is not None else ''} in total"))
    a.final = True
    return a


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


_TOKEN_ALIASES = {"lvl": "level", "lev": "level", "fl": "floor", "flr": "floor", "bsmt": "basement",
                  "gf": "ground", "korrus": "floor", "sal": "floor", "etg": "floor", "og": "floor"}


def _tokens(s) -> list:
    """Lower-case alphanumeric tokens, digits split from letters ('og1' -> og 1), leading zeros and
    common abbreviations normalised ('Lvl 01' -> level 1)."""
    out = []
    for t in re.findall(r"[a-z]+|\d+", unicodedata.normalize("NFKD", norm(s)).encode("ascii", "ignore").decode()):
        t = t.lstrip("0") or "0" if t.isdigit() else t
        out.append(_TOKEN_ALIASES.get(t, t))
    return out


def storey_from_sheet_name(M, pdf_path):
    """Storey whose own name (token sequence, abbreviations normalised) appears as a contiguous run
    of tokens in the sheet's file name; the longest match wins and it must be unique.
    Returns (storey or None)."""
    ftoks = _tokens(Path(pdf_path).stem)
    best, hits = 0, []
    for st in M.storeys:
        nt = _tokens(st.Name or "")
        if not nt or not any(ch.isalnum() for ch in (st.Name or "")):
            continue
        if any(ftoks[i:i + len(nt)] == nt for i in range(len(ftoks) - len(nt) + 1)):
            if len(nt) > best:
                best, hits = len(nt), [st]
            elif len(nt) == best:
                hits.append(st)
    return hits[0] if len(hits) == 1 else None


def storey_of_sheet(M, pdf_path):
    """The storey a plan sheet shows. Content first (_storey_from_content: the storey name and space
    labels printed on the sheet). The storey named in the sheet's file name is a tie-breaker, and the
    answer when the content gives no winner; if the content has a winner that disagrees with the file
    name, nothing is clear and the result is None. Returns (storey or None, how it was decided)."""
    try:
        sc, note = _storey_from_content(M, pdf_path)
    except Exception as ex:
        sc, note = None, f"sheet text layer not readable ({type(ex).__name__})"
    sn = storey_from_sheet_name(M, pdf_path)
    fname = Path(pdf_path).name
    if sn is None:
        return sc, note
    if sc is None:
        return sn, f"storey {sn.Name} taken from the sheet file name '{fname}' (the sheet content gave no winner: {note})"
    if sc == sn:
        return sc, f"{note}; the file name '{fname}' names the same storey"
    return None, (f"the sheet content points to storey {sc.Name} but the file name '{fname}' names storey {sn.Name}")


def _storey_from_content(M, pdf_path):
    import pymupdf
    with pymupdf.open(str(pdf_path)) as doc:
        text = " ".join(page.get_text() for page in doc)
    words = set(norm(w) for w in re.split(r"[\s()]+", text) if w)
    full = norm(text)

    def printed(s):
        s = norm(s)
        return bool(s) and (s in words or (len(s) > 3 and s in full))

    by_name = {}
    for sp in M.entities("room"):
        st = M.storey_of(sp)
        for nm in {norm(sp.Name), norm(getattr(sp, "LongName", None) or "")} - {""}:
            by_name.setdefault(nm, set()).add(st)
    score = {}
    for st in M.storeys:
        sc = 3.0 if (printed(st.Name or "") and len(norm(st.Name)) > 1) else 0.0
        for nm, sts in by_name.items():
            if st in sts and printed(nm):
                sc += 1.0 / len(sts)
        score[st] = sc
    ranked = sorted(score.items(), key=lambda kv: -kv[1])
    desc = ", ".join(f"{st.Name} {v:.1f}" for st, v in ranked[:3])
    if not ranked or ranked[0][1] < 2 or (len(ranked) > 1 and ranked[1][1] >= 0.7 * ranked[0][1]):
        return None, "storey names and space labels on the sheet do not single out one storey: " + desc
    st = ranked[0][0]
    return st, f"sheet shows storey {st.Name} (score {desc})"


def _zrange(geom):
    v = geom.verts
    zs = v[2::3]
    return (min(zs), max(zs)) if zs else (None, None)


def space_height(M, sp):
    """(height m, source): the space's own height quantity, else its geometry's z extent, else storey height."""
    for k in ("FinishCeilingHeight", "ClearHeight", "Height"):
        v, src = M.qto(sp, [k])
        if v is not None and v > 0:
            return v * M.len_scale, f"IfcSpace {src}"
    fp, geom = M.footprint(sp)
    if geom is not None:
        z0, z1 = _zrange(geom)
        if z0 is not None and z1 - z0 > 1.5:
            return z1 - z0, "IfcSpace geometry height (z extent)"
    st = M.storey_of(sp)
    if st is not None:
        sts = sorted(M.storeys, key=lambda s: float(getattr(s, "Elevation", 0) or 0))
        i = sts.index(st) if st in sts else -1
        if 0 <= i < len(sts) - 1:
            h = (float(sts[i + 1].Elevation or 0) - float(st.Elevation or 0)) * M.len_scale
            if 1.5 < h < 12:
                return h, f"storey height {st.Name} to {sts[i + 1].Name} (no space height in the model)"
    return None, ""


OPEN_TOL = 0.2   # m: an opening whose plan footprint lies within this distance of the room outline is in its wall


def space_openings(M, sp, poly):
    """Doors and windows on the room's walls: plan footprint within 0.2 m of the room outline along at least
    30% of the opening's width. Vertical extent from the opening's own geometry, relative to the room floor."""
    _, sgeom = M.footprint(sp)
    sz0 = _zrange(sgeom)[0] if sgeom is not None else None
    st = M.storey_of(sp)
    if not hasattr(M, "_openings"):
        M._openings = {}
    if st not in M._openings:
        M._openings[st] = [e for cls in ("IfcDoor", "IfcWindow") for e in M.m.by_type(cls) if M.storey_of(e) == st]
    rings = poly.boundary
    out = []
    for e in M._openings[st]:
        g, geom = M.footprint(e)
        if g is None or g.is_empty or g.distance(poly) > OPEN_TOL:
            continue
        mrr = g.minimum_rotated_rectangle
        xs = list(mrr.exterior.coords) if hasattr(mrr, "exterior") else []
        sides = sorted({round(((xs[i][0] - xs[i + 1][0]) ** 2 + (xs[i][1] - xs[i + 1][1]) ** 2) ** 0.5, 4) for i in range(len(xs) - 1)}) if xs else [0]
        span = sides[-1]
        w = getattr(e, "OverallWidth", None)
        width, wsrc = (float(w) * M.len_scale, "OverallWidth") if w else (span, "plan extent")
        if rings.intersection(g.buffer(OPEN_TOL)).length < 0.3 * min(width, span or width):
            continue
        z0, z1 = _zrange(geom)
        if z0 is None or sz0 is None:
            h = getattr(e, "OverallHeight", None)
            if not h:
                continue
            a, b, zsrc = 0.0, float(h) * M.len_scale, "OverallHeight from the floor"
        else:
            a, b, zsrc = z0 - sz0, z1 - sz0, "own geometry z range"
        kind = "door" if e.is_a("IfcDoor") else "window"
        out.append({"kind": kind, "id": e.GlobalId, "name": e.Name, "width": width, "z0": a, "z1": b,
                    "src": f"{kind} {e.Name or e.GlobalId}: width {wsrc}, height {zsrc}", "bbox": g.bounds})
    return out


WALL_TOL = 0.1   # m: a room edge within this distance of a wall or column footprint is a wall face


def wall_faces(M, sp, poly):
    """The part of the room outline that runs along a wall or column (IfcWall, IfcColumn footprints within
    WALL_TOL). Reported as a note on the takeoff (a room open to another has edges without a wall); not
    used as the wall length, see wall_rooms. Returns (lines, length, how)."""
    st = M.storey_of(sp)
    if not hasattr(M, "_wallunion"):
        M._wallunion = {}
    if st not in M._wallunion:
        gs = [M.footprint(e)[0] for cls in ("IfcWall", "IfcColumn") for e in M.m.by_type(cls) if M.storey_of(e) == st]
        gs = [g for g in gs if g is not None and not g.is_empty]
        M._wallunion[st] = unary_union(gs).buffer(WALL_TOL) if gs else None
    U = M._wallunion[st]
    full = wallmod.ring_length(poly)
    if U is None:
        return poly.boundary, full, "whole outline (no wall geometry on the storey)"
    on = poly.boundary.intersection(U)
    if on.length < 0.3 * full:
        return poly.boundary, full, "whole outline (walls found along less than 30% of it)"
    return on, on.length, f"outline along walls and columns ({on.length:.2f} of {full:.2f} m; open sides left out)"


def wall_rooms(M, used):
    """Room records for bpqa/walls.py from IfcSpaces: outline, perimeter, height, openings, model check."""
    rooms = []
    for sp, area in used:
        fp, _ = M.footprint(sp)
        if fp is None or fp.is_empty:
            return None, f"space {space_display(sp)} has no geometry for its outline"
        poly = fp.simplify(0.005)
        h, hsrc = space_height(M, sp)
        mnet, _ = M.qto(sp, ["NetWallArea"])
        # the whole outline is the wall length. Counting only the edges along wall footprints (wall_faces)
        # was tried and validated worse against the models' own GrossWallArea (46% vs 66% within 2%):
        # space outlines often sit more than 0.1 m off the wall footprints. It is reported as a note.
        _, along, _ = wall_faces(M, sp, poly)
        full = wallmod.ring_length(poly)
        note = "" if along >= 0.95 * full else f"; only {along:.2f} m of it runs along a wall within {WALL_TOL} m (open side or offset outline)"
        rooms.append({"label": space_display(sp), "number": sp.Name, "floor_area": area,
                      "floor_src": f"IfcSpace {M.space_area(sp)[1]}", "poly": poly,
                      "perimeter": full, "perimeter_src": f"IfcSpace geometry outline{note}",
                      "height": h, "height_src": hsrc, "openings": space_openings(M, sp, poly),
                      "model_net_wall": mnet * M._area_scale() if mnet is not None else None, "guid": sp.GlobalId})
    return rooms, ""


def space_type_label(sp) -> str:
    """The space label that names its type: LongName ('Bathroom 1') when it has one, else Name."""
    for s in (getattr(sp, "LongName", None), sp.Name):
        if s and takeoff.room_types(s):
            return s
    return getattr(sp, "LongName", None) or sp.Name or ""


FIXTURE_KINDS = [("shower", r"shower|dusch|suihku|douche|doccia"), ("bath", r"bath ?tub|bathtub|\bbath\b|badewanne|kylpyamme|baignoire|vasca"),
                 ("wc", r"\bwc\b|toilet|water ?closet|closet|urinal|\bpan\b|vessa|toilette|cuvette"),
                 ("basin", r"wash ?basin|\bbasin\b|lavabo|lavatory|hand ?basin|sink|pesuallas|waschtisch|waschbecken")]


def fixture_kind(e) -> str | None:
    """shower | bath | wc | basin from the fixture's PredefinedType, name and type name."""
    t = E.get_type(e)
    txt = " ".join(str(x) for x in (getattr(e, "PredefinedType", None), getattr(e, "Name", None),
                                    getattr(t, "Name", None) if t is not None else None,
                                    getattr(t, "PredefinedType", None) if t is not None else None) if x).lower()
    for kind, rx in FIXTURE_KINDS:
        if re.search(rx, txt):
            return kind
    return None


def _space_fixtures(M, spaces):
    """{space id: [(fixture, how)]}: a fixture belongs to the space that contains it by an IFC spatial
    relation (IfcRelContainedInSpatialStructure / IfcRelReferencedInSpatialStructure), else to the
    space on its storey whose footprint holds the fixture's footprint centre."""
    out = {sp.id(): [] for sp in spaces}
    by_storey = {}
    for sp in spaces:
        by_storey.setdefault(M.storey_of(sp), []).append(sp)
    for e in M.entities("sanitary"):
        if fixture_kind(e) is None:
            continue
        host = None
        for rel in getattr(e, "ContainedInStructure", None) or ():
            if rel.RelatingStructure.id() in out:
                host = (rel.RelatingStructure, "contained in the space")
        for rel in getattr(e, "ReferencedInStructures", None) or ():
            if rel.RelatingStructure.id() in out:
                host = (rel.RelatingStructure, "referenced in the space")
        if host is None:
            fp = M.footprint(e)[0]
            if fp is not None and not fp.is_empty:
                c = fp.centroid
                for sp in by_storey.get(M.storey_of(e), []):
                    sfp = M.footprint(sp)[0]
                    if sfp is not None and not sfp.is_empty and sfp.buffer(0.05).contains(c):
                        host = (sp, "inside the space footprint")
                        break
        if host is not None:
            out[host[0].id()].append((e, host[1]))
    return out


def infer_space_types(M, spaces):
    """{space id: (types, fixture description)} for spaces whose type is not in their name but follows
    from the sanitary fixtures in them: a shower or bathtub = bathroom; a WC pan (with or without a
    basin) and no shower/bath = wc. A basin alone says nothing."""
    res = {}
    fx = _space_fixtures(M, spaces)
    for sp in spaces:
        kinds = Counter(fixture_kind(e) for e, _ in fx[sp.id()])
        if not kinds:
            continue
        how = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) + " (" + "/".join(sorted({h for _, h in fx[sp.id()]})) + ")"
        if kinds.get("shower") or kinds.get("bath"):
            res[sp.id()] = ({"bathroom"}, how)
        elif kinds.get("wc"):
            res[sp.id()] = ({"wc"}, how)
    return res


def toilet_rooms_fallback(M, floor, tk):
    """(spaces, takeoff, why) for "the toilets" on a model with no space named or typed as a WC: the
    rooms that contain a toilet (takeoff.toilet_fallback). A space holding a WC pan counts as having a
    toilet, a space holding only other sanitary fixtures as having none, a space with no fixture
    attached as unknown; with no sanitary fixture on the floor at all the rule falls back to the
    bathroom labels alone. why is the reason to abstain with when no room qualifies."""
    fx = _space_fixtures(M, floor)
    kinds = {sid: {fixture_kind(e) for e, _ in fl} for sid, fl in fx.items()}
    def has_toilet(r):
        k = kinds.get(r["sp"].id())
        return None if not k else "wc" in k
    wrapped = [{"name": space_type_label(sp), "sp": sp} for sp in floor]
    used, _, tk2 = takeoff.toilet_fallback(wrapped, tk, has_toilet=has_toilet if any(kinds.values()) else None)
    tr = tk2.get("toilet_rooms")
    if not tr:
        return [], tk, ""
    if not any(kinds.values()):
        tr["fixture_source"] = "model"      # no sanitary fixture on this floor: the wording says "the model", not "the drawing"
    return [r["sp"] for r in used], tk2, ("; ".join(filter(None, [tr["why"]])) if not used else "")



def floor_room_names(M, floor, n=14) -> str:
    """'Toilet x3, Depot, Gang ...': the type labels of the spaces on the floor (grouped, drawing order)."""
    labs = Counter(space_type_label(sp) or "(unnamed)" for sp in floor)
    out = [f"{k} x{v}" if v > 1 else k for k, v in labs.items()]
    return ", ".join(out[:n]) + (f" and {len(out) - n} more" if len(out) > n else "")


def types_elsewhere(M, storey, types) -> str:
    """'Bad - Dame is on Kælder': where else in the model a room of one of `types` is named ('' when nowhere)."""
    by = {}
    for sp in M.entities("room"):
        st = M.storey_of(sp)
        if st is storey or (st is not None and storey is not None and st.id() == storey.id()):
            continue
        if takeoff.room_types(space_type_label(sp)) & set(types):
            nm = st.Name if st is not None else "(no storey)"
            by.setdefault(space_type_label(sp), [])
            if nm not in by[space_type_label(sp)]:
                by[space_type_label(sp)].append(nm)
    if not by:
        return ""
    return "; ".join(f"{lab} is on {', '.join(sts[:5])}" + (" ..." if len(sts) > 5 else "") for lab, sts in list(by.items())[:4])


def no_room_of_type(M, storey, floor, types):
    """(sentence, extra) for a floor that has no room of `types`: what is on it, and where such rooms are.
    The caller answers 0 m2 (proven, the way the count question answers 0); `extra` lists rooms of a related type."""
    names = " / ".join(takeoff.TYPE_NAMES[t] for t in types)
    s = (f"None on this floor: 0 m2. No space on storey {storey.Name} is named as {names}"
         f"{' or typed as one from sanitary fixtures' if set(types) & {'bathroom', 'wc'} else ''}. "
         f"Rooms on this storey: {floor_room_names(M, floor)}.")
    elsewhere = types_elsewhere(M, storey, types)
    if elsewhere:
        s += f" Elsewhere in the model: {elsewhere}."
    related = [sp for sp in floor if takeoff.room_types(space_type_label(sp)) & ({"wc"} if "bathroom" in types else {"bathroom"})
               and not takeoff.room_types(space_type_label(sp)) & set(types)]
    if related:
        rt = "WC rooms" if "bathroom" in types else "bathrooms"
        s += (f" {len(related)} {rt} on this storey ({floor_room_names(M, related, 4)}) are not counted; "
              f"ask for the toilets or 'wet rooms' to include them.")
    return s

# count objects answered from IfcSpace names (and the WC fixtures for "toilets")
ROOM_COUNTS = set(takeoff.COUNT_TYPES) | {"toilet"}
SINK_OBJS = {"sink", "washbasin"}      # counted from the sanitary fixtures whose type reads as a basin or sink


def room_type_count(M, storey, q) -> dict:
    """Count of rooms of one type (bedrooms, bathrooms, WC rooms ...) on the storey (or the model), from
    the IfcSpace names; and "toilets". Returns {used, value, note[, estimate]} or {why}.

    "toilets" follows the dataset's convention for IFC models: a question about "toilets" on a floor is
    answered with the WC / toilet spaces (ifc_bench: "How many toilets exist on the ground floor?" ->
    "Toilet/WC spaces on ground floor: 2"). "Toilet bowls", "WC seats" and "water closets" ask for the
    fixtures: IfcSanitaryTerminal / IfcFlowTerminal whose type reads as a WC (fixture_kind). Each answer
    also states the other count, so the reader sees both."""
    spaces = [s for s in M.entities("room") if storey is None or M.storey_of(s) == storey]
    where = f"on storey {storey.Name}" if storey is not None else "in the model"
    fx = [e for e in M.entities("sanitary") if (storey is None or M.storey_of(e) == storey) and fixture_kind(e) == "wc"]
    fx_note = (f"{len(fx)} WC fixture(s) (IfcSanitaryTerminal of WC type) {where}" if fx else
               f"0 WC fixture objects (IfcSanitaryTerminal of WC type) {where}: the model has no WC fixtures")
    if q.obj == "toilet" and not q.extra.get("toilet_ambiguous"):
        if not fx:
            return {"why": fx_note + "; WC seats cannot be counted from the model"}
        wc_sp = [s for s in spaces if "wc" in takeoff.room_types(space_type_label(s))]
        return {"used": [(e, None) for e in fx], "value": len(fx),
                "note": f"{len(fx)} x IfcSanitaryTerminal of WC type {where} ({len(wc_sp)} WC / toilet spaces)"}
    t = "wc" if q.obj == "toilet" else takeoff.COUNT_TYPES[q.obj]
    rt = takeoff.count_room_type([{"name": space_type_label(s), "number": s.Name, "sp": s} for s in spaces],
                                 q.obj if q.obj != "toilet" else "wc_room")
    typed = [r["sp"] for r in rt.get("rooms") or []]
    if rt["value"] is None:
        return {"why": rt["why"]}
    est = rt.get("estimate", "")
    note = f"{len(typed)} x IfcSpace named as {takeoff.TYPE_NAMES[t]} {where}"
    if typed:
        labs = Counter(space_type_label(s) for s in typed)
        note += " (" + ", ".join(f"{k} x{v}" if v > 1 else k for k, v in labs.most_common(8)) + ")"
    if not typed and t in ("bathroom", "wc"):
        inf = infer_space_types(M, spaces)
        typed = [s for s in spaces if s.id() in inf and t in inf[s.id()][0]]
        if typed:
            note = f"{len(typed)} x IfcSpace typed as {takeoff.TYPE_NAMES[t]} from the sanitary fixtures inside {where}"
            est = (est + "; " if est else "") + "no space name reads as this room type; typed from the fixtures inside"
    if q.obj == "toilet":
        if not typed and fx:
            return {"used": [(e, None) for e in fx], "value": len(fx),
                    "note": f"{len(fx)} x IfcSanitaryTerminal of WC type {where} (no space is named as a WC / toilet room)",
                    "estimate": "'toilets' read as WC fixtures because no space is named as a toilet room"}
        if not fx and not typed:
            note = (f"{fx_note}, and 0 spaces named as WC / toilet room {where}; "
                    f"'toilets' read as toilet rooms (the dataset's IFC convention)")
        else:
            note += f"; 'toilets' read as toilet rooms (the dataset's IFC convention); {fx_note}"
    if not typed and not spaces:
        return {"why": f"no IfcSpace {where}"}
    return {"used": [(s, None) for s in typed], "value": len(typed), "note": note, "estimate": est}


def space_display(sp) -> str:
    n, ln = sp.Name or "", getattr(sp, "LongName", None) or ""
    return f"{ln} ({n})" if ln and n and ln != n else (ln or n)


def sheet_scope_applies(ctx, q) -> bool:
    """True when the question was asked on a plan sheet (PDF/SVG rendered from an IFC model) and names
    neither a level nor the whole model/building: the numbers then belong to the storey the sheet shows.
    Questions asked on the .ifc itself keep whole-model scope unless a level is named."""
    return (ctx.q.ext in ("pdf", "svg") and q.scope != "model" and not q.extra.get("whole_model")
            and not (q.scope == "level" and q.level))


def sheet_storey(M, ctx):
    """(storey or None, note). Never raises: an unreadable sheet gives (None, reason)."""
    try:
        return storey_of_sheet(M, ctx.file)
    except Exception as ex:
        return None, f"sheet text layer not readable ({type(ex).__name__})"


def self_zero(ctx, M, storey, floor, tk, q, path, sheet_note) -> Answer:
    """A takeoff or area question for a room type the storey does not have: 0 m2, proven (the storey's spaces were read
    and none is of the type), with the rooms that are on the storey listed. The count question answers 0 the same way."""
    text = no_room_of_type(M, storey, floor, tk["types"])
    ans = Answer(value=0.0, unit="m2", route=ROUTE, confidence=0.9,
                 items=[{"storey": storey.Name, "rooms_on_storey": len(floor)}],
                 explanation=text + f" Source: {path.name}" + (f"; {sheet_note}." if sheet_note else "."))
    if ctx.evidence:
        try:
            ans.evidence = render(ctx, M, storey, [], q, 0.0, "m2", f"0 rooms of the asked type on storey {storey.Name}")
        except Exception:
            ans.evidence = None
    return ans


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
    counted = q.obj in CLASSES or q.obj in ROOM_COUNTS or q.obj in SINK_OBJS
    if storey is None and (q.variant == "type_sum" and ctx.q.ext == "pdf" or counted and sheet_scope_applies(ctx, q)):
        # a plan sheet shows one storey: find it from the storey name and space labels printed on the sheet
        storey, sheet_note = sheet_storey(M, ctx)
        if storey is None:
            return abstain(ROUTE, f"cannot tell which storey this sheet shows ({sheet_note}); "
                                  f"ask with a level name or for the whole model")

    used, value, note, unit = [], None, "", ""
    tier_reason = ""
    inferred, inf_note = {}, ""
    tk_eff = q.extra.get("takeoff")       # the takeoff as interpreted (the toilets fallback may rewrite its types)
    if q.measure == "count" and q.obj in ROOM_COUNTS:
        rc = room_type_count(M, storey, q)
        if rc.get("why"):
            return abstain(ROUTE, rc["why"])
        used, value, unit, note = rc["used"], rc["value"], "count", rc["note"]
        if rc.get("estimate"):
            tier_reason = rc["estimate"]
    elif q.measure == "count" and q.obj in SINK_OBJS:
        everything = M.entities("sanitary")
        where = f"on storey {storey.Name}" if storey is not None else "in the model"
        if not everything:
            a = abstain(ROUTE, "cannot prove: this model has no sink objects (it has no sanitary fixtures at all, "
                               "IfcSanitaryTerminal), so sinks cannot be counted from it")
            a.final = True
            return a
        here = [e for e in everything if storey is None or M.storey_of(e) == storey]
        ents = [e for e in here if fixture_kind(e) == "basin"]
        used, value, unit = [(e, None) for e in ents], len(ents), "count"
        note = (f"{len(ents)} x IfcSanitaryTerminal of sink / washbasin type {where}"
                + (f" (the model has {len(here)} other sanitary fixture(s) {where}, none of them a sink)" if not ents and here else ""))
    elif q.measure == "count":
        if q.obj not in CLASSES:
            return abstain(ROUTE, f"no IFC class for {q.obj}")
        ents = M.entities(q.obj)
        if storey is not None:
            ents = [e for e in ents if M.storey_of(e) == storey]
        ents, qnote, qtier, qwhy = qualifiers.filter_ifc(M, ents, q, storey)
        if qwhy:
            return qualifier_abstain(qwhy, len(ents), q, storey)
        n_before = len(ents)
        ents = _external_filter(M, ents, q)
        if ents is None:
            return abstain(ROUTE, "interior/exterior asked but IsExternal not set in the model")
        used = [(e, None) for e in ents]
        value, unit = len(ents), "count"
        where = f" on storey {storey.Name}" if storey else " in the model"
        note = f"{len(ents)} x {CLASSES[q.obj][0]}" + where
        filters = [qnote.removesuffix(where)] if qnote else []
        if q.extra.get("external") is not None and not q.extra.get("slab_type"):
            filters.append(f"{len(ents)} of {n_before} {q.obj}s with IsExternal = {q.extra['external']}")
        if filters:
            note = f"{'; '.join(filters)}{where} ({CLASSES[q.obj][0]})"
        if qtier:
            tier_reason = qtier
    elif q.measure in ("area", "length", "volume"):
        if q.obj == "room":
            spaces = M.entities("room")
            if storey is not None:
                spaces = [s for s in spaces if M.storey_of(s) == storey]
            if q.variant == "type_sum":
                tk = tk_eff
                want = set(tk["types"]) | ({"bathroom", "wc"} if "wet" in tk["types"] else set())
                floor = [sp for sp in M.entities("room") if storey is None or M.storey_of(sp) == storey]
                by_name = [sp for sp in spaces if takeoff.room_types(space_type_label(sp)) & set(tk["types"])]
                spaces = by_name
                if not spaces:
                    inf = infer_space_types(M, floor)
                    spaces = [sp for sp in floor if sp.id() in inf and inf[sp.id()][0] & want]
                    if spaces:
                        inferred = {sp.id(): inf[sp.id()][1] for sp in spaces}
                where = f"on storey {storey.Name}" if storey is not None else "in the model"
                if not spaces and "wc" in tk["types"]:
                    # "the toilets" with no separate WC room (by name or by fixtures): the rooms that contain a toilet
                    if storey is not None and not any("bathroom" in takeoff.room_types(space_type_label(sp)) for sp in floor):
                        return self_zero(ctx, M, storey, floor, tk, q, path, sheet_note)
                    spaces, tk_eff, fb_why = toilet_rooms_fallback(M, floor, tk)
                    if not spaces and fb_why:
                        return abstain(ROUTE, fb_why)
                if not spaces:
                    gen = takeoff.generic_reason([{"name": space_type_label(sp)} for sp in floor], tk["types"])
                    if gen:
                        a = abstain(ROUTE, gen + f" (space names {where}: {', '.join(sorted({space_type_label(sp) for sp in floor} - {''})[:15])})")
                        a.final = True
                        return a
                    if storey is not None and floor:
                        return self_zero(ctx, M, storey, floor, tk, q, path, sheet_note)
                    names = sorted({space_type_label(sp) for sp in floor} - {""})
                    return abstain(ROUTE, f"no IfcSpace of type {', '.join(tk['types'])} {where} "
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
            why = single_room_problem(q, [s for s, _ in used], lambda s: space_display(s) + (
                f" on {M.storey_of(s).Name}" if M.storey_of(s) is not None else ""))
            if why:
                return ambiguous_room(ROUTE, why)
            value, unit = round(sum(a for _, a in used), 3), "m2"
            note = f"sum of {len(used)} IfcSpace areas ({', '.join(sorted(srcs))})"
        elif (q.obj, q.measure) in QTO_KEYS:
            ents = M.entities(q.obj)
            if storey is not None:
                ents = [e for e in ents if M.storey_of(e) == storey]
            ents, qnote, qtier, qwhy = qualifiers.filter_ifc(M, ents, q, storey)
            if qwhy:
                return qualifier_abstain(qwhy, len(ents), q, storey)
            ents = _external_filter(M, ents, q)
            if ents is None:
                return abstain(ROUTE, "interior/exterior asked but IsExternal not set in the model")
            if qtier:
                tier_reason = qtier
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
            note = f"sum of {', '.join(sorted(srcs))} over {len(used)}/{len(ents)} {CLASSES[q.obj][0]}" + (f" ({qnote})" if qnote else "")
        else:
            return abstain(ROUTE, f"no IFC rule for {q.obj} {q.measure}")
    else:
        return abstain(ROUTE, f"measure {q.measure} not supported")

    items = [{"guid": e.GlobalId, "name": getattr(e, "Name", None), "value": (round(v, 3) if v is not None else None)} for e, v in used]
    ans = Answer(value=value, unit=unit, route=ROUTE, confidence=0.9, items=items,
                 explanation=f"{value} = {note} in {path.name}" + (f"; {sheet_note}" if sheet_note else ""))
    if tier_reason:
        ans.tier, ans.tier_reason = "estimate", tier_reason
    if q.variant == "type_sum":
        rows = []
        for e, v in used:
            st = M.storey_of(e)
            rows.append({"label": space_display(e), "area": v, "number": e.Name,
                         "source": f"IfcSpace {M.space_area(e)[1]}" + (f", storey {st.Name}" if st is not None else "")})
        if tk_eff and tk_eff.get("toilet_rooms"):
            tk_eff["toilet_rooms"]["no_boxes"] = True     # the IFC evidence image does not box the fixtures
        ans.value, ans.items, expl = takeoff.summary(rows, tk_eff)
        for it, (e, _) in zip(ans.items, used):
            it["guid"] = e.GlobalId
        ans.explanation = expl + f" Source: {path.name}, {note}" + (f"; {sheet_note}." if sheet_note else ".")
        value = ans.value
        if inferred:
            ans.tier = "estimate"
            ans.tier_reason = (f"no space name reads as {', '.join(q.extra['takeoff']['types'])}; the room type was inferred "
                               f"from sanitary fixtures in the {len(inferred)} spaces used")
            inf_note = (" Room type inferred from fixtures (no IfcSpace name matches): "
                        + "; ".join(f"{space_display(sp)}: {inferred[sp.id()]}" for sp, _ in used[:8])
                        + (" ..." if len(used) > 8 else "") + ".")
            ans.explanation += inf_note
        tk = tk_eff
        if tk.get("toilet_rooms") and not inferred:
            ans.tier, ans.tier_reason = "estimate", takeoff.fallback_reason(tk)
        scope_why = takeoff.scope_reason(tk, [r["label"] for r in rows])
        if scope_why:
            estimate(ans, scope_why)
        if wallmod.surfaces(tk)[1]:
            rooms, why = wall_rooms(M, used)
            if rooms is None:
                return abstain(ROUTE, why)
            ans.value, ans.items, wexpl, totals = wallmod.summary(rooms, tk, ctx.q.question)
            for it, r in zip(ans.items, rooms):
                it["guid"] = r["guid"]
            ans.items.append({"totals": totals})
            ans.explanation = wexpl + f" Source: {path.name}" + (f"; {sheet_note}." if sheet_note else ".") + inf_note
            ans.checks = wall_checks(ans.items)
            if any(c["status"] == "fail" for c in ans.checks):
                ans.tier, ans.tier_reason = "estimate", "computed wall area differs from the model's own NetWallArea by more than 5%"
                if scope_why:
                    ans.tier_reason += "; " + scope_why
            if any("ASSUMED" in r.get("height_src", "") for r in rooms) or not any(r["height"] for r in rooms):
                ans.tier = "estimate"
                ans.tier_reason = (ans.tier_reason + "; " if ans.tier_reason else "") + "room height assumed"
            if ctx.evidence:
                ans.evidence = render_walls(ctx, M, rooms, ans.value, totals, wexpl, ans.checks, ans.tier_reason if ans.tier == "estimate" else "", tk)
            return ans
    if ctx.evidence:
        ans.evidence = render(ctx, M, storey, used, q, value, unit, note)
    return ans


def wall_checks(items):
    out = []
    for it in items:
        if "model_net_wall_m2" in it and "net_wall_m2" in it and it.get("tiled_height_m") == it.get("height_m"):
            m, c = it["model_net_wall_m2"], it["net_wall_m2"]
            ok = abs(c - m) <= 0.05 * max(m, 1e-6)
            out.append({"name": f"wall area {it['room']}", "status": "pass" if ok else "fail",
                        "detail": f"computed {c:.2f} m2 vs model NetWallArea {m:.2f} m2"})
    return out


def short_reason(reason: str, n: int = 70) -> str:
    """Estimate reason for an evidence header: whole clauses only, never cut inside a bracket or quote."""
    if len(reason) <= n:
        return reason
    out = ""
    for part in re.split(r"(?<=[;,]) ", reason):
        if len(out) + len(part) > n or part.count("(") != part.count(")") or part.count("'") % 2:
            break
        out += part + " "
    return (out.strip(" ;,") or reason[:n].rsplit(" ", 1)[0]) + " ..."


def render_walls(ctx, M, rooms, value, totals, expl, checks=None, estimate="", tk=None):
    """Plan of the storey: the rooms filled, their wall faces drawn thick (blue), openings deducted boxed
    (amber), each room labelled with floor and net wall area."""
    from ..evidence import AMBER, BLUE
    sp0 = next((s for s in M.m.by_type("IfcSpace") if s.GlobalId == rooms[0]["guid"]), None)
    storey = M.storey_of(sp0) if sp0 is not None else None
    ctx_els = [e for cls in ("IfcWall", "IfcSpace") for e in M.m.by_type(cls) if storey is None or M.storey_of(e) == storey]
    fps = [(e, M.footprint(e)[0]) for e in ctx_els[:2000]]
    geoms = [g for _, g in fps if g is not None] + [r["poly"] for r in rooms]
    cv = VectorCanvas(unary_union(geoms).bounds)
    for e, g in fps:
        if g is None:
            continue
        for p in getattr(g, "geoms", [g]):
            if hasattr(p, "exterior"):
                cv.poly(list(p.exterior.coords), fill=(235, 235, 235, 255) if e.is_a("IfcWall") else None,
                        outline=(175, 175, 175), width=1)

    inp = wallmod.parse_inputs(ctx.q.question, tk)
    for r in rooms:
        w = wallmod.room_walls(r, inp)
        for o, d in zip(r["openings"], w["deductions"]):
            o["deducted"] = d["area"]

    def lab(i, r):
        w = wallmod.room_walls(r, inp)
        return f"{i + 1}. {r['label'][:22]}: floor {r['floor_area']:.2f}, walls {w['net']:.2f} m²"

    wallmod.draw_rooms(cv, rooms, BLUE, BLUE, AMBER, lab)
    out = evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question)
    hs = sorted({r["height_src"].split(" (")[0] for r in rooms})
    nop = sum(len(r["openings"]) for r in rooms)
    return finish(cv.img, out, ctx.q.question, wallmod.answer_line(value, totals, ROUTE, short_reason(estimate)),
                  note=(f"Walls = outline (thick blue) x height - {nop} openings (amber boxes). Height: {'; '.join(hs)}. "
                        f"Surfaces: {totals['surfaces']}. Storey {storey.Name if storey is not None else '-'}."), checks=checks)


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
            if q.variant == "type_sum":
                lab = f"{i + 1}. {space_display(e)} {v:.2f} m²"
            cv.text((c.x, c.y), lab if q.measure != "count" else f"#{i + 1}", size=11)
    out = evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question)
    shown = sum(1 for _, _, g in hl if g is not None)
    return finish(cv.img, out, ctx.q.question, (takeoff.answer_line(value, len(used), ROUTE) if q.variant == "type_sum" else f"Answer: {value} {unit}   route: ifc (cost €0)"),
                  note=f"{note}. Storey {storey.Name}: {shown} highlighted."
                  + (" Floor area only, no waste allowance." if q.variant == "type_sum" else ""))
