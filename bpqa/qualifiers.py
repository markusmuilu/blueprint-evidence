"""Qualifiers that narrow the counted object ("fire doors", "sliding doors", "load-bearing walls").

The parser (`query.attach_qualifier`) records them in `Query.extra["qualifiers"]`. This module is the
other half of the rule "never silently drop a qualifier":

- `cannot_honour(route, query)`: the router asks it before calling a route. A route that cannot filter by
  a qualifier is skipped instead of answering the unfiltered total as if the qualifier were absent.
- `filter_ifc(...)`: the IFC route's filters (FireRating, OperationType, LoadBearing; IsExternal stays in
  ifc_route). They narrow the elements before they are counted or summed, so the evidence boxes only the
  matching ones, and the explanation states the filter.
- When no route can honour a qualifier the router ends with a final abstention (no model guesses).
"""
from __future__ import annotations

import re
from collections import Counter

# what each qualifier needs from the drawing, for the abstention text
NEEDS = {
    "fire": "fire ratings", "external": "whether an element is exterior or interior",
    "internal": "whether an element is exterior or interior", "sliding": "the opening type (sliding or swing)",
    "double": "single or double leaf", "glazed": "glazing", "accessible": "accessibility",
    "emergency": "which doors are emergency exits", "entrance": "which doors are entrance doors", "loadbearing": "the load-bearing flag",
    "material": "the element material", "orientation": "facade orientation",
}
# qualifier kinds each route filters by (routes are named by their module)
HONOURS = {"ifc_route": {"fire", "external", "internal", "sliding", "double", "loadbearing", "emergency", "entrance",
                          "material"}}
# the typed-lookup routes after the numeric ones read interior / exterior themselves (IsExternal, layer names)
FALLBACK_HONOURS = {"external", "internal"}
SOURCES = {"dxf_swiss": "CAD drawing", "dxf_generic": "CAD drawing", "pdf_text": "sheet text",
           "pdf_vector": "vector drawing", "pdf_generic": "vector drawing", "raster": "scan",
           "raster_rooms": "scan", "detector": "scan", "ifc_route": "model"}


def qualifiers(query) -> list[dict]:
    return list(query.extra.get("qualifiers") or [])


def kinds(query) -> list[str]:
    return [x["kind"] for x in qualifiers(query)]


def route_module(fn) -> str:
    return getattr(fn, "__module__", "route").rsplit(".", 1)[-1]


def describe(query, only: list[str] | None = None) -> str:
    return ", ".join(f"'{x['raw']}' ({NEEDS.get(x['kind'], x['kind'])})" for x in qualifiers(query)
                     if only is None or x["kind"] in only)


def cannot_honour(fn, query) -> str | None:
    """Reason a route must not answer this query (it cannot filter by a qualifier), else None."""
    mod = route_module(fn)
    missing = [k for k in kinds(query) if k not in HONOURS.get(mod, set())]
    if not missing:
        return None
    return (f"cannot prove: the question narrows the {query.obj}s by {describe(query, missing)}, which this "
            f"{SOURCES.get(mod, 'route')} does not record, so a {query.obj} count would ignore the filter")


def blocks_fallback(query) -> bool:
    """True when the typed-lookup and model fallbacks cannot honour every qualifier either."""
    return any(k not in FALLBACK_HONOURS for k in kinds(query))


def final_abstention(skipped: str, reasons: list[str], query):
    """The abstention when no route could honour the qualifier: the router stops here, no model guesses."""
    from .answer import abstain
    why = skipped or f"cannot prove: no route can filter the {query.obj}s by {describe(query)}"
    a = abstain("qualifier", "; ".join([why] + [r for r in reasons if r not in why]))
    a.final = True
    return a


# ---- IFC filters ---------------------------------------------------------------------------------------
NONE_VALUES = {"-", "--", "none", "n/a", "na", "no", "nil", "false", "0", "not rated", "unrated", "undefined",
               "notdefined", "unknown"}


def _props():
    from .routes import ifc_props     # lazy: ifc_props imports the IFC route
    return ifc_props


def fire_value(e) -> str | None:
    """The fire rating the element or its type carries in a property set, else None."""
    P = _props()
    for ps, k, v in P.flat_props(e):
        if P.FIRE.search(str(k)) and v not in (None, "", " ") and not P.placeholder(k, v) \
                and str(v).strip().lower() not in NONE_VALUES:
            return str(v).strip()
    return None


def operation_values(e) -> list[str]:
    """OperationType / PartitioningType of the element or its type, plus property-set equivalents."""
    vals = []
    holders = [e]
    try:
        import ifcopenshell.util.element as E
        t = E.get_type(e)
        if t is not None:
            holders.append(t)
    except Exception:
        pass
    for x in holders:
        for attr in ("OperationType", "PartitioningType"):
            v = getattr(x, attr, None)
            if v:
                vals.append(str(v))
    for ps, k, v in _props().flat_props(e):
        if re.sub(r"[\s_]", "", str(k)).lower() in ("operationtype", "operation", "partitioningtype") \
                and isinstance(v, str) and v.strip():
            vals.append(v)
    return [v for v in vals if v.upper() not in ("NOTDEFINED", "USERDEFINED", "")]


EXIT_KEY = re.compile(r"fire ?exit|emergency|escape|notausgang|fluchtweg", re.I)
ENTRANCE_KEY = re.compile(r"entrance|haupteingang|entry ?door", re.I)
ENTRANCE_NAME = re.compile(r"entrance|entry door|front door|main door|haustur|haustuer|eingang|sisaankaynti|sisaankaynt|"
                           r"paaovi|etuovi|porte d.entree|porte principale|ingresso", re.I)


def _truthy(v) -> bool:
    return v is True or str(v).strip().lower() in ("true", "yes", "ja", "1", "x", "vrai", "oui")


SLIDING = re.compile(r"slid|schiebe|liuku|coulis|skjut", re.I)
DOUBLE = re.compile(r"double|doppel|dubbel|two[_ -]?(panel|leaf)|2[_ -]?(fl|leaf|panel)", re.I)


def _names(e) -> str:
    return f"{getattr(e, 'Name', '') or ''} {_props().type_name(e)}"


# material word in the question -> pattern on the model's material / type names
MATERIAL_RX = [(r"concrete|beton", r"beton|concrete|betong|\bC\d{2}/\d{2}"),
               (r"steel|stahl|teras|acier", r"stahl|steel|teras|stal\b|acier|\bS\d{3}\b"),
               (r"wood|timber|holz|puu|bois|trae|tra", r"holz|wood|timber|puu|bois|trae|\btra\b|kiefer|fichte|eiche|pine|oak"),
               (r"brick|tegel|masonry|brique", r"brick|ziegel|tegel|tiili|masonry|mauerwerk|brique")]


def material_names(e) -> str:
    """The element's material names (layers, constituents, profiles) and its type name, one string."""
    names = []
    try:
        import ifcopenshell.util.element as E
        for m in E.get_materials(e) or []:
            if getattr(m, "Name", None):
                names.append(str(m.Name))
        t = E.get_type(e)
        if t is not None:
            for m in E.get_materials(t) or []:
                if getattr(m, "Name", None):
                    names.append(str(m.Name))
    except Exception:
        pass
    if names:
        names.append(_props().type_name(e))
    return " ".join(names)


def filter_ifc(M, ents, q, storey):
    """Apply the non-exterior qualifiers to `ents` (already narrowed to the storey).

    Returns (entities, note, tier_reason, why). `why` is a final abstention reason (the model was read and
    does not record the property); `note` states the filter for the explanation; `tier_reason` is set when
    the filter rests on type names rather than a property (the answer is then an estimate).
    """
    want = [k for k in kinds(q) if k not in ("external", "internal")]
    if not want:
        return ents, "", "", None
    where = f" on storey {storey.Name}" if storey is not None else " in the model"
    label = q.obj
    everything = M.entities(q.obj)
    notes, reason = [], ""
    for kind in want:
        total = len(ents)
        if kind == "fire":
            if not any(fire_value(e) for e in everything):
                return ents, "", "", (f"cannot prove: no {label} in this model carries a fire rating "
                                      f"({len(everything)} {label}s in the model, none with a FireRating or equivalent property)")
            vals = [(e, fire_value(e)) for e in ents]
            ents = [e for e, v in vals if v]
            top = Counter(v for _, v in vals if v).most_common(3)
            notes.append(f"{len(ents)} of {total} {label}s with a FireRating property"
                         + (f" ({', '.join(v for v, _ in top)})" if top else ""))
        elif kind == "emergency":
            def exit_flag(e):
                for ps, k, v in _props().flat_props(e):
                    if EXIT_KEY.search(str(k)) and v is not None:
                        return _truthy(v)
                return None
            flags = [exit_flag(e) for e in everything]
            if all(f is None for f in flags):
                return ents, "", "", (f"cannot prove: no {label} in this model carries a fire-exit or emergency-exit property "
                                      f"(Pset_DoorCommon.FireExit or similar); {len(everything)} {label}s in the model")
            ents = [e for e in ents if exit_flag(e)]
            notes.append(f"{len(ents)} of {total} {label}s with a FireExit / emergency-exit property = True "
                         f"(set on {sum(f is not None for f in flags)} of {len(everything)} {label}s in the model)")
        elif kind == "entrance":
            def entrance_flag(e):
                for ps, k, v in _props().flat_props(e):
                    if ENTRANCE_KEY.search(str(k)) and v is not None:
                        return _truthy(v)
                return None
            if any(entrance_flag(e) is not None for e in everything):
                ents = [e for e in ents if entrance_flag(e)]
                notes.append(f"{len(ents)} of {total} {label}s with an entrance property = True")
            elif any(ENTRANCE_NAME.search(_names(e)) for e in everything):
                ents = [e for e in ents if ENTRANCE_NAME.search(_names(e))]
                notes.append(f"{len(ents)} of {total} {label}s whose name or type name reads as an entrance door")
                reason = f"no {label} carries an entrance property; entrance doors were read from element and type names"
            elif any(M.is_external(e) is not None for e in everything):
                ents = [e for e in ents if M.is_external(e)]
                notes.append(f"{len(ents)} of {total} {label}s with IsExternal = True")
                reason = ("the model does not mark entrance doors; the exterior doors (IsExternal) are shown as the "
                          "closest recorded property and include doors that are not entrances")
            else:
                return ents, "", "", (f"cannot prove: this model marks no entrance doors (no entrance property, no door name "
                                      f"that says entrance, no IsExternal value); {len(everything)} {label}s in the model")
        elif kind == "loadbearing":
            P = _props()

            def lb(e):
                for ps, k, v in P.flat_props(e):
                    if P.LOADBEAR_KEY.match(str(k)) and v is not None:
                        return bool(v)
                return None
            if not any(lb(e) is not None for e in everything):
                return ents, "", "", (f"cannot prove: no {label} in this model has a LoadBearing property "
                                      f"({len(everything)} {label}s in the model)")
            ents = [e for e in ents if lb(e)]
            notes.append(f"{len(ents)} of {total} {label}s with LoadBearing = True")
        elif kind in ("sliding", "double"):
            rx = SLIDING if kind == "sliding" else DOUBLE
            if any(operation_values(e) for e in everything):
                ents = [e for e in ents if any(rx.search(x) for x in operation_values(e))]
                notes.append(f"{len(ents)} of {total} {label}s with a {kind} OperationType/PartitioningType")
            elif any(rx.search(_names(e)) for e in everything):
                ents = [e for e in ents if rx.search(_names(e))]
                notes.append(f"{len(ents)} of {total} {label}s whose name or type name reads as {kind}")
                reason = (f"no {label} records an operation type; '{kind}' was read from element and type names")
            else:
                return ents, "", "", (f"cannot prove: this model records no operation type for its {label}s and no "
                                      f"{label} name says {kind} ({len(everything)} {label}s in the model)")
        elif kind == "material":
            raw = next((x["raw"] for x in qualifiers(q) if x["kind"] == "material"), "")
            rx = next((r for k, r in MATERIAL_RX if re.match(k, raw)), None)
            if rx is None:
                return ents, "", "", (f"cannot prove: the question narrows the {label}s by {describe(q, [kind])}, which the "
                                      f"model reader does not match ({total} {label}s{where} in total)")
            if not any(material_names(e) for e in everything):
                return ents, "", "", (f"cannot prove: no {label} in this model records a material "
                                      f"({len(everything)} {label}s in the model)")
            ents = [e for e in ents if re.search(rx, material_names(e), re.I)]
            notes.append(f"{len(ents)} of {total} {label}s whose material or type name reads as {raw}")
            reason = f"the material '{raw}' was matched by name against the model's material and type names"
        else:
            return ents, "", "", (f"cannot prove: the question narrows the {label}s by {describe(q, [kind])}, which the "
                                  f"model reader does not filter by ({total} {label}s{where} in total)")
    return ents, "; ".join(notes) + where, reason, None
