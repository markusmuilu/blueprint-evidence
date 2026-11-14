"""Piping (IFC): "total pipe length", "pipe length by system or diameter", "how many pipe fittings".

A pipe segment is an IfcPipeSegment (IFC4) or, in IFC2x3 where pipes are exported as the generic IfcFlowSegment, a flow
segment whose type or object type names a pipe ("Pipe Types:Cold Water", "Rohrtypen:...") and not a duct, conduit or cable.
Fittings are IfcPipeFitting, or in IFC2x3 the IfcFlowFitting objects connected through ports to a pipe segment (or, when the
model has no ports, every fitting of a model that has pipes only).

  length    a length quantity (Qto Length) or the Length property of a Pset, in the model's length unit: READ. A segment with
            neither: the extent of its geometry along its axis: COMPUTED (the answer is then an estimate and says so).
            The read lengths are checked against the geometry of the same segments; a mismatch above 5% is reported and
            lowers the tier.
  system    the IfcSystem / IfcDistributionSystem the segment is assigned to; else the system word of a Revit type name
            ("Pipe Types:Hot Water:123" -> Hot Water).
  diameter  a nominal diameter property (NominalDiameter, DN, "40 mm" in Size); else an outer diameter property.

Piping on PDF or scans is out of scope: a plan sheet without an IFC model gets "cannot prove" with the reason.
"""
from __future__ import annotations

import re

from .query import fold, strip_quoted

PIPE_WORDS = re.compile(
    r"\bpipes?\b|\bpiping\b|\bpipework\b|\bpipelines?\b|\brohre?\b|\brohrleitung\w*|\bleitungen\b|\brohrnetz\w*|\bputki\w*|\bputkisto\w*|"
    r"\brorledning\w*|\bror\b|\bledning\w*|\btuyaux?\b|\btuyauterie\b|\bcanalisation\w*|\btubazion\w*|\btubi\b")
LENGTH_WORDS = re.compile(
    r"\blength\b|\bmetres?\b|\bmeters?\b|\blinear\b|\bhow long\b|\bpituu\w*|\blange\b|\blaenge\b|\blongueur\b|\blangd\b|\bmetri\w*|\bmeter\b|\bm\b")
FITTING_WORDS = re.compile(
    r"\bfittings?\b|\bformstuck\w*|\bformstueck\w*|\bfitting\w*|\bliitin\w*|\braccords?\b|\braccordi\b|\bpipe elbows?\b|\bpipe bends?\b|\bpipe tees?\b")
COUNT_WORDS = re.compile(r"\bhow many\b|\bwie viele\b|\bwieviele\b|\bnumber of\b|\banzahl\b|\bkuinka monta\b|\bmontako\b|\bcombien\b|"
                         r"\bhur manga\b|\bcount\b|\blukumaara\b|\bantal\w*\b")
BY_SYSTEM = re.compile(r"\bby systems?\b|\bper systems?\b|\beach systems?\b|\bsystems?\b|\bnach system\w*|\bsysteemeittain\b|\bjarjestelm\w*|\bpar systeme\b")
BY_DIAMETER = re.compile(r"\bdiameters?\b|\bsizes?\b|\bdn ?\d*\b|\bdurchmesser\b|\bhalkaisij\w*|\bdiametre\b|\bdimensions?\b")
BREADTH = re.compile(r"\bcomprehensive\b|\banalysis\b|\bwhich\b|\bwhat (?:different )?types?\b|\bmaterials?\b|\bdistribut\w*|\blevels?\b|\bstoreys?\b|"
                     r"\bfloors?\b|\btypes?\b|\bcomponents?\b|\bcategor\w*|\bpercent\w*|\bconnectivity\b|\bflexible\b|\bduct\w*|\bventilation\b|\bwith quantities\b|\bcounts\b|\baccessor\w*")


def parse_piping(question: str) -> dict | None:
    """{kind: length | fittings | segments, by: system | diameter | None} for a numeric piping question, else None.
    Questions that ask for a type list, materials, levels or a comprehensive analysis are left to the property route."""
    f = fold(strip_quoted(question))
    if not PIPE_WORDS.search(f) and not (FITTING_WORDS.search(f) and re.search(r"\bpip", f)):
        return None
    if BREADTH.search(f):
        return None
    by = "system" if BY_SYSTEM.search(f) else ("diameter" if BY_DIAMETER.search(f) else None)
    if FITTING_WORDS.search(f):
        return {"kind": "fittings", "by": by}
    if COUNT_WORDS.search(f) and not LENGTH_WORDS.search(f):
        return {"kind": "segments", "by": by}
    if LENGTH_WORDS.search(f):
        return {"kind": "length", "by": by}
    return None


# a system named in the question -> the system names of the model it covers (matched on the folded system name)
SYSTEM_WORDS = [
    ("cold water", r"cold|kalt|kylm", r"\bcold water\b|\bkaltwasser\b"),
    ("hot water", r"hot|warm|dhw", r"\bhot water\b|\bwarmwasser\b"),
    ("water supply", r"cold|hot|domestic|water|supply|wasser|vesi",
     r"\bwater supply\b|\bdomestic water\b|\bpotable\b|\bfreshwater\b|\btrinkwasser\b|\bwater pip"),
    ("waste", r"waste|drain|sewage|sewer|abwasser|schmutz|soil",
     r"\bwaste\b|\bwastewater\b|\bdrain\w*|\bsewage\b|\bsewer\w*|\babwasser\w*"),
    ("vent", r"vent", r"\bvent\b|\bvent pip"),
    ("storm", r"storm|rain", r"\bstorm\w*|\brain ?water\b"),
    ("heating", r"heat|heiz|chw|hydronic|radiat|lampo", r"\bheating\b|\bheizung\w*|\bhydronic\b|\bradiator\b"),
]


def system_asked(question: str):
    f = fold(strip_quoted(question))
    for name, names_rx, ask_rx in SYSTEM_WORDS:
        if re.search(ask_rx, f):
            return name, names_rx
    return None, None


# ------------------------------------------------------------------------------------------------------ model side

NOT_PIPE = re.compile(r"duct|conduit|cable|kanal|luft|wire|tray|electr", re.I)
IS_PIPE = re.compile(r"pipe|rohr|leiding|buis|putki|tuyau|tubo|\bror\b|ledning", re.I)


def _names(e):
    import ifcopenshell.util.element as E
    t = E.get_type(e)
    return " ".join(str(x) for x in (getattr(t, "Name", None) if t is not None else None, getattr(e, "ObjectType", None), getattr(e, "Name", None)) if x)


def _by_type(M, cls):
    try:
        return M.m.by_type(cls)
    except RuntimeError:
        return []


def pipe_segments(M):
    segs = _by_type(M, "IfcPipeSegment")
    if segs:
        return segs, "IfcPipeSegment"
    flow = _by_type(M, "IfcFlowSegment")
    return [e for e in flow if IS_PIPE.search(_names(e)) and not NOT_PIPE.search(_names(e))], "IfcFlowSegment of a pipe type"


def _ports_neighbours(M):
    """{element id: set of element ids connected to it through ports} for the model's port connections."""
    port_el = {}
    for rel in _by_type(M, "IfcRelConnectsPortToElement"):
        port_el[rel.RelatingPort.id()] = rel.RelatedElement.id()
    for rel in _by_type(M, "IfcRelConnectsPorts"):
        pass
    nb: dict = {}
    for rel in _by_type(M, "IfcRelConnectsPorts"):
        a, b = port_el.get(rel.RelatingPort.id()), port_el.get(rel.RelatedPort.id())
        if a is not None and b is not None and a != b:
            nb.setdefault(a, set()).add(b)
            nb.setdefault(b, set()).add(a)
    return nb


def pipe_fittings(M, segs):
    fit = _by_type(M, "IfcPipeFitting")
    if fit:
        return fit, "IfcPipeFitting"
    flow = _by_type(M, "IfcFlowFitting")
    if not flow:
        return [], ""
    seg_ids = {s.id() for s in segs}
    nb = _ports_neighbours(M)
    if nb:
        ok, frontier = set(), set(seg_ids)
        flow_ids = {f.id() for f in flow}
        while frontier:
            nxt = set()
            for i in frontier:
                for j in nb.get(i, ()):
                    if j in flow_ids and j not in ok:
                        ok.add(j)
                        nxt.add(j)
            frontier = nxt
        return [f for f in flow if f.id() in ok], "IfcFlowFitting connected through ports to pipe segments"
    other = [e for e in _by_type(M, "IfcFlowSegment") if e not in segs]
    if not other:
        return list(flow), "IfcFlowFitting (the model has pipe segments only)"
    return [f for f in flow if IS_PIPE.search(_names(f)) and not NOT_PIPE.search(_names(f))], "IfcFlowFitting of a pipe type name"


LEN_KEYS = ["Length", "NetLength", "GrossLength"]
LEN_PROPS = {"length", "laenge", "länge", "pituus", "lange", "longueur", "langd", "längd"}


def _length(M, e):
    """(metres, source, computed)"""
    import ifcopenshell.util.element as E
    v, src = M.qto(e, LEN_KEYS)
    if v is not None and v > 0:
        return v * M.len_scale, f"quantity {src}", False
    for pset, vals in E.get_psets(e, psets_only=True).items():
        for k, val in vals.items():
            if k.lower() in LEN_PROPS and isinstance(val, (int, float)) and val > 0:
                return float(val) * M.len_scale, f"property {pset}.{k}", False
    g = _geom_length(M, e)
    return (g, "geometry extent along the axis", True) if g else (None, "", False)


def _geom_length(M, e):
    fp, geom = M.footprint(e)
    if geom is None:
        return None
    import numpy as np
    v = np.asarray(geom.verts, dtype=float).reshape(-1, 3)
    if len(v) < 3:
        return None
    c = v - v.mean(axis=0)
    u, s, vt = np.linalg.svd(c, full_matrices=False)
    ext = c @ vt[0]
    return float(ext.max() - ext.min())


def _geom_length_safe(M, e):
    try:
        return _geom_length(M, e)
    except Exception:
        return None


DIA_PROPS = ["nominaldiameter", "nominal diameter", "dn", "nenndurchmesser", "nennweite"]
OUT_PROPS = ["outer diameter", "outside diameter", "outerdiameter", "aussendurchmesser", "durchmesser", "diameter"]


def _nominal(M, psets, standard: bool):
    for pset, vals in psets.items():
        if pset.startswith("Pset_") != standard:
            continue
        for k, val in vals.items():
            if k.lower() in DIA_PROPS and val not in (None, ""):
                try:
                    n = float(str(val).replace(",", "."))
                except ValueError:
                    continue
                mm = n * M.len_scale * 1000 if isinstance(val, float) and n < 5 else n
                if mm >= 5:          # "1" is a template default, not a diameter
                    return f"{mm:g} mm", mm, f"property {pset}.{k}"
    return None


def _diameter(M, e):
    """(label, mm or None, source)"""
    import ifcopenshell.util.element as E
    psets = E.get_psets(e)
    got = _nominal(M, psets, True)
    if got:
        return got
    for pset, vals in psets.items():
        for k, val in vals.items():
            if k.lower() == "size" and isinstance(val, str):
                m = re.search(r"(\d+(?:[.,]\d+)?)\s*mm", val.replace("�", " "))
                if m:
                    mm = float(m.group(1).replace(",", "."))
                    return f"{mm:g} mm", mm, f"property {pset}.Size ('{val}')"
                m = re.search(r"\bdn\s*(\d+)", val.lower())
                if m:
                    return f"DN {m.group(1)}", float(m.group(1)), f"property {pset}.Size ('{val}')"
    got = _nominal(M, psets, False)
    if got:
        return got
    for pset, vals in psets.items():
        for k, val in vals.items():
            if k.lower() in OUT_PROPS and isinstance(val, (int, float)) and val > 0:
                mm = float(val) * M.len_scale * 1000
                return f"outer {mm:.0f} mm", mm, f"property {pset}.{k} (outer diameter)"
    return "diameter not recorded", None, ""


def _system(e):
    import ifcopenshell.util.element as E
    for g in (E.get_groups(e) if hasattr(E, "get_groups") else []):
        if g.is_a("IfcSystem") and g.Name:
            return str(g.Name), "IfcSystem assignment"
    t = E.get_type(e)
    nm = str(getattr(t, "Name", None) or getattr(e, "ObjectType", None) or "")
    parts = [p for p in nm.split(":") if p.strip()]
    if len(parts) >= 2 and IS_PIPE.search(parts[0]):
        return parts[1].strip(), "system word of the type name"
    return "(no system recorded)", ""


def answer(ctx, q, M, path):
    from .answer import Answer, abstain, estimate
    pp = q.extra["piping"]
    segs, seg_how = pipe_segments(M)
    if pp["kind"] == "fittings":
        fits, fit_how = pipe_fittings(M, segs)
        if not fits:
            a = abstain("ifc", "cannot prove: the model has no pipe fittings (no IfcPipeFitting, no IfcFlowFitting connected to a pipe)")
            a.final = True
            return a
        groups: dict = {}
        for e in fits:
            import ifcopenshell.util.element as E
            t = E.get_type(e)
            k = str(getattr(t, "Name", None) or e.ObjectType or e.Name or "(unnamed)")
            k = re.sub(r":\d+$", "", k)
            groups.setdefault(k, []).append(e)
        glist = sorted(({"type": k, "count": len(v)} for k, v in groups.items()), key=lambda g: -g["count"])
        items = [{"guid": e.GlobalId, "name": getattr(e, "Name", None)} for e in fits]
        items.append({"groups": glist, "total": len(fits)})
        lines = "; ".join(f"{g['type']}: {g['count']}" for g in glist[:10]) + ("; ..." if len(glist) > 10 else "")
        ans = Answer(value=len(fits), unit="count", route="ifc", confidence=0.9, items=items,
                     explanation=f"{len(fits)} pipe fittings = {len(fits)} x {fit_how} in {path.name}. By type: {lines}.")
        if "connected through ports" not in fit_how and "IfcPipeFitting" != fit_how:
            estimate(ans, f"fittings are {fit_how}: the model does not link them to pipes explicitly")
        _evidence(ctx, M, ans, [e for e in fits], q)
        return ans
    if not segs:
        a = abstain("ifc", "cannot prove: the model has no pipe segments (no IfcPipeSegment, and no IfcFlowSegment of a pipe type)")
        a.final = True
        return a
    rows = []
    for e in segs:
        L, src, comp = _length(M, e)
        d, mm, dsrc = _diameter(M, e)
        sysn, ssrc = _system(e)
        rows.append({"e": e, "L": L, "src": src, "comp": comp, "dia": d, "dia_src": dsrc, "sys": sysn, "sys_src": ssrc})
    if pp["kind"] == "segments":
        items = [{"guid": r["e"].GlobalId, "name": getattr(r["e"], "Name", None)} for r in rows]
        ans = Answer(value=len(rows), unit="count", route="ifc", confidence=0.9, items=items,
                     explanation=f"{len(rows)} pipe segments = {len(rows)} x {seg_how} in {path.name}.")
        _evidence(ctx, M, ans, segs, q)
        return ans
    sysname, sysrx = system_asked(ctx.q.question)
    sys_note = ""
    if sysname:
        keep = [r for r in rows if re.search(sysrx, fold(r["sys"]))]
        if not keep:
            a = abstain("ifc", f"cannot prove: the question names the {sysname} system, but no pipe system of the model matches it "
                               f"(systems: {', '.join(sorted({r['sys'] for r in rows}))})")
            a.final = True
            return a
        direct = {"cold water", "hot water", "waste", "vent", "storm"}
        sys_note = (f"{sysname} read as the pipe systems {', '.join(sorted({r['sys'] for r in keep}))} "
                    f"({len(keep)} of {len(rows)} segments)")
        rows = keep
    missing = [r for r in rows if r["L"] is None]
    if missing:
        return abstain("ifc", f"{len(missing)} pipe segment(s) have neither a length quantity nor geometry")
    total = sum(r["L"] for r in rows)
    comp = [r for r in rows if r["comp"]]
    # the read lengths against the geometry of the same segments (a sample of up to 200)
    checks, tier_why = [], []
    read = [r for r in rows if not r["comp"]]
    if read:
        step = max(len(read) // 200, 1)
        pairs = [(r["L"], _geom_length_safe(M, r["e"])) for r in read[::step]]
        pairs = [(a, b) for a, b in pairs if b]
        if pairs:
            sa, sb = sum(a for a, _ in pairs), sum(b for _, b in pairs)
            ok = abs(sa - sb) <= 0.05 * sb
            checks.append({"name": "pipe length vs geometry", "status": "pass" if ok else "fail",
                           "detail": f"{len(pairs)} sampled segments: read {sa:.2f} m, geometry {sb:.2f} m"})
            if not ok:
                tier_why.append(f"the read lengths differ from the segments' geometry by {abs(sa - sb) / sb:.0%} on a sample")
    if comp:
        tier_why.append(f"{len(comp)} of {len(rows)} segment(s) have no length in the model: computed from the geometry")
    key = {"system": "sys", "diameter": "dia"}.get(pp["by"] or "", None)
    groups = {}
    for r in rows:
        g = groups.setdefault((r["sys"], r["dia"]), {"system": r["sys"], "diameter": r["dia"], "segments": 0, "length_m": 0.0})
        g["segments"] += 1
        g["length_m"] += r["L"]
    by_sys, by_dia = {}, {}
    for r in rows:
        by_sys[r["sys"]] = by_sys.get(r["sys"], 0.0) + r["L"]
        by_dia[r["dia"]] = by_dia.get(r["dia"], 0.0) + r["L"]
    def fmt(d):
        return "; ".join(f"{k}: {v:.2f} m" for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:12])
    srcs = sorted({r["src"] for r in rows})
    items = [{"guid": r["e"].GlobalId, "name": getattr(r["e"], "Name", None), "length_m": round(r["L"], 3), "length_source": r["src"],
              "system": r["sys"], "diameter": r["dia"]} for r in rows]
    items.append({"totals": {"total_m": round(total, 2), "segments": len(rows),
                             "by_system_m": {k: round(v, 2) for k, v in by_sys.items()},
                             "by_diameter_m": {k: round(v, 2) for k, v in by_dia.items()}}})
    head = {"system": f"By system: {fmt(by_sys)}.", "diameter": f"By diameter: {fmt(by_dia)}."}.get(pp["by"] or "", "")
    both = f"By system: {fmt(by_sys)}. By diameter: {fmt(by_dia)}." if not head else head + f" (Also by {'diameter' if pp['by'] == 'system' else 'system'}: {fmt(by_dia if pp['by'] == 'system' else by_sys)}.)"
    ans = Answer(value=round(total, 2), unit="m", route="ifc", confidence=0.9, items=items, checks=checks,
                 explanation=(f"{total:.2f} m of pipe = sum of the lengths of {len(rows)} pipe segments ({seg_how}); length "
                              f"{' / '.join(srcs)}, model unit {M.len_scale:g} m. {both} Source: {path.name}."))
    if sys_note:
        ans.explanation = sys_note[0].upper() + sys_note[1:] + ". " + ans.explanation
        if sysname not in direct:
            tier_why.append(f"'{sysname}' is an interpretation: {sys_note}")
    if tier_why:
        estimate(ans, "; ".join(tier_why))
    _evidence(ctx, M, ans, [r["e"] for r in rows], q)
    return ans


def _evidence(ctx, M, ans, els, q):
    """Plan of the storey with most of the highlighted elements: the storey's walls and spaces grey, the pipes or fittings
    coloured, no per-element labels (hundreds of segments)."""
    if not ctx.evidence:
        return
    try:
        from shapely.ops import unary_union
        from .evidence import PALETTE, VectorCanvas, evidence_path, finish
        from .routes import ifc_route as R
        cnt: dict = {}
        for e in els:
            st = M.storey_of(e)
            cnt[st] = cnt.get(st, 0) + 1
        st = max(cnt, key=cnt.get) if cnt else None
        on = [e for e in els if M.storey_of(e) == st]
        ctx_els = [e for cls in ("IfcWall", "IfcSpace") for e in M.m.by_type(cls) if M.storey_of(e) == st][:2000]
        fps = [(e, M.footprint(e)[0]) for e in ctx_els]
        hl = [M.footprint(e)[0] for e in on]
        geoms = [g for _, g in fps if g is not None] + [g for g in hl if g is not None]
        if not geoms:
            return
        cv = VectorCanvas(unary_union(geoms).bounds)

        def draw(g, **kw):
            for p in getattr(g, "geoms", [g]):
                if hasattr(p, "exterior"):
                    cv.poly(list(p.exterior.coords), **kw)
        for e, g in fps:
            if g is not None:
                draw(g, fill=(235, 235, 235, 255) if e.is_a("IfcWall") else None, outline=(170, 170, 170), width=1)
        col = PALETTE[0]
        for g in hl:
            if g is not None:
                draw(g, fill=col + (140,), outline=col, width=2)
        out = evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question)
        shown = sum(1 for g in hl if g is not None)
        line = (f"Answer: {ans.value} {ans.unit}   route: ifc (cost €0)")
        ans.evidence = finish(cv.img, out, ctx.q.question, line,
                              note=f"{len(els)} elements in the model; {shown} highlighted on storey {st.Name if st is not None else '-'}.")
    except Exception:
        ans.evidence = None


def text_answer(ctx, query):
    """Text-route entry (router.text_routes): answers a numeric piping question for a plan sheet or model that has an IFC model."""
    from .answer import abstain
    from .routes import ifc_route as R
    pp = parse_piping(ctx.q.question)
    if pp is None:
        return abstain("piping", "not a numeric piping question")
    path = ctx.model_ifc()
    if path is None:
        a = abstain("piping", "cannot prove: piping is read from IFC pipe objects; this drawing has no IFC model (pipes on a PDF or scan are not measured)")
        return a
    M = R.load(str(path))
    query.extra["piping"] = pp
    return answer(ctx, query, M, path)
