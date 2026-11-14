"""Slab area by type and thickness (IFC): "slab area by type", "how many m2 of 200 mm slab", "total floor slab area".

Per IfcSlab the model gives, in this order of preference:

  area       NetArea (openings excluded) or GrossArea from the slab's own quantity set (Qto_SlabBaseQuantities /
             BaseQuantities): READ. Without one: the plan footprint of the slab geometry: COMPUTED (tier estimate).
  thickness  a thickness quantity (Width / Thickness / Dicke), else a thickness property of a Pset, else the sum of
             the material layers: all READ. Without any: the vertical extent of the geometry: COMPUTED (estimate).
  type       the slab's IfcSlabType name; else its ObjectType; else its Name without the trailing element id.

Slabs are grouped by (type, thickness to the millimetre). Reinforcement: no model in the dataset has IfcReinforcingBar
or IfcReinforcingMesh, so a question about bars or rebar is answered "cannot prove" with what the model does know.
"""
from __future__ import annotations

import re

from .query import fold, strip_quoted

SLAB_WORDS = re.compile(
    r"\bslabs?\b|\bgeschossdecke\w*|\bbodenplatte\w*|\bdeckenplatte\w*|\bstahlbetondecke\w*|\bbetondecke\w*|"
    r"\bbetonilaat\w*|\bvalipohja\w*|\balapohja\w*|\bylapohja\w*|\bbjalklag\w*|\bbetongplatt\w*|\bgolvplatt\w*|\bbottenplatt\w*|"
    r"\bdalles?\b|\bplanchers?\b|\bsolai\b|\bsoletta\b")
AREA_WORDS = re.compile(
    r"\bareas?\b|\bm2\b|\bm\^2|square ?met|\bsq\.? ?m\b|\bsqm\b|\bflache\w*|\bflaeche\w*|\bpinta-?ala\w*|\bneli\w+|\byta\b|\bytan\b|"
    r"\bsurface\b|\bsuperficie\b|\bquadratmeter\b|\bcarres?\b")
REBAR_WORDS = re.compile(
    r"\brebars?\b|\breinforc\w*|\bbewehr\w*|\barmier\w*|\barmatur\w*|\braudoit\w*|\barmering\w*|\bstirrups?\b|\bbars\b|"
    r"\bstab(?:stahl|e)\b|\bstahl\b|\bbetonistee\w*|\bteraks\w*|\bferraillage\b|\barmature\b")
BREAKDOWN = re.compile(r"\bby types?\b|\bper types?\b|\beach types?\b|\bevery types?\b|\bby thickness\b|\bper thickness\b|\bbreakdown\b|"
                       r"\bgrouped?\b|\bbroken down\b|\bnach typ\w*|\bpro typ\w*|\btyypeittain\b|\btyypin mukaan\b|\bper typ\b|\bpar type\b|"
                       r"\bslab types?\b")
GROSS = re.compile(r"\bgross\b|\bbrutto\b|\bbruttoala\b|\bbrut\b")
KINDS = [("BASESLAB", r"\bbase ?slabs?\b|\bfoundation slabs?\b|\bslabs? on grade\b|\bground slabs?\b|\bbodenplatte\w*|\balapohja\w*|\bbottenplatt\w*"),
         ("ROOF", r"\broof slabs?\b|\bylapohja\w*|\bdachdecke\w*|\btakplatt\w*"),
         ("LANDING", r"\blanding slabs?\b|\bpodest\w*"),
         ("FLOOR", r"\bfloor slabs?\b|\bfloor ?slabs?\b|\bgeschossdecke\w*|\bvalipohja\w*|\bbjalklag\w*|\bplanchers?\b|\bsolai\b|\bfloors? and slabs?\b")]
_THICK = re.compile(r"(\d+(?:[.,]\d+)?)\s*(mm|cm|m)(?![\w2^])")


def parse_slab(question: str) -> dict | None:
    """Slab question -> {thickness_mm, predefined, group, rebar, gross} or None. Only questions that name slabs AND ask for an
    area / quantity (or for reinforcement) are read here; the slab volume and slab type text questions keep their routes."""
    f = fold(strip_quoted(question))
    if not SLAB_WORDS.search(f):
        return None
    rebar = bool(REBAR_WORDS.search(f))
    if not (AREA_WORDS.search(f) or rebar):
        return None
    if re.search(r"\bvolume\b|\bvolumen\b|\bm3\b|\bcubic\b|\bkuutio\w*|\bvolym\b", f) and not AREA_WORDS.search(f):
        return None
    out = {"thickness_mm": None, "predefined": None, "group": bool(BREAKDOWN.search(f)), "rebar": rebar,
           "gross": bool(GROSS.search(f))}
    for m in _THICK.finditer(f):
        v = float(m.group(1).replace(",", "."))
        mm = v * {"mm": 1, "cm": 10, "m": 1000}[m.group(2)]
        if 20 <= mm <= 2000:
            out["thickness_mm"] = round(mm, 1)
            break
    for kind, rx in KINDS:
        if re.search(rx, f):
            out["predefined"] = kind
            break
    return out


# ------------------------------------------------------------------------------------------------------ model side

AREA_KEYS_NET = ["NetArea", "NetFloorArea"]
AREA_KEYS_GROSS = ["GrossArea", "GrossFloorArea"]
THICK_QTO = ["Width", "Thickness", "Dicke", "Depth"]
THICK_PSET = {"thickness", "width", "slabthickness", "structural thickness", "dicke"}


def _type_name(e) -> str:
    import ifcopenshell.util.element as E
    t = E.get_type(e)
    if t is not None and getattr(t, "Name", None):
        return str(t.Name)
    ot = getattr(e, "ObjectType", None)
    if ot:
        return str(ot)
    nm = re.sub(r":\d+$", "", str(getattr(e, "Name", None) or "")).strip()
    return nm or "(no type)"


def _thickness(M, e):
    """(metres, source, computed)"""
    import ifcopenshell.util.element as E
    v, src = M.qto(e, THICK_QTO)
    if v is not None and v > 0:
        return v * M.len_scale, f"quantity {src}", False
    for pset, vals in E.get_psets(e, psets_only=True).items():
        for k, val in vals.items():
            if k.lower() in THICK_PSET and isinstance(val, (int, float)) and val > 0:
                return float(val) * M.len_scale, f"property {pset}.{k}", False
    mat = E.get_material(e)
    ls = None
    if mat is not None:
        if mat.is_a("IfcMaterialLayerSetUsage"):
            ls = mat.ForLayerSet
        elif mat.is_a("IfcMaterialLayerSet"):
            ls = mat
    if ls is not None:
        tot = sum(float(l.LayerThickness or 0) for l in ls.MaterialLayers)
        if tot > 0:
            return tot * M.len_scale, "sum of the material layers", False
    fp, geom = M.footprint(e)
    if geom is not None:
        zs = geom.verts[2::3]
        if zs and max(zs) - min(zs) > 0.01:
            return max(zs) - min(zs), "geometry z extent", True
    return None, "", False


def _area(M, e, gross: bool):
    """(m2, source, computed, net, gross)"""
    keys = (AREA_KEYS_GROSS + AREA_KEYS_NET) if gross else (AREA_KEYS_NET + AREA_KEYS_GROSS)
    sc = M._area_scale()
    v, src = M.qto(e, keys)
    n, _ = M.qto(e, AREA_KEYS_NET)
    g, _ = M.qto(e, AREA_KEYS_GROSS)
    if v is not None:
        return v * sc, f"quantity {src}", False, (n * sc if n is not None else None), (g * sc if g is not None else None)
    fp, _ = M.footprint(e)
    if fp is not None and not fp.is_empty:
        return fp.area, "plan footprint of the geometry", True, None, None
    return None, "", False, None, None


def collect(M, storey, slab: dict) -> dict:
    ents = M.entities("slab")
    if storey is not None:
        ents = [e for e in ents if M.storey_of(e) == storey]
    rows = []
    for e in ents:
        a, asrc, acomp, net, gross = _area(M, e, slab.get("gross", False))
        t, tsrc, tcomp = _thickness(M, e)
        st = M.storey_of(e)
        rows.append({"e": e, "guid": e.GlobalId, "name": getattr(e, "Name", None), "type": _type_name(e),
                     "predefined": str(getattr(e, "PredefinedType", None) or "NOTDEFINED"),
                     "area": a, "area_src": asrc, "area_computed": acomp, "net": net, "gross": gross,
                     "t_m": t, "t_src": tsrc, "t_computed": tcomp, "storey": st.Name if st is not None else None})
    return {"rows": rows, "n_all": len(ents)}


def _t_mm(r):
    return None if r["t_m"] is None else round(r["t_m"] * 1000)


def answer(ctx, q, M, path, storey):
    """Answer a slab question from a loaded model (called by ifc_route.answer). Returns an Answer."""
    from .answer import Answer, abstain, estimate
    from .routes import ifc_route as R
    slab = q.extra["slab"]
    where = f"on storey {storey.Name}" if storey is not None else "in the model"
    got = collect(M, storey, slab)
    rows = got["rows"]
    if not rows:
        a = abstain("ifc", f"cannot prove: the model has no IfcSlab {where}")
        a.final = True
        return a
    if slab.get("rebar"):
        bars = [e for c in ("IfcReinforcingBar", "IfcReinforcingMesh", "IfcTendon") for e in _by_type(M, c)]
        thick = sorted({_t_mm(r) for r in rows if r["t_m"] is not None})
        area = sum(r["area"] for r in rows if r["area"] is not None)
        known = (f"it does know the slabs themselves: {len(rows)} IfcSlab {where}, {area:.2f} m2 in total, thicknesses "
                 f"{', '.join(f'{t} mm' for t in thick) if thick else 'not recorded'}")
        if bars:
            why = (f"cannot prove: the model has {len(bars)} reinforcement object(s) (IfcReinforcingBar / Mesh) but none is linked to a "
                   f"slab, so bars cannot be attributed to slabs; {known}")
        else:
            why = f"cannot prove: the model has no reinforcement objects (no IfcReinforcingBar, IfcReinforcingMesh or IfcTendon); {known}"
        a = abstain("ifc", why)
        a.final = True
        return a

    sel, notes = rows, []
    kind = slab.get("predefined") or q.extra.get("slab_type")
    tier_why = []
    if kind:
        by = [r for r in rows if r["predefined"] == kind]
        if not by and kind == "FLOOR" and any(r["predefined"] in ("NOTDEFINED", "USERDEFINED") for r in rows):
            by = [r for r in rows if r["predefined"] in ("NOTDEFINED", "USERDEFINED")]
            tier_why.append("no slab is typed FLOOR in the model; slabs typed NOTDEFINED read as floor slabs")
        sel = by
        notes.append(f"{kind} slabs only ({len(by)} of {len(rows)})")
    ext = q.extra.get("external")
    if ext is not None:
        ents = R._external_filter(M, [r["e"] for r in sel], q)
        if ents is None:
            return abstain("ifc", "interior/exterior asked but IsExternal not set in the model")
        keep = {e.GlobalId for e in ents}
        sel = [r for r in sel if r["guid"] in keep]
        notes.append(f"IsExternal = {ext}")
    want = slab.get("thickness_mm")
    unknown_t = [r for r in sel if r["t_m"] is None]
    if want is not None:
        match = [r for r in sel if r["t_m"] is not None and abs(r["t_m"] * 1000 - want) <= 1.0]
        have = sorted({_t_mm(r) for r in sel if r["t_m"] is not None})
        notes.append(f"thickness {want:g} mm (+-1 mm); thicknesses in the model: {', '.join(f'{t} mm' for t in have) or 'none recorded'}")
        if unknown_t:
            tier_why.append(f"{len(unknown_t)} slab(s) have no thickness, so they may belong to the {want:g} mm group")
        sel = match
    if not sel:
        if want is not None and not unknown_t and not kind and ext is None:
            return Answer(value=0.0, unit="m2", route="ifc", confidence=0.9, items=[],
                          explanation=f"0 m2: no IfcSlab of {want:g} mm {where}; " + "; ".join(notes) + f". Source: {path.name}.")
        a = abstain("ifc", f"no slab matches ({len(rows)} slabs {where}): " + "; ".join(notes))
        a.final = True
        return a
    missing = [r for r in sel if r["area"] is None]
    if missing:
        return abstain("ifc", f"{len(missing)} slab(s) have neither an area quantity nor geometry")
    total = sum(r["area"] for r in sel)
    comp_a = [r for r in sel if r["area_computed"]]
    comp_t = [r for r in sel if r["t_computed"]] if (want is not None or slab.get("group")) else []
    if comp_a:
        tier_why.append(f"{len(comp_a)} of {len(sel)} slab(s) carry no area quantity; their plan footprint was computed from the geometry")
    if comp_t:
        tier_why.append(f"{len(comp_t)} slab thickness(es) computed from the geometry (no thickness in the model)")
    groups: dict = {}
    for r in sel:
        k = (r["type"], _t_mm(r))
        g = groups.setdefault(k, {"type": r["type"], "thickness_mm": k[1], "count": 0, "area_m2": 0.0, "guids": [],
                                  "area_source": set(), "thickness_source": set()})
        g["count"] += 1
        g["area_m2"] += r["area"]
        g["guids"].append(r["guid"])
        g["area_source"].add(r["area_src"])
        g["thickness_source"].add(r["t_src"] or "not recorded")
    glist = sorted(groups.values(), key=lambda g: -g["area_m2"])
    for g in glist:
        g["area_m2"] = round(g["area_m2"], 2)
        g["area_source"] = "; ".join(sorted(g["area_source"]))
        g["thickness_source"] = "; ".join(sorted(g["thickness_source"]))
    basis = "GrossArea" if slab.get("gross") else "NetArea (openings excluded)"
    items = [{"guid": r["guid"], "name": r["name"], "type": r["type"],
              "thickness_mm": _t_mm(r), "thickness_source": r["t_src"] or "not recorded",
              "area_m2": round(r["area"], 2), "area_source": r["area_src"],
              **({"net_area_m2": round(r["net"], 2)} if r["net"] is not None else {}),
              **({"gross_area_m2": round(r["gross"], 2)} if r["gross"] is not None else {}),
              "storey": r["storey"], "predefined_type": r["predefined"]} for r in sel]
    items.append({"groups": glist, "total_m2": round(total, 2), "slabs": len(sel), "basis": basis})
    lines = "; ".join(f"{g['type']}" + (f", {g['thickness_mm']} mm" if g["thickness_mm"] is not None else ", thickness not recorded")
                      + f": {g['count']} slab(s), {g['area_m2']:.2f} m2" for g in glist[:12]) + ("; ..." if len(glist) > 12 else "")
    what = f"slab area {where}" + (f" ({'; '.join(notes)})" if notes else "")
    how = ("the plan footprint of the geometry (the model has no area quantity), COMPUTED" if len(comp_a) == len(sel) else
           f"{basis} from the slab quantity set" + (", the plan footprint of the geometry (COMPUTED) where the model has none" if comp_a else ""))
    expl = (f"{total:.2f} m2 = {what}: {len(sel)} IfcSlab. Area per slab: {how}. By type and thickness: {lines}. "
            f"Source: {path.name}.")
    ans = Answer(value=round(total, 2), unit="m2", route="ifc", confidence=0.9, items=items, explanation=expl)
    if tier_why:
        estimate(ans, "; ".join(tier_why))
    if ctx.evidence:
        st = storey or _main_storey(M, sel)
        on = [(r["e"], r["area"]) for r in sel if (M.storey_of(r["e"]) == st)]
        try:
            ans.evidence = R.render(ctx, M, st, on, q, round(total, 2), "m2",
                                    f"{len(sel)} slab(s) {where}, {total:.2f} m2" + (f" ({'; '.join(notes[:1])})" if notes else ""))
        except Exception:
            ans.evidence = None
    return ans


def _by_type(M, cls):
    try:
        return M.m.by_type(cls)
    except RuntimeError:
        return []


def _main_storey(M, sel):
    tot: dict = {}
    for r in sel:
        st = M.storey_of(r["e"])
        tot[st] = tot.get(st, 0.0) + (r["area"] or 0.0)
    return max(tot, key=tot.get) if tot else None


def apply(question: str, q):
    """Query hook (called at the end of RuleParser.parse): a slab area / reinforcement question becomes
    measure area, obj slab, variant slab_report. Anything else is returned unchanged."""
    if q.variant == "type_sum" or q.extra.get("takeoff") is not None:
        return q
    slab = parse_slab(question)
    if slab is None:
        return q
    q.measure, q.obj, q.variant, q.label = "area", "slab", "slab_report", None
    q.extra.pop("compound", None)           # a breakdown by type is part of every slab answer
    q.extra["slab"] = slab
    return q
