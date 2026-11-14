"""IFC property route (`ifc-props`): free-text and property questions answered by typed lookups.

"Which door types are installed, with quantities?", "What is the fire rating of door D-12?", "Which
rooms are on level 2?", "What materials are the exterior walls made of?". The question is mapped to
one lookup over the model; every value in the answer is read from the file and cited by entity:

  plan(question, model) -> Lookup {intent, classes, storey, room, external, attr, k, by_level}
  run(lookup, model)    -> rows [{key, value, guids}] and the answer text built only from those rows

Intents: type breakdown (counts, shares, optional per level), materials by type, fire rating (or
its proven absence), a property of one named element, extremes (widest door, largest room, tallest
ceiling), totals of a quantity with a per-type breakdown, spaces on a level / in the model,
storey list and heights, model metadata (schema, authoring application), a property's absence
("load-bearing" when no element carries LoadBearing).

The rule mapper runs first. Only when it finds no lookup and Nemotron is enabled, the model is asked
to choose a lookup from a closed schema built from the model's own class, storey and property
names (`map_with_nemotron`); it never supplies a value. Words in the question that name classes,
storeys and rooms are matched against the names in the file, not against a fixed list.
"""
from __future__ import annotations

import json
import re
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, field

import ifcopenshell.util.element as E

from ..answer import Answer, abstain
from ..query import fold, whole_model_asked
from .ifc_route import load, norm, sheet_storey, space_display

ROUTE = "ifc-props"

# question word -> IFC classes (first class that exists in the schema and has entities wins as a group)
CLASS_WORDS: list[tuple[str, list[str], str]] = [
    (r"curtain ?walls?", ["IfcCurtainWall"], "curtain wall"),
    (r"pipe fittings?", ["IfcPipeFitting"], "pipe fitting"),
    (r"pipes?|piping|pipe segments?", ["IfcPipeSegment"], "pipe"),
    (r"duct fittings?", ["IfcDuctFitting"], "duct fitting"),
    (r"ducts?|duct segments?", ["IfcDuctSegment"], "duct"),
    (r"air terminals?|diffusers?", ["IfcAirTerminal"], "air terminal"),
    (r"valves?", ["IfcValve"], "valve"),
    (r"pumps?", ["IfcPump"], "pump"),
    (r"light(ing)? fixtures?|luminaires?|lamps?", ["IfcLightFixture"], "light fixture"),
    (r"sanitary|toilets?|wcs?|washbasins?|sinks?", ["IfcSanitaryTerminal"], "sanitary terminal"),
    (r"furniture|furnishings?", ["IfcFurniture", "IfcFurnishingElement"], "furniture"),
    (r"doors?|turen|tueren|ovi|ovet", ["IfcDoor"], "door"),
    (r"windows?|fenster|ikkuna", ["IfcWindow"], "window"),
    (r"walls?|wande|waende|seina", ["IfcWall"], "wall"),
    (r"floor slabs?|slabs?|decken", ["IfcSlab"], "slab"),
    (r"roofs?", ["IfcRoof"], "roof"),
    (r"columns?|stutzen|pillars?", ["IfcColumn"], "column"),
    (r"beams?|trager", ["IfcBeam"], "beam"),
    (r"members?", ["IfcMember"], "member"),
    (r"plates?", ["IfcPlate"], "plate"),
    (r"footings?|foundations?", ["IfcFooting"], "footing"),
    (r"stairs?|staircases?|treppen?", ["IfcStair"], "stair"),
    (r"ramps?", ["IfcRamp"], "ramp"),
    (r"railings?|handrails?|gelander", ["IfcRailing"], "railing"),
    (r"coverings?|ceilings?|claddings?", ["IfcCovering"], "covering"),
    (r"rooms?|spaces?|raume|tilat?", ["IfcSpace"], "space"),
]
FIRE = re.compile(r"fire ?rating|fire ?resist|feuerwiderstand|brandklasse|brannklasse|brandschutz|"
                  r"fireproof|palo(luokka|osasto)|ei\d{2}", re.I)
FIRE_Q = re.compile(r"fire ?rat|fire ?resist|fire ?protect|feuerwiderstand|brandschutz|palo")
LOADBEAR_KEY = re.compile(r"^(loadbearing|tragend|baerende|kantava)$", re.I)
# intents whose mapping from the question is narrow (one element, one attribute, the storey list): "proven";
# broad keyword-mapped intents (type breakdowns, property search, systems) answer as "estimate"
SHEET_SCOPED = {"types", "spaces", "total", "stats", "extreme", "search", "absent", "materials", "fire"}
PROVEN_INTENTS = {"extreme", "storeys", "fire", "element", "meta", "project"}
NUM_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "ten": 10}


@dataclass
class Lookup:
    intent: str                      # types | materials | fire | element | extreme | total | spaces | storeys | meta | absent
    classes: list = field(default_factory=list)
    label: str = ""                  # "door", "wall" ... for the answer text
    storey: object = None
    storey_note: str = ""
    room: object = None
    external: bool | None = None
    attr: str | None = None          # width | height | area | volume | length | thickness | ceiling_height
    order: str = "max"               # extreme: max | min
    k: int = 1
    by_level: bool = False
    group: str = "type"              # spaces: type | function | none
    element: object = None
    prop: str | None = None
    mapper: str = "rules"
    cost: float = 0.0

    def describe(self) -> str:
        bits = [self.intent, "/".join(self.classes) or "-"]
        if self.storey is not None:
            bits.append(f"storey {self.storey.Name}")
        if self.external is not None:
            bits.append("exterior" if self.external else "interior")
        if self.attr:
            bits.append(f"{self.order} {self.attr}")
        if self.room is not None:
            bits.append(f"room {space_display(self.room)}")
        return ", ".join(bits)


# ------------------------------------------------------------------ model helpers

def ents(M, classes):
    out = []
    for cls in classes:
        try:
            out += M.m.by_type(cls)
        except RuntimeError:
            continue
    if "IfcSanitaryTerminal" in classes and not out:       # IFC2X3: flow terminals typed as sanitary
        try:
            out = [e for e in M.m.by_type("IfcFlowTerminal")
                   if (t := E.get_type(e)) is not None and t.is_a("IfcSanitaryTerminalType")]
        except RuntimeError:
            pass
    if "IfcPipeSegment" in classes and not out:             # IFC2X3: pipes are IfcFlowSegment of a pipe type (bpqa/piping.py)
        from .. import piping
        out = list(piping.pipe_segments(M)[0])
    if "IfcPipeFitting" in classes and not out:
        from .. import piping
        out = list(piping.pipe_fittings(M, piping.pipe_segments(M)[0])[0])
    if "IfcRoof" in classes:
        try:
            out += [e for e in M.m.by_type("IfcSlab") if getattr(e, "PredefinedType", None) == "ROOF"]
        except RuntimeError:
            pass
    if "IfcStair" in classes:
        out = [e for e in out if not (E.get_aggregate(e) and E.get_aggregate(e).is_a("IfcStair"))]
    seen, uniq = set(), []
    for e in out:
        if e.id() not in seen:
            seen.add(e.id())
            uniq.append(e)
    return uniq


def type_name(e) -> str:
    t = E.get_type(e)
    if t is not None and t.Name:
        return t.Name
    ot = getattr(e, "ObjectType", None)
    if ot:
        return ot
    return e.Name or "(no type name)"


def psets(e) -> dict:
    try:
        return E.get_psets(e)
    except Exception:
        return {}


def placeholder(k, v) -> bool:
    """Exporters write the property's own name as its value when it is empty ('Fire Rating')."""
    n = lambda x: re.sub(r"[\s_\-]", "", fold(str(x)))
    return n(v) == n(k)


def flat_props(e):
    """(pset, name, value) for every simple property and quantity of the element and its type."""
    for ps, vals in psets(e).items():
        for k, v in vals.items():
            if k == "id":
                continue
            if isinstance(v, dict) and "properties" in v:        # complex property
                for k2, v2 in (v.get("properties") or {}).items():
                    if not isinstance(v2, dict):
                        yield f"{ps}.{k}", k2, v2
            elif not isinstance(v, dict):
                yield ps, k, v


def common_flag(M, e, key):
    for ps, vals in psets(e).items():
        if ps.endswith("Common") and key in vals and vals[key] is not None:
            return bool(vals[key])
    for ps, vals in psets(e).items():
        if key in vals and isinstance(vals[key], bool):
            return vals[key]
    return None


LAYER_KEYS = {"ebene", "layer", "layername", "layer name", "tag layer", "category", "kategorie"}
EXT_WORD = re.compile(r"aussen|außen|au.enw|exterior|external|outer|outside|ytter|utvendig|ulko|facade|fassade")
INT_WORD = re.compile(r"innen|interior|internal|inner|inside|innv|innerv|sisa|partition|trennw")


def layer_side(e):
    """Exterior/interior from the element's CAD layer or category name ('Aussenwaende',
    'A24_Innervegger'), used only when IsExternal is missing everywhere. Returns (flag, text)."""
    for ps, k, v in flat_props(e):
        if fold(str(k)).strip() in LAYER_KEYS and isinstance(v, str) and v.strip():
            fv = fold(v)
            ext, inn = bool(EXT_WORD.search(fv)), bool(INT_WORD.search(fv))
            if ext != inn:
                return ext, f"{k} '{v}'"
    return None, None


def qty(M, e, keys):
    v, src = M.qto(e, keys)
    return v, src


def width_of(M, e):
    v = getattr(e, "OverallWidth", None)
    if v:
        return float(v) * M.len_scale, "OverallWidth"
    v, src = qty(M, e, ["Width"])
    return (v * M.len_scale, src) if v is not None else (None, None)


def height_of(M, e):
    v = getattr(e, "OverallHeight", None)
    if v:
        return float(v) * M.len_scale, "OverallHeight"
    if e.is_a("IfcSpace"):
        v, src = qty(M, e, ["FinishCeilingHeight", "ClearHeight", "Height"])
    else:
        v, src = qty(M, e, ["Height"])
    if v is not None:
        return v * M.len_scale, src
    return None, None


def area_of(M, e):
    if e.is_a("IfcSpace"):
        return M.space_area(e)
    v, src = qty(M, e, ["NetArea", "GrossArea", "NetSideArea", "GrossSideArea", "Area"])
    return (v * M._area_scale(), src) if v is not None else (None, None)


def volume_of(M, e):
    v, src = qty(M, e, ["NetVolume", "GrossVolume", "Volume"])
    return (v * M._vol_scale(), src) if v is not None else (None, None)


def length_of(M, e):
    v, src = qty(M, e, ["Length"])
    return (v * M.len_scale, src) if v is not None else (None, None)


def thickness_of(M, e):
    try:
        usage = E.get_material(e, should_skip_usage=True)
    except Exception:
        usage = None
    if usage is not None and usage.is_a("IfcMaterialLayerSet"):
        t = sum(float(l.LayerThickness or 0) for l in usage.MaterialLayers)
        if t > 0:
            return t * M.len_scale, "material layer set"
    v, src = qty(M, e, ["Width", "Thickness", "Depth"])
    return (v * M.len_scale, src) if v is not None else (None, None)


ATTR_FN = {"width": width_of, "height": height_of, "ceiling_height": height_of, "area": area_of,
           "volume": volume_of, "length": length_of, "thickness": thickness_of}
ATTR_UNIT = {"width": "m", "height": "m", "ceiling_height": "m", "area": "sq m", "volume": "cu m",
             "length": "m", "thickness": "m"}


def materials_of(e):
    try:
        mat = E.get_material(e, should_skip_usage=True)
    except Exception:
        return []
    if mat is None:
        return []
    if mat.is_a("IfcMaterialLayerSet"):
        return [(l.Material.Name if l.Material else "?", l.LayerThickness) for l in mat.MaterialLayers]
    try:
        return [(m.Name, None) for m in E.get_materials(e)]
    except Exception:
        return []


# ------------------------------------------------------------------ question -> lookup

def _classes(f):
    for rx, classes, label in CLASS_WORDS:
        if re.search(rf"\b(?:{rx})\b", f):
            return classes, label
    return [], ""


def _ordinal_num(f):
    m = re.search(r"\b(?:level|floor|storey|story|etage|geschoss|kerros)\s+(\d{1,2})\b", f) or \
        re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:level|floor|storey|story)\b", f)
    if m:
        return int(m.group(1))
    for w, n in (("first", 1), ("second", 2), ("third", 3), ("fourth", 4), ("fifth", 5)):
        if re.search(rf"\b{w} (level|floor|storey|story)\b", f):
            return n
    return None


def find_storey(M, question):
    """Storey named in the question: its own name printed in the question first, then ground /
    basement / attic / level N words resolved against storey names and elevations. Returns
    (storey or None, how it was read, asked: bool)."""
    f = fold(question)
    named = []
    for s in M.storeys:
        for nm in {s.Name or "", getattr(s, "LongName", None) or ""}:
            n = fold(nm).strip()
            if len(n) >= 2 and re.search(rf"(?<![\w.]){re.escape(n)}(?![\w])", f):
                named.append((len(n), s, nm))
    if named:
        named.sort(key=lambda t: -t[0])
        return named[0][1], f"storey '{named[0][2]}' named in the question", True
    sts = sorted(M.storeys, key=lambda s: float(getattr(s, "Elevation", 0) or 0))
    if not sts:
        return None, "", False
    names = {s: fold(s.Name or "") for s in sts}
    elev = {s: float(getattr(s, "Elevation", 0) or 0) * M.len_scale for s in sts}

    def by_name(rx):
        hit = [s for s in sts if re.search(rx, names[s])]
        return hit[0] if len(hit) == 1 else None

    if re.search(r"\b(basement|underground|cellar|keller|kjeller|kellari)\b", f):
        s = by_name(r"keller|kjeller|basement|cellar|\bug\b|\bkg\b|kellari|sous-sol|untergeschoss")
        if s is None:
            below = [s for s in sts if elev[s] < -0.5]
            s = below[-1] if len(below) == 1 or below else None
        return s, (f"'basement' read as storey '{s.Name}'" if s else "basement asked, no storey named so"), True
    if re.search(r"\b(ground (floor|level)|erdgeschoss|ground)\b", f):
        s = by_name(r"erdgeschoss|\beg\b|ground|\bgf\b|\b00\b|level 0|\bl0\b|u\.etg|1\.krs|maantaso")
        if s is None:
            s = min(sts, key=lambda s: abs(elev[s]))
        return s, f"'ground floor' read as storey '{s.Name}' (elevation {elev[s]:.2f} m)", True
    if re.search(r"\b(attic|roof (level|floor)|dachgeschoss|loft)\b", f):
        s = by_name(r"dach|attic|roof|loft|takplan|ullakko")
        return s, (f"'attic' read as storey '{s.Name}'" if s else "attic asked, no storey named so"), True
    n = _ordinal_num(f)
    if n is not None:
        hit = [s for s in sts if re.search(rf"(?<!\d){n}(?!\d)", names[s])]
        if len(hit) == 1:
            return hit[0], f"level {n} read as storey '{hit[0].Name}' (the only storey name with {n})", True
        return None, f"level {n} asked; storey names: {', '.join(s.Name for s in sts)}", True
    return None, "", bool(re.search(r"\b(level|floor|storey|story)\b(?! area)", f)) and \
        not re.search(r"\b(each|every|all|per|by|across)\s+(the\s+)?(building\s+)?(level|floor|storey|story)", f)


def find_room(M, question):
    m = re.search(r"\b(?:room|space|raum|huone)\s+([A-Za-z]?\d[\w.\-]*)", question, re.I)
    if not m:
        return None
    key = norm(m.group(1).rstrip(".?,"))
    hits = [s for s in ents(M, ["IfcSpace"]) if key in {norm(s.Name), norm(getattr(s, "LongName", None))}]
    return hits[0] if len(hits) == 1 else None


def find_element(M, question, classes):
    """An element named in the question ('door D-12', 'window W03'): a token after the class word
    that equals an element's Name or Tag, or an element type name."""
    toks = re.findall(r"(?<![\w-])([^\W\d_]{0,12}[-.]?\d[\w.\-/]*)", question)
    toks = [t.rstrip(".") for t in toks]
    if not toks or not classes:
        return None
    pool = ents(M, classes)
    for t in toks:
        k = norm(t)
        hits = [e for e in pool if k in {norm(e.Name), norm(getattr(e, "Tag", None))}]
        if len(hits) == 1:
            return hits[0]
        if 1 < len(hits) <= 300:        # several elements carry the same name: answer for all of them
            return hits
    return None


SUBSCOPE = re.compile(r"\b(house|block|wing|apartment|unit|flat|duplex|tower|building)\s+[A-Z0-9]\b(?!\s*(?:m|mm|sq))")
# words a lookup above already understands; any other content word of 4+ letters is a property to search for
KNOWN = set("""what which how many much are is the there this that these those with their them they its and or of in on
for to by per from all any each every some list show give provide tell could would you please me can does do did
type types kind kinds sort sorts different various distinct quantity quantities number numbers count counts total
totals distribution distributed breakdown percentage percentages share shares used use installed present exist exists
existing available include includes including included contain contains building buildings model level levels floor
floors storey storeys story stories element elements component components item items detail details information
info data overview summary along also have has having been being find found where located location here within inside
outside interior exterior internal external inner outer side sides envelope facade facades partition partitions
respective respectively respect overall combined complete full entire whole main primary specific specified
grouped group groups organized organised organize across between both other others plus versus compared comparison
about related relating associated function functions functional usage purpose
square meters metres meter metre area areas volume volumes length lengths width widths height heights thickness
thicknesses dimension dimensions size sizes sizing value values property properties attribute attributes name names
named called labelled labeled identify identified applied apply applies employed employ fitted provided
specification specifications characteristic characteristics construction constructed structure structures structural
system systems standard standards material materials made built composed composition layers layer
ground basement underground attic first second third fourth fifth sixth range ranges ones equipment equipments
utilized utilised utilization utilisation required require requires needed need needs average averages find
rooms room spaces space type-wise usable gross net nett floorspace much more less than most least highest lowest largest smallest every
""".split())
ING_KEEP = {"glazing", "flooring", "skirting", "roofing", "cladding", "insulating", "lighting", "heating", "plumbing"}


def unknown_terms(f: str, M=None) -> list:
    if M is not None:
        for nm in _model_names(M):
            if len(nm) >= 3 and nm in f:
                f = f.replace(nm, " ")
    words = re.findall(r"[a-z][a-z\-]{3,}", f)
    out = []
    for w in words:
        if w in KNOWN or (w.endswith("ing") and w not in ING_KEEP):
            continue
        if any(re.fullmatch(rf"(?:{rx})", w) for rx, _, _ in CLASS_WORDS):
            continue
        if M is not None:
            _model_classes(M, "")
            toks = {t for cw, _ in M._class_words for t in cw.split()}
            if w in toks or w.rstrip("s") in toks or (w.endswith("es") and w[:-2] in toks):
                continue
        out.append(w)
    return list(dict.fromkeys(out))


def _model_names(M):
    if not hasattr(M, "_prop_names"):
        names = set()
        for s in M.storeys:
            names |= {fold(s.Name or ""), fold(getattr(s, "LongName", None) or "")}
        for sp in ents(M, ["IfcSpace"]):
            names |= {fold(sp.Name or ""), fold(getattr(sp, "LongName", None) or "")}
        M._prop_names = sorted((n.strip() for n in names if n.strip()), key=len, reverse=True)
    return M._prop_names


def find_room_by_name(M, question, storey):
    """A space whose LongName or Name (4+ characters) is printed in the question; unique, or unique on
    the asked storey."""
    f = fold(question)
    hits = []
    for sp in ents(M, ["IfcSpace"]):
        for nm in (getattr(sp, "LongName", None), sp.Name):
            n = fold(nm or "").strip()
            if len(n) >= 4 and re.search(rf"(?<![\w]){re.escape(n)}(?![\w])", f):
                hits.append((len(n), sp))
                break
    if storey is not None:
        hits = [h for h in hits if M.storey_of(h[1]) == storey]
    if not hits:
        return None
    best = max(h[0] for h in hits)
    top = [sp for n, sp in hits if n == best]
    return top[0] if len(top) == 1 else None


def _model_classes(M, f):
    """Classes named by the model itself: an IFC class present in the file whose name, split into words
    (IfcSpaceHeater -> 'space heater', IfcWasteTerminal -> 'waste terminal'), appears in the question,
    singular or plural. No word list to maintain: the file says which classes exist."""
    if not hasattr(M, "_class_words"):
        present = {e.is_a() for e in M.m.by_type("IfcProduct")}
        words = []
        for cls in present:
            w = re.sub(r"(?<!^)(?=[A-Z])", " ", cls[3:]).lower()
            if w in ("building element proxy", "opening element", "annotation", "virtual element", "space", "site",
                     "building", "building storey"):
                continue
            words.append((w, cls))
        M._class_words = sorted(words, key=lambda t: -len(t[0]))
    for w, cls in M._class_words:
        if re.search(rf"\b{re.escape(w)}(s|es)?\b", f):
            return [cls], w
    return [], ""


def plan(M, question) -> Lookup | None:
    f = fold(question)
    classes, label = _classes(f)
    if not classes:
        classes, label = _model_classes(M, f)
    storey, snote, asked_storey = find_storey(M, question)
    room = find_room(M, question) or find_room_by_name(M, question, storey)
    ext = None
    f = re.sub(r"\b(inside|within|in) (of )?(the |this )?(building|model|house)\b", " ", f)
    if re.search(r"\b(exterior|outside|external|outer|facade|envelope)\b", f) and not re.search(r"\b(interior|inside|internal|inner)\b", f):
        ext = True
    elif re.search(r"\b(interior|inside|internal|inner|partitions?)\b", f):
        ext = False
    by_level = bool(re.search(r"\b(by|per|across|each)\s+(building\s+)?(level|floor|storey|story)|distribut\w* (by|across|over) (the )?(different )?(building )?(level|floor|storey)", f))
    base = dict(classes=classes, label=label, storey=storey, storey_note=snote, room=room, external=ext, by_level=by_level)
    if asked_storey and storey is None:
        return Lookup(intent="unresolved", storey_note=snote)
    m = SUBSCOPE.search(question)
    if m:
        return Lookup(intent="unresolved", storey_note=f"'{m.group(0)}' names a part of the building that the model does not identify by that name")
    if re.search(r"\b(north|south|east|west)(ern)?\b", f):
        return Lookup(intent="unresolved", storey_note="orientation (north/south/east/west) is not resolved by this lookup")

    if re.search(r"\b(software|application|authoring|ifc (version|schema|standard)|modell?ing standards?|"
                 r"which standards?|ifc standards?)\b", f):
        return Lookup(intent="meta")
    if re.search(r"\b(project (information|info|name|data)|building name|location|coordinates|address|"
                 r"where is (it|the building) (situated|located)|building'?s? type|type of building|usage)\b", f) and \
            (not classes or classes == ["IfcSpace"]):
        return Lookup(intent="project")
    if re.search(r"\b(mep|hvac|heating|cooling|ventilation|plumbing|electrical|sanitary|drainage|water|lighting|"
                 r"fire protection|sprinkler)\b.*\b(systems?|components?|sub-systems?)\b|\bsystems? (types?|installed)\b", f):
        return Lookup(intent="systems", prop=" ".join(re.findall(r"\b(mep|hvac|heating|cooling|ventilation|plumbing|"
                                                                 r"electrical|sanitary|drainage|water|lighting|sprinkler)\b", f)))
    if re.search(r"\bstructural (elements?|components?|members?|system)\b", f) and not re.search(r"material", f):
        return Lookup(intent="structural", by_level=by_level)
    if re.search(r"\bheights?\b.*\b(levels|storeys|stories|floors)\b", f) and not classes:
        return Lookup(intent="storeys")
    if re.search(r"\b(load[- ]?bearing|tragend|structural walls?)\b", f) and "IfcWall" in classes:
        return Lookup(intent="absent", prop="LoadBearing", **base)
    if FIRE_Q.search(f) and classes:
        elem = find_element(M, question, classes)
        return Lookup(intent="fire", element=elem, **base)
    if re.search(r"\b(heights? of (each|every|the) (level|floor|storey)|storey heights?|level heights?|"
                 r"floor[- ]to[- ]floor|how many (levels|storeys|stories|floors)|which (levels|storeys|floors)|"
                 r"list (of )?(the )?(levels|storeys))\b", f):
        return Lookup(intent="storeys")
    named = find_element(M, question, classes) if classes and room is None else None
    if re.search(r"\bmaterials?\b|made of|what (is|are) .* built (of|from)", f) and classes:
        return Lookup(intent="materials", element=named, **base)
    if re.search(r"\bthickness(es)?\b", f) and classes and not unknown_terms(f, M):
        return Lookup(intent="types", attr="thickness", **base)
    if re.search(r"\b(dimensions?|sizes?|sizing|width and height|heights?|widths?)\b", f) and classes and named is None and \
            classes != ["IfcSpace"] and not re.search(r"\b(widest|narrowest|tallest|largest|smallest|greatest|biggest|"
                                                      r"highest|lowest|maximum|minimum|average|mean|top \d+)\b", f) \
            and not re.search(r"\bceilings?\b", f):
        return Lookup(intent="types", attr="dims", **base)
    stat_attr = next((a for rx, a in ((r"width", "width"), (r"height|ceiling", "height"), (r"area", "area"),
                                      (r"length", "length"), (r"volume", "volume")) if re.search(rx, f)), None)
    if re.search(r"\b(average|mean|typical)\b", f) and stat_attr and (classes or re.search(r"ceiling", f)):
        if re.search(r"ceiling", f):
            base.update(classes=["IfcSpace"], label="space")
        return Lookup(intent="stats", attr=stat_attr, **base)
    if re.search(r"\bceiling heights?\b|\bheights? of (the )?ceilings?\b", f) and room is None and \
            not re.search(r"\b(tallest|highest|lowest|largest|smallest|top)\b", f):
        base.update(classes=["IfcSpace"], label="space", by_level=True)
        return Lookup(intent="stats", attr="height", **base)
    # extremes: widest door, largest room, tallest ceilings, narrowest window
    ext_words = [(r"widest|greatest widths?|largest widths?|maximum width|biggest width", "width", "max"),
                 (r"narrowest|smallest widths?|minimum width", "width", "min"),
                 (r"tallest|highest ceilings?|highest ceiling heights?|greatest heights?", "height", "max"),
                 (r"lowest ceilings?|smallest heights?", "height", "min"),
                 (r"largest|biggest|greatest area", "area", "max"),
                 (r"smallest", "area", "min"),
                 (r"longest", "length", "max"), (r"shortest", "length", "min")]
    for rx, attr, order in ext_words:
        if re.search(rf"\b(?:{rx})\b", f) and classes:
            if attr == "width" and "width" not in f and re.search(r"\b(largest|biggest)\b", f) and classes != ["IfcSpace"]:
                attr = "area"
            if re.search(r"ceiling", f):
                attr, classes = "height", ["IfcSpace"]
                base.update(classes=classes, label="space")
            k = 10 if re.search(r"\b(which|what)\s+(\w+\s+)?(rooms|spaces|doors|windows|walls)\b", f) else 1
            m = re.search(r"\b(two|three|four|five|ten|\d{1,2})\s+(?:\w+\s+)?(?:doors|windows|rooms|spaces|walls|slabs)", f)
            if m:
                k = NUM_WORDS.get(m.group(1)) or int(m.group(1))
            return Lookup(intent="extreme", attr=attr, order=order, k=k, **base)
    prop = None
    for rx, p in ((r"width|wide", "width"), (r"height|high|tall", "height"), (r"area|big|large", "area"), (r"volume", "volume"),
                  (r"level|storey|floor", "storey"), (r"type", "type")):
        if re.search(rf"\b(?:{rx})\b", f):
            prop = p
            break
    if room is not None and re.search(r"\bceiling\b", f) and re.search(r"high|height|tall", f):
        base.update(classes=["IfcSpace"], label="space")
        return Lookup(intent="element", element=room, prop="height", **base)
    elem = find_element(M, question, classes) if classes and not room else None
    if elem is not None and not re.search(r"\bhow many\b", f):
        return Lookup(intent="element", element=elem, prop=prop, **base)
    if room is not None and classes and classes != ["IfcSpace"]:
        return Lookup(intent="types", **base)
    total_attr = None
    for rx, a in ((r"volume", "volume"), (r"length", "length"), (r"\b(area|square)", "area")):
        if re.search(rx, f):
            total_attr = a
            break
    if not classes and re.search(r"\b(floor area|gross area|net area|usable area|nettoflache|bruttoflache)\b", f):
        classes = ["IfcSpace"]
        base.update(classes=classes, label="space")
        base.update(by_level=base["by_level"] or bool(re.search(r"\b(level|storey|floor)s?\b.*\b(breakdown|by)\b|\bby (level|storey|floor)", f)))
    from ..takeoff import Q_TYPE_RE as _QT
    if not classes and any(re.search(rx, f) for rx in _QT.values()) and re.search(r"\bhow many\b|\bwhich\b|\blist\b|\barea", f):
        classes = ["IfcSpace"]
    if (classes == ["IfcSpace"] or (not classes and re.search(r"\bspaces?\b|\brooms?\b", f))) and \
            (not unknown_terms(f, M) or any(re.search(rx, f) for rx in _QT.values())):
        base.update(classes=["IfcSpace"], label="space")
        grp = "function" if re.search(r"function|usage|use\b|purpose|grouped", f) else "none"
        from ..takeoff import Q_TYPE_RE, GROUPS
        rtypes = [t for t, rx in Q_TYPE_RE.items() if re.search(rx, f)]
        if rtypes and room is None:
            want = []
            for t in rtypes:
                want += list(GROUPS.get(t, (t,)))
            return Lookup(intent="spaces", group=grp, attr="area", prop="types:" + ",".join(want), **dict(base, by_level=True))
        if room is not None:
            return Lookup(intent="element", element=room, prop=prop, **base)
        return Lookup(intent="spaces", group=grp, attr="area", **base)
    if total_attr and classes and re.search(r"\b(total|sum|combined|overall|how much)\b", f):
        return Lookup(intent="total", attr=total_attr, **base)
    terms = unknown_terms(f, M)
    if terms and classes:
        return Lookup(intent="search", prop=" ".join(terms), **base)
    if classes and re.search(r"^\s*(what|which)\b", f) and not terms:
        return Lookup(intent="types", **base)
    if classes and re.search(r"\btypes?\b|how many|quantit|distribution|breakdown|count|which .*(are|is) (used|installed|present)|"
                             r"what .*(are|is) (used|installed|present)|\bpresent\b|\binstalled\b|\bused\b|\blist\b", f):
        return Lookup(intent="types", **base)
    return None


# ------------------------------------------------------------------ lookup -> rows, text

def room_elements(M, sp, es):
    """Elements of `es` that belong to space `sp`: contained in it, bounding it (IfcRelSpaceBoundary),
    or, without either relation, whose footprint lies within 0.3 m of the space footprint on its storey."""
    ids = {e.id() for e in contained_in_space(M, sp)}
    for rel in getattr(sp, "BoundedBy", []) or []:
        if rel.RelatedBuildingElement is not None:
            ids.add(rel.RelatedBuildingElement.id())
    hit = [e for e in es if e.id() in ids]
    if hit:
        return hit, "contained in or bounding the space (IFC relations)"
    fp, _ = M.footprint(sp)
    if fp is None:
        return [], "space has no geometry"
    st = M.storey_of(sp)
    tol = 0.3 / (M.len_scale or 1.0)
    out = []
    for e in es:
        if M.storey_of(e) != st:
            continue
        g, _ = M.footprint(e)
        if g is not None and g.distance(fp) <= tol:
            out.append(e)
    return out, "footprint within 0.3 m of the space footprint (no IFC relation)"


def _filter(M, L, es):
    notes = []
    if L.room is not None and L.classes != ["IfcSpace"]:
        es, how = room_elements(M, L.room, es)
        notes.append(f"in space {space_display(L.room)}: {how}")
    if L.storey is not None:
        es = [e for e in es if M.storey_of(e) == L.storey]
        notes.append(f"contained in storey {L.storey.Name}")
    if L.external is not None:
        flags = [(e, common_flag(M, e, "IsExternal")) for e in es]
        if all(fl is None for _, fl in flags):
            side = [(e,) + layer_side(e) for e in es]
            if sum(1 for _, fl, _ in side if fl is not None) < 0.5 * max(len(es), 1):
                return None, "IsExternal is not set on these elements and their layer names do not say exterior/interior"
            es = [e for e, fl, _ in side if fl is L.external]
            ex = Counter(t for _, fl, t in side if fl is L.external).most_common(2)
            notes.append(f"{'exterior' if L.external else 'interior'} from the CAD layer name (IsExternal not set): "
                         + ", ".join(t for t, _ in ex))
        else:
            es = [e for e, fl in flags if fl is L.external]
            notes.append(f"Pset_*Common.IsExternal = {L.external}")
    return es, "; ".join(notes)


def _fmt(v, nd=2):
    return f"{v:,.{nd}f}".replace(",", "")


def run(M, L):
    """Returns (text, rows, cited entities, note) or raises LookupError(reason)."""
    if isinstance(L.element, list):
        group = L.element
        if L.intent in ("fire", "materials"):
            L.element = None
            L.storey = L.external = None
            L.classes = sorted({e.is_a() for e in group})
            es0 = group
        elif L.intent == "element":
            lab = L.label or group[0].is_a()
            rows = Counter()
            for e in group:
                st = M.storey_of(e)
                parts = [f"type {type_name(e)}"]
                if L.prop in (None, "width"):
                    w, _ = width_of(M, e)
                    parts.append(f"width {_fmt(w)} m" if w is not None else "width -")
                if L.prop in (None, "height"):
                    h, _ = height_of(M, e)
                    parts.append(f"height {_fmt(h)} m" if h is not None else "height -")
                if L.prop in (None, "storey") and st is not None:
                    parts.append(f"storey {st.Name}")
                rows[", ".join(parts)] += 1
            text = (f"{len(group)} {lab}s are named '{group[0].Name}' in the model (names are not unique); "
                    + "; ".join(f"{k}: {v}" for k, v in rows.most_common(12)))
            return text, [{"key": k, "count": v} for k, v in rows.most_common()], group, \
                "element attributes (OverallWidth/OverallHeight or quantity sets) of every element with that name"
    if L.intent == "unresolved":
        raise LookupError(L.storey_note or "storey in the question not found in the model")
    if L.intent == "meta":
        h = M.m.header
        fn, fd = h.file_name, h.file_description
        apps = sorted({f"{a.ApplicationFullName} {a.Version}".strip() for a in M.m.by_type("IfcApplication")})
        lines = [f"IFC schema: {M.m.schema}",
                 f"Originating system: {fn.originating_system or '-'}",
                 f"Preprocessor: {fn.preprocessor_version or '-'}"]
        if apps:
            lines.append("Applications in IfcOwnerHistory: " + "; ".join(apps))
        mvd = " ".join(x for x in (fd.description or []) if x)
        if mvd:
            lines.append(f"File description (model view): {mvd}")
        return "\n".join(lines), [], [], "file header and IfcApplication"
    if L.intent == "project":
        lines, cited = [], []
        for cls in ("IfcProject", "IfcSite", "IfcBuilding"):
            for e in M.m.by_type(cls):
                bits = [f"{cls[3:]}: {e.Name or '-'}"]
                for a in ("LongName", "Description", "ObjectType", "Phase"):
                    v = getattr(e, a, None)
                    if v:
                        bits.append(f"{a} {v}")
                if cls == "IfcSite":
                    for a in ("RefLatitude", "RefLongitude"):
                        v = getattr(e, a, None)
                        if v:
                            dms = list(v)
                            dec = dms[0] + (dms[1] if len(dms) > 1 else 0) / 60 + (dms[2] if len(dms) > 2 else 0) / 3600
                            bits.append(f"{a} {dec:.5f}")
                    if getattr(e, "RefElevation", None) is not None:
                        bits.append(f"RefElevation {e.RefElevation}")
                addr = getattr(e, "BuildingAddress", None) or getattr(e, "SiteAddress", None)
                if addr is not None:
                    parts = [*(getattr(addr, "AddressLines", None) or []), getattr(addr, "PostalCode", None),
                             getattr(addr, "Town", None), getattr(addr, "Region", None), getattr(addr, "Country", None)]
                    parts = [p for p in parts if p]
                    if parts:
                        bits.append("address " + ", ".join(parts))
                lines.append("; ".join(bits))
                cited.append(e)
        lines.append(f"Storeys: {len(M.storeys)}; spaces: {len(ents(M, ['IfcSpace']))}")
        return "\n".join(lines), [], cited, "IfcProject, IfcSite, IfcBuilding attributes and addresses"

    if L.intent == "systems":
        words = L.prop.split() if L.prop else []
        syss = []
        for cls in ("IfcDistributionSystem", "IfcSystem", "IfcBuildingSystem"):
            try:
                syss += [x for x in M.m.by_type(cls) if x not in syss]
            except RuntimeError:
                pass
        hit = []
        for x in syss:
            txt = fold(" ".join(str(getattr(x, a, "") or "") for a in ("Name", "ObjectType", "PredefinedType", "Description", "LongName")))
            if not words or "mep" in words or any(w[:5] in txt for w in words):
                n = sum(len(r.RelatedObjects) for r in getattr(x, "IsGroupedBy", []) or [])
                hit.append((x, n))
        dist = Counter(e.is_a() for e in M.m.by_type("IfcDistributionElement")) if _has(M, "IfcDistributionElement") else Counter()
        lines = []
        if hit:
            lines.append(f"{len(hit)} systems (IfcSystem) in the model" + (f" matching {', '.join(words)}" if words and 'mep' not in words else "") + ":")
            for x, n in sorted(hit, key=lambda t: -t[1])[:40]:
                pt = getattr(x, "PredefinedType", None) or getattr(x, "ObjectType", None) or ""
                lines.append(f"- {x.Name or '-'}" + (f" ({pt})" if pt else "") + f": {n} elements")
        else:
            lines.append(f"No system (IfcSystem / IfcDistributionSystem) " + (f"matching {', '.join(words)} " if words else "")
                         + "is defined in this model.")
        if dist:
            lines.append(f"Distribution elements by class ({sum(dist.values())}): " + "; ".join(f"{k[3:]}: {v}" for k, v in dist.most_common(15)))
        else:
            lines.append("The model contains no distribution (MEP) elements.")
        return "\n".join(lines), [{"key": x.Name, "count": n} for x, n in hit], [x for x, _ in hit], \
            "IfcSystem groupings (IfcRelAssignsToGroup) and IfcDistributionElement classes"

    if L.intent == "structural":
        lines, rows, cited = [], [], []
        for cls in ("IfcBeam", "IfcColumn", "IfcSlab", "IfcWall", "IfcFooting", "IfcMember", "IfcPlate", "IfcPile", "IfcRoof"):
            es_c = ents(M, [cls])
            if cls == "IfcWall":
                lb = [e for e in es_c if common_flag(M, e, "LoadBearing") is True]
                if lb:
                    es_c = lb
                    cls = "IfcWall (LoadBearing = True)"
                else:
                    cls = "IfcWall (all; no LoadBearing flag set)" if es_c else cls
            if not es_c:
                rows.append({"key": cls, "count": 0})
                lines.append(f"- {cls[3:]}: 0")
                continue
            c = Counter(type_name(e) for e in es_c)
            lines.append(f"- {cls[3:]}: {len(es_c)} (" + "; ".join(f"{t}: {n}" for t, n in c.most_common(6)) + (" ..." if len(c) > 6 else "") + ")")
            rows.append({"key": cls, "count": len(es_c)})
            cited += es_c
        return "Structural elements by class:\n" + "\n".join(lines), rows, cited[:400], "entity counts by class, types from IfcRelDefinesByType"

    if L.intent == "storeys":
        sts = sorted(M.storeys, key=lambda s: float(getattr(s, "Elevation", 0) or 0))
        lines, rows = [f"{len(sts)} storeys (IfcBuildingStorey):"], []
        for i, s in enumerate(sts):
            el = float(getattr(s, "Elevation", 0) or 0) * M.len_scale
            h = None
            if i + 1 < len(sts):
                h = float(getattr(sts[i + 1], "Elevation", 0) or 0) * M.len_scale - el
            lines.append(f"- {s.Name}: elevation {_fmt(el)} m" + (f", height to next storey {_fmt(h)} m" if h is not None else ""))
            rows.append({"key": s.Name, "elevation_m": round(el, 3), "height_m": round(h, 3) if h is not None else None, "guid": s.GlobalId})
        return "\n".join(lines), rows, sts, "storey elevations; height = elevation difference to the next storey"

    if not L.classes:
        raise LookupError("no element class named in the question")
    es = ents(M, L.classes)
    lab = L.label or L.classes[0]
    if not es:
        return (f"No {lab} elements in this model: 0 entities of {' / '.join(L.classes)}."), [], [], \
            f"entity count by class ({', '.join(L.classes)})"
    es, fnote = _filter(M, L, es)
    if es is None:
        raise LookupError(fnote)
    scope = f" ({fnote})" if fnote else ""
    lab = L.label or L.classes[0]

    if "es0" in locals():
        es, scope = es0, f" (the {len(es0)} elements named '{es0[0].Name}')"
    if L.intent == "absent":
        vals = Counter()
        for e in es:
            for ps, k, v in flat_props(e):
                if LOADBEAR_KEY.match(str(k)) and v is not None:
                    vals[bool(v)] += 1
        if not vals:
            return (f"Load-bearing classification is not available in this model: none of the {len(es)} "
                    f"{lab}s carries a LoadBearing property."), [], es[:50], "searched every property set of every element"
        return (f"{lab}s with LoadBearing = True: {vals[True]}; LoadBearing = False: {vals[False]}; "
                f"without the property: {len(es) - sum(vals.values())}"), [{"key": k, "count": v} for k, v in vals.items()], es, "Pset_*Common.LoadBearing"

    if L.intent == "fire":
        pool = [L.element] if L.element is not None else es
        by_type = OrderedDict()
        for e in pool:
            for ps, k, v in flat_props(e):
                if FIRE.search(str(k)) and v not in (None, "", " ") and not placeholder(k, v):
                    by_type.setdefault(type_name(e), {"values": Counter(), "guids": []})
                    by_type[type_name(e)]["values"][str(v)] += 1
                    by_type[type_name(e)]["guids"].append(e.GlobalId)
                    break
        if L.element is not None:
            e = L.element
            if not by_type:
                return f"{lab} {e.Name}: no fire rating property in the model.", [], [e], "searched every property set"
            v = next(iter(by_type.values()))["values"].most_common(1)[0][0]
            return f"{lab} {e.Name} ({type_name(e)}): fire rating {v}", [{"key": e.Name, "value": v}], [e], "property set value"
        if not by_type:
            return (f"Fire rating information is not available for {lab}s in this model: none of the {len(es)} "
                    f"{lab}s{scope} or their types carries a fire rating property."), [], es[:50], "searched every property set of every element and its type"
        lines = [f"{lab} types with fire rating information ({sum(len(d['guids']) for d in by_type.values())} of {len(es)} {lab}s{scope}):"]
        rows = []
        for t, d in by_type.items():
            vs = ", ".join(f"{v}" + (f" x{c}" if len(d["values"]) > 1 else "") for v, c in d["values"].most_common())
            lines.append(f"- {t}: {len(d['guids'])} {lab}s, fire rating {vs}")
            rows.append({"key": t, "count": len(d["guids"]), "values": dict(d["values"])})
        cited = [e for e in es if any(e.GlobalId in d["guids"] for d in by_type.values())]
        return "\n".join(lines), rows, cited, "fire-rating properties"

    if L.intent == "search":
        terms = [t for t in L.prop.split() if len(t) >= 4]
        stems = [t[:-1] if t.endswith("s") else t for t in terms]
        hits = OrderedDict()
        for e in es:
            found = []
            for ps, k, v in flat_props(e):
                kn = fold(str(k))
                if any(st in kn for st in stems) and v not in (None, "", " ") and not placeholder(k, v):
                    found.append((ps, k, v))
            for m, t in materials_of(e):
                if m and any(st in fold(m) for st in stems):
                    found.append(("material", "Name", m + (f" ({float(t) * M.len_scale * 1000:.0f} mm)" if t else "")))
            for ps, k, v in found:
                d = hits.setdefault((str(k), type_name(e)), {"values": Counter(), "guids": [], "pset": ps})
                d["values"][str(v)] += 1
                d["guids"].append(e.GlobalId)
        if not hits:
            return (f"Not available in this model: no property or material matching {', '.join(repr(t) for t in terms)} "
                    f"on any of the {len(es)} {lab}s{scope} or their types."), [], es[:50], \
                f"searched every property name and material of every {lab}"
        lines = [f"Properties matching {', '.join(repr(t) for t in terms)} on {lab}s{scope}:"]
        rows = []
        for (k, t), d in list(hits.items())[:40]:
            vs = "; ".join(f"{v}" + (f" ({c})" if c > 1 else "") for v, c in d["values"].most_common(6))
            lines.append(f"- {t}: {k} = {vs}")
            rows.append({"key": t, "property": f"{d['pset']}.{k}", "values": dict(d["values"])})
        cited = [e for e in es if any(e.GlobalId in d["guids"] for d in hits.values())]
        return "\n".join(lines), rows, cited, "property names / materials matching the question words"

    if L.intent == "element":
        e = L.element
        st = M.storey_of(e)
        facts = OrderedDict()
        facts["class"] = e.is_a()
        facts["type"] = type_name(e)
        if st is not None:
            facts["storey"] = st.Name
        for a in ("width", "height", "area", "volume"):
            v, src = ATTR_FN[a](M, e)
            if v is not None:
                facts[a] = f"{_fmt(v)} {ATTR_UNIT[a]} ({src})"
        mats = materials_of(e)
        if mats:
            facts["material"] = ", ".join(m for m, _ in mats)
        name = space_display(e) if e.is_a("IfcSpace") else (e.Name or e.GlobalId)
        if L.prop and L.prop in facts:
            text = f"{lab} {name}: {L.prop} {facts[L.prop]}"
        else:
            text = f"{lab} {name}: " + "; ".join(f"{k} {v}" for k, v in facts.items())
        if e.is_a("IfcSpace"):
            inside = contained_in_space(M, e)
            if inside:
                c = Counter(f"{x.is_a()[3:]} {type_name(x)}" for x in inside)
                text += "\nElements in this space: " + "; ".join(f"{k} ({v})" for k, v in c.most_common(20))
        return text, [dict(facts, key=name)], [e], "element attributes, quantity sets and material"

    if L.intent == "stats":
        fn = ATTR_FN[L.attr]
        vals, srcs = [], set()
        for e in es:
            v, src = fn(M, e)
            if v is not None:
                vals.append((v, e))
                srcs.add(src)
        if not vals:
            raise LookupError(f"no {L.attr} value on the {lab}s")
        u = ATTR_UNIT[L.attr]
        xs = [v for v, _ in vals]
        lo, hi = min(vals, key=lambda t: t[0]), max(vals, key=lambda t: t[0])
        nm = lambda e: space_display(e) if e.is_a("IfcSpace") else (e.Name or e.GlobalId)
        lines = [f"{L.attr.capitalize()} of {len(vals)} of {len(es)} {lab}s{scope}: average {_fmt(sum(xs) / len(xs))} {u}, "
                 f"median {_fmt(sorted(xs)[len(xs) // 2])} {u}, minimum {_fmt(lo[0])} {u} ({nm(lo[1])}), maximum "
                 f"{_fmt(hi[0])} {u} ({nm(hi[1])})"]
        if L.by_level:
            per = OrderedDict()
            for v, e in sorted(vals, key=lambda t: float(getattr(M.storey_of(t[1]), "Elevation", 0) or 0) if M.storey_of(t[1]) else 0):
                st = M.storey_of(e)
                per.setdefault(st.Name if st is not None else "(no storey)", []).append(v)
            for k, v in per.items():
                c = Counter(round(x, 2) for x in v).most_common(1)[0]
                lines.append(f"- {k}: {len(v)} {lab}s, most common {_fmt(c[0])} {u} ({c[1]}), range {_fmt(min(v))} to {_fmt(max(v))} {u}")
        return "\n".join(lines), [{"key": nm(e), "value": round(v, 3)} for v, e in vals][:200], [e for _, e in vals], \
            f"{L.attr} from {', '.join(sorted(s for s in srcs if s))}"

    if L.intent == "extreme":
        fn = ATTR_FN[L.attr]
        vals = []
        srcs = set()
        for e in es:
            v, src = fn(M, e)
            if v is not None:
                vals.append((v, e))
                srcs.add(src)
        if not vals:
            raise LookupError(f"no {L.attr} value on the {lab}s")
        vals.sort(key=lambda t: -t[0] if L.order == "max" else t[0])
        cut = vals[min(L.k, len(vals)) - 1][0]
        top = [(v, e) for v, e in vals if (v >= cut - 1e-6 if L.order == "max" else v <= cut + 1e-6)]
        top = top[: max(L.k, 1) + 9]
        word = {"max": {"width": "widest", "height": "tallest", "area": "largest", "length": "longest"},
                "min": {"width": "narrowest", "height": "lowest", "area": "smallest", "length": "shortest"}}[L.order].get(L.attr, L.order)
        lines = [f"{word.capitalize()} {lab}{'s' if len(top) > 1 else ''} by {L.attr.replace('_', ' ')} ({len(vals)} {lab}s with a value{scope}):"]
        rows = []
        for i, (v, e) in enumerate(top):
            st = M.storey_of(e)
            nm = space_display(e) if e.is_a("IfcSpace") else (e.Name or "")
            extra = f", type {type_name(e)}" if not e.is_a("IfcSpace") else ""
            if e.is_a("IfcSpace"):
                a, _ = M.space_area(e)
                h, _ = height_of(M, e)
                extra = (f", area {_fmt(a)} sq m" if a is not None and L.attr != "area" else "") + \
                        (f", height {_fmt(h)} m" if h is not None and L.attr not in ("height",) else "")
            lines.append(f"{i + 1}. {nm}: {_fmt(v)} {ATTR_UNIT[L.attr]}{extra}" + (f", storey {st.Name}" if st is not None else ""))
            rows.append({"key": nm, "value": round(v, 3), "guid": e.GlobalId})
        return "\n".join(lines), rows, [e for _, e in top], f"{L.attr} from {', '.join(sorted(srcs))}"

    if L.intent == "spaces":
        if L.room is not None:
            es = [L.room]
        if L.prop and L.prop.startswith("types:"):
            from .. import takeoff as _tk
            from .ifc_route import space_type_label
            want = set(L.prop[6:].split(","))
            es_all = es
            es = [e for e in es if _tk.room_types(space_type_label(e)) & want]
            scope = f" typed {', '.join(sorted(want))} from their names"
            if not es:
                from .ifc_route import infer_space_types
                inf = infer_space_types(M, es_all)
                w2 = want | ({"bathroom", "wc"} if "wet" in want else set())
                es = [e for e in es_all if e.id() in inf and inf[e.id()][0] & w2]
                if es:
                    scope = (f" typed {', '.join(sorted(want))} from the sanitary fixtures in them (no space name says so; "
                             + "; ".join(f"{space_display(e)}: {inf[e.id()][1]}" for e in es[:6]) + ")")
                    L.fixture_inferred = True
            if not es:
                names = sorted({space_type_label(e) for e in ents(M, ["IfcSpace"])} - {""})
                return (f"No space whose name reads as {', '.join(sorted(want))} in this model (space names: "
                        f"{', '.join(names[:25])}{' ...' if len(names) > 25 else ''})."), [], [], "space names"
        rows, total = [], 0.0
        groups = OrderedDict()
        srcs = set()
        for e in es:
            a, src = M.space_area(e)
            srcs.add(src)
            ln = getattr(e, "LongName", None) or ""
            key = (ln or type_name(e)) if L.group == "function" else ""
            groups.setdefault(key, []).append((e, a))
            total += a or 0
        where = f"on storey {L.storey.Name}" if L.storey is not None else "in the model"
        lines = [f"{len(es)} spaces{scope if L.prop and L.prop.startswith('types:') else ''} {where}, total area {_fmt(total)} sq m."]
        if L.storey is None:
            per = OrderedDict()
            for e in es:
                st = M.storey_of(e)
                d = per.setdefault(st.Name if st is not None else "(no storey)", [0, 0.0])
                d[0] += 1
                d[1] += M.space_area(e)[0] or 0
            lines.append("By storey: " + "; ".join(f"{k}: {n} spaces, {_fmt(a)} sq m" for k, (n, a) in per.items()))
        if L.group == "function" and total:
            lines.append("By function: " + "; ".join(
                f"{k or '-'}: {len(v)} spaces, {_fmt(sum(a or 0 for _, a in v))} sq m ({100 * sum(a or 0 for _, a in v) / total:.1f}%)"
                for k, v in sorted(groups.items(), key=lambda kv: -sum(a or 0 for _, a in kv[1]))[:25]))
        for key, items in sorted(groups.items(), key=lambda kv: -sum(a or 0 for _, a in kv[1])):
            if key:
                lines.append(f"{key} ({len(items)} spaces, {_fmt(sum(a or 0 for _, a in items))} sq m):")
            for e, a in items[:60]:
                st = M.storey_of(e)
                lines.append(f"{'  ' if key else ''}- {space_display(e)}: " + (f"{_fmt(a)} sq m" if a is not None else "no area")
                             + (f" ({st.Name})" if L.storey is None and st is not None else ""))
                rows.append({"key": space_display(e), "area_m2": round(a, 3) if a is not None else None, "guid": e.GlobalId})
        return "\n".join(lines[:200]), rows, es, f"IfcSpace areas ({', '.join(sorted(s for s in srcs if s))})"

    if L.intent == "materials":
        if L.element is not None:
            es = [L.element]
            scope = f" ({lab} {L.element.Name})"
        by_mat = OrderedDict()
        for e in es:
            seen_m = set()
            for m, t in materials_of(e) or [("(no material assigned)", None)]:
                d = by_mat.setdefault(m, {"n": 0, "types": Counter(), "thick": set()})
                if m not in seen_m:          # a material in two layers of one wall counts that wall once
                    seen_m.add(m)
                    d["n"] += 1
                    d["types"][type_name(e)] += 1
                if t:
                    d["thick"].add(round(float(t) * M.len_scale * 1000))
        lines = [f"Materials of {len(es)} {lab}s{scope}, {len([m for m in by_mat if not m.startswith('(')])} distinct:"]
        rows = []
        for m, d in sorted(by_mat.items(), key=lambda kv: -kv[1]["n"]):
            th = f", layer thickness {', '.join(str(x) + ' mm' for x in sorted(d['thick']))}" if d["thick"] else ""
            lines.append(f"- {m}: {d['n']} {lab}s ({100 * d['n'] / len(es):.1f}%){th}")
            for t, c in d["types"].most_common(12):
                lines.append(f"    {t}: {c}")
            rows.append({"key": m, "count": d["n"], "types": dict(d["types"])})
        return "\n".join(lines), rows, es, "IfcRelAssociatesMaterial (layer sets, lists, single materials)"

    if L.intent == "total":
        fn = ATTR_FN[L.attr]
        per = OrderedDict()
        srcs, missing, total = set(), 0, 0.0
        for e in es:
            v, src = fn(M, e)
            if v is None:
                missing += 1
                continue
            srcs.add(src)
            d = per.setdefault(type_name(e), {"n": 0, "v": 0.0})
            d["n"] += 1
            d["v"] += v
            total += v
        if not per:
            raise LookupError(f"no {L.attr} quantity on any of the {len(es)} {lab}s")
        u = ATTR_UNIT[L.attr]
        lines = [f"Total {L.attr} of {lab}s{scope}: {_fmt(total)} {u} over {sum(d['n'] for d in per.values())} {lab}s"
                 + (f" ({missing} without a value)" if missing else "") + ". By type:"]
        rows = []
        for t, d in sorted(per.items(), key=lambda kv: -kv[1]["v"]):
            lines.append(f"- {t}: {d['n']} {lab}s, {_fmt(d['v'])} {u}")
            rows.append({"key": t, "count": d["n"], "value": round(d["v"], 3)})
        return "\n".join(lines), rows, es, f"{L.attr} from {', '.join(sorted(srcs))}"

    if L.intent == "types":
        c = Counter(type_name(e) for e in es)
        lines = [f"Total {lab}s{scope}: {len(es)}. {lab.capitalize()} types and quantities:"]
        rows = []
        per_level = defaultdict(Counter)
        if L.by_level:
            for e in es:
                st = M.storey_of(e)
                per_level[type_name(e)][st.Name if st is not None else "(no storey)"] += 1
        thick, dims = {}, {}
        if L.attr == "thickness":
            for e in es:
                v, _ = thickness_of(M, e)
                if v is not None:
                    thick.setdefault(type_name(e), Counter())[round(v * 1000)] += 1
        if L.attr == "dims":
            for e in es:
                w, _ = width_of(M, e)
                h, _ = height_of(M, e)
                if w is not None or h is not None:
                    dims.setdefault(type_name(e), Counter())[(round(w, 3) if w else None, round(h, 3) if h else None)] += 1
        for t, n in c.most_common(60):
            s = f"- {t}: {n} ({100 * n / len(es):.1f}%)"
            if t in thick:
                s += ", thickness " + ", ".join(f"{mm} mm ({mm / 1000:.3f} m)" for mm, _ in thick[t].most_common())
            if t in dims:
                s += ", " + "; ".join((f"width {_fmt(w)} m" if w else "") + (" x " if w and h else "") + (f"height {_fmt(h)} m" if h else "")
                                      + (f" ({k})" if len(dims[t]) > 1 else "") for (w, h), k in dims[t].most_common(4))
            lines.append(s)
            if L.by_level:
                lines.append("    " + "; ".join(f"{lv}: {k}" for lv, k in per_level[t].most_common()))
            rows.append({"key": t, "count": n})
        if len(c) > 60:
            lines.append(f"... and {len(c) - 60} more types")
        if thick:
            byt = Counter()
            for t, cc in thick.items():
                for mm, k in cc.items():
                    byt[mm] += k
            lines.append("By thickness: " + "; ".join(f"{mm} mm: {k} ({100 * k / len(es):.1f}%)" for mm, k in sorted(byt.items())))
        if L.by_level:
            lv = Counter(M.storey_of(e).Name if M.storey_of(e) is not None else "(no storey)" for e in es)
            lines.append("By storey: " + "; ".join(f"{k}: {v}" for k, v in lv.most_common()))
        return "\n".join(lines), rows, es, "type = IfcRelDefinesByType type name, else ObjectType"
    raise LookupError(f"intent {L.intent} not implemented")


def _has(M, cls):
    try:
        M.m.by_type(cls)
        return True
    except RuntimeError:
        return False


def contained_in_space(M, sp):
    out = []
    for rel in getattr(sp, "ContainsElements", []) or []:
        out += [e for e in rel.RelatedElements if not e.is_a("IfcSpace")]
    return out


# ------------------------------------------------------------------ Nemotron mapping (fallback)

INTENTS = ["types", "materials", "fire", "extreme", "total", "spaces", "storeys", "meta"]


def catalogue(M) -> dict:
    present = Counter(e.is_a() for e in M.m.by_type("IfcProduct") if not e.is_a("IfcOpeningElement")
                      and not e.is_a("IfcAnnotation") and not e.is_a("IfcVirtualElement"))
    return {"classes": dict(present.most_common(40)), "storeys": [s.Name for s in M.storeys]}


def map_with_nemotron(M, question) -> Lookup | None:
    from .. import nemotron as nemo
    if not nemo.enabled():
        return None
    cat = catalogue(M)
    prompt = ("Map a question about an IFC building model to ONE lookup. Reply with JSON only: "
              '{"intent": one of ' + json.dumps(INTENTS) + ', "class": one IFC class from the list or null, '
              '"storey": one storey name from the list or null, "external": true/false/null, '
              '"attr": one of ["width","height","area","volume","length","thickness",null], '
              '"order": "max" or "min", "by_level": true/false}. Use null for anything the question does not '
              'state. If no lookup fits, reply {"intent": null}. Do not answer the question.\n'
              f"Classes in the model (count): {json.dumps(cat['classes'])}\nStoreys: {json.dumps(cat['storeys'])}\n"
              f"Question: {question}")
    try:
        r = nemo.chat(prompt, max_tokens=300, think=False)
    except Exception:
        return None
    from .nemotron import PRICE_IN, PRICE_OUT
    cost = ((r.get("prompt_tokens") or 0) * PRICE_IN + (r.get("completion_tokens") or 0) * PRICE_OUT) / 1e6
    text = r.get("content") or ""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    intent = d.get("intent")
    if intent not in INTENTS:
        return None
    cls = d.get("class")
    if cls is not None and cls not in cat["classes"]:
        return None
    storey = None
    if d.get("storey"):
        storey = next((s for s in M.storeys if s.Name == d["storey"]), None)
        if storey is None:
            return None
    attr = d.get("attr") if d.get("attr") in ATTR_FN else None
    if intent in ("extreme", "total") and attr is None:
        return None
    label = cls[3:].lower() if cls else ""
    return Lookup(intent=intent, classes=[cls] if cls else [], label=label, storey=storey,
                  storey_note=f"storey '{storey.Name}' chosen by Nemotron" if storey else "",
                  external=d.get("external") if isinstance(d.get("external"), bool) else None,
                  attr=attr, order="min" if d.get("order") == "min" else "max", by_level=bool(d.get("by_level")),
                  mapper="nemotron", cost=cost)


# ------------------------------------------------------------------ route entry

# the file header is an answer only to a question about the file: software, schema, standard (any language)
META_CUE = re.compile(r"software|application|authoring|\bifc\b|schema|standard|version|exported|originat|"
                      r"ohjelm|sovellu|versio|anwendung|programm|program\b|programvara|logiciel|norme?\b")

def answer(ctx, q) -> Answer:
    path = ctx.model_ifc()
    if path is None:
        return abstain(ROUTE, "no IFC model for this drawing")
    if q.extra.get("takeoff") is not None:
        return abstain(ROUTE, "takeoff question (area to order for a room type): answered by the ifc takeoff route, "
                              "not by a property lookup")
    M = load(str(path))
    question = ctx.q.question
    L = plan(M, question)
    cost = 0.0
    if L is None and ctx.nemotron:
        L = map_with_nemotron(M, question)
        if L is not None and L.intent == "meta" and not META_CUE.search(fold(question)):
            # QA 14 Nov: "Mika kerros tama on?" (which floor is this) came back as the IFC file header
            L = None
    if L is None:
        return abstain(ROUTE, "no property lookup matches the question")
    if (ctx.q.ext in ("pdf", "svg") and L.storey is None and L.intent in SHEET_SCOPED and not L.by_level
            and L.element is None and L.room is None and not whole_model_asked(question)):
        # asked on a plan sheet without a level or the whole model: the sheet's storey is meant
        st, snote = sheet_storey(M, ctx)
        if st is None:
            return abstain(ROUTE, f"cannot tell which storey this sheet shows ({snote}); "
                                  f"ask with a level name or for the whole model")
        L.storey, L.storey_note = st, snote
    try:
        text, rows, cited, note = run(M, L)
    except LookupError as ex:
        return abstain(ROUTE, str(ex))
    cost = L.cost
    items = [r for r in rows][:200]
    if cited and len(cited) <= 200:
        guids = [e.GlobalId for e in cited]
    else:
        guids = [e.GlobalId for e in cited[:200]]
    expl = (f"Lookup{'' if L.mapper == 'rules' else f' ({L.mapper})'}: {L.describe()}. Values read from {path.name}: {note}."
            + (f" {L.storey_note}." if L.storey_note else "") + f" Cited entities: {len(cited)} (GlobalIds in items).")
    ans = Answer(value=text, unit="", route=ROUTE, confidence=0.85 if L.mapper == "rules" else 0.6,
                 items=items + [{"cited_guids": guids}], explanation=expl, cost_usd=cost)
    if getattr(L, "fixture_inferred", False):
        ans.tier, ans.tier_reason = "estimate", "room type inferred from the sanitary fixtures in the spaces, not from their names"
    elif L.mapper != "rules":
        ans.tier, ans.tier_reason = "estimate", "the question was mapped to the lookup by Nemotron; values are from the file"
    elif L.intent not in PROVEN_INTENTS and not (L.intent in ("materials", "types") and (L.room is not None or L.element is not None)):
        ans.tier = "estimate"
        ans.tier_reason = (f"every value is read from the file, but the question was matched to a broad '{L.intent}' "
                           f"broad lookup; check that the lookup line answers what was asked")
    if ctx.evidence:
        try:
            ans.evidence = render(ctx, M, L, text, cited, estimate=ans.tier == "estimate")
        except Exception:
            ans.evidence = None
    return ans


def render(ctx, M, L, text, cited, estimate=False):
    """Evidence: the answer table as text beside a plan of the storey holding most cited elements,
    with those elements highlighted (blue) and numbered in the order of the table where possible."""
    from PIL import Image, ImageDraw
    from shapely.ops import unary_union
    from ..evidence import BLUE, INK, VectorCanvas, evidence_path, finish, font

    plan_img = None
    sts = Counter(M.storey_of(e) for e in cited if hasattr(e, "GlobalId") and not e.is_a("IfcBuildingStorey"))
    sts.pop(None, None)
    if sts and len(cited) <= 400:
        storey = sts.most_common(1)[0][0]
        ctx_els = [e for cls in ("IfcWall", "IfcSpace") for e in M.m.by_type(cls) if M.storey_of(e) == storey]
        fps = [(e, M.footprint(e)[0]) for e in ctx_els[:1500]]
        hl = [(e, M.footprint(e)[0]) for e in cited if M.storey_of(e) == storey]
        geoms = [g for _, g in fps + hl if g is not None]
        if geoms:
            cv = VectorCanvas(unary_union(geoms).bounds, width=1100)
            for e, g in fps:
                for p in getattr(g, "geoms", [g]) if g is not None else []:
                    if hasattr(p, "exterior"):
                        cv.poly(list(p.exterior.coords), fill=(235, 235, 235, 255) if e.is_a("IfcWall") else None,
                                outline=(175, 175, 175), width=1)
            for i, (e, g) in enumerate(hl):
                if g is None:
                    continue
                if e.is_a("IfcSpace"):
                    for p in getattr(g, "geoms", [g]):
                        if hasattr(p, "exterior"):
                            cv.poly(list(p.exterior.coords), fill=BLUE + (70,), outline=BLUE, width=2)
                    c = g.representative_point()
                    cv.text((c.x, c.y), space_display(e)[:24], size=11)
                else:
                    if e.is_a("IfcDoor") or e.is_a("IfcWindow") or not hasattr(g, "area") or g.area < 0.5:
                        cv.box(g.bounds, n=i + 1 if len(hl) <= 60 else None, color=BLUE, pad=0.05)
                    else:     # walls, slabs, beams: the element's own footprint, filled
                        for p in getattr(g, "geoms", [g]):
                            if hasattr(p, "exterior"):
                                cv.poly(list(p.exterior.coords), fill=BLUE + (150,), outline=BLUE, width=1)
            d = ImageDraw.Draw(cv.img)
            d.text((10, 6), f"Storey {storey.Name}: {len(hl)} of {len(cited)} cited entities highlighted", fill=INK, font=font(14))
            plan_img = cv.img
    lines = text.splitlines()[:70]
    f = font(14)
    tw = 640
    th = 30 + 19 * len(lines) + 40
    panel = Image.new("RGB", (tw, max(th, plan_img.height if plan_img else 0)), "white")
    d = ImageDraw.Draw(panel)
    y = 10
    for ln in lines:
        d.text((12, y), ln[:90], fill=INK, font=f)
        y += 19
    d.text((12, y + 10), f"{len(cited)} cited entities; GlobalIds in the answer JSON", fill=(90, 90, 90), font=font(12))
    if plan_img is not None:
        img = Image.new("RGB", (tw + plan_img.width, max(panel.height, plan_img.height)), "white")
        img.paste(panel, (0, 0))
        img.paste(plan_img, (tw, 0))
    else:
        img = panel
    out = evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question)
    first = lines[0] if lines else ""
    tag = "ESTIMATE (broad lookup, check it answers the question)" if estimate else "proven"
    return finish(img, out, ctx.q.question, f"Answer [{tag}]: {first[:90]}   route: {ROUTE} (cost €0)",
                  note=f"Lookup: {L.describe()}" + (f"; {L.storey_note}" if L.storey_note else ""))
