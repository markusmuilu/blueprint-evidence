"""Build data/unseen/questions.jsonl: questions of Stoa's kinds, reference answers worked out here.

IFC answers are computed from the model with ifcopenshell (independent of bpqa). Answers for PDFs,
images and the DXF are counted by hand from the rendered drawing and flagged "manual, check".

    python tools/unseen_build_questions.py ../data/unseen
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.element as ue
import ifcopenshell.util.unit as uu

sys.path.insert(0, str(Path(__file__).parent))
from unseen_refs_ifc import footprint_area, qto_value  # noqa: E402

ROOT = Path(sys.argv[1])
OUT = []


def storey_of(el):
    c = ue.get_container(el) or ue.get_aggregate(el)
    while c is not None and not c.is_a("IfcBuildingStorey"):
        c = ue.get_aggregate(c)
    return c


def add(folder, fn, q, ans, unit, typ, method, manual=False, expected="answer"):
    OUT.append({"path": f"{folder}/{fn}", "question": q, "answer": ans, "unit": unit, "type": typ,
                "ref_method": method, "drawing": folder, "manual_check": manual, "expected": expected})


M2, M = "m2", "m"
EN_DOORS = "How many doors are there in the model '{}'?"
EN_WINDOWS = "How many windows are there in the model '{}'?"
EN_ROOMS = "How many rooms (spaces) are there in the model '{}'?"
EN_WALLS = "How many walls are there in the model '{}'?"

# ---------------------------------------------------------------- IFC (computed)
for folder, fn in [("bsmart_building_arch_ifc4", "Building-Architecture.ifc"),
                   ("bsmart_building_arch_ifc2x3", "Building-Architecture.ifc")]:
    m = ifcopenshell.open(str(ROOT / folder / fn))
    name = m.by_type("IfcProject")[0].Name
    n = lambda c: len(m.by_type(c))  # noqa: E731  (subtypes included)
    add(folder, fn, EN_ROOMS.format(name), n("IfcSpace"), "count", "count", "ifcopenshell: len(by_type('IfcSpace'))")
    add(folder, fn, EN_WALLS.format(name), n("IfcWall"), "count", "count", "ifcopenshell: len(by_type('IfcWall')), subtypes included")
    add(folder, fn, EN_DOORS.format(name), n("IfcDoor"), "count", "count", "ifcopenshell: len(by_type('IfcDoor')); the model has none, so 0")
    sc = uu.calculate_unit_scale(m)
    lv = m.by_type("IfcBuildingStorey")[0].Name
    walls = [w for w in m.by_type("IfcWall") if storey_of(w) and storey_of(w).Name == lv]
    tot = sum(qto_value(w, ["Length"]) for w in walls) * sc
    add(folder, fn, f"What is the total length of all walls on '{lv}', in metres?", round(tot, 3), M, "length",
        f"ifcopenshell: sum of Qto_WallBaseQuantities.Length of the {len(walls)} walls contained in the storey, x unit scale {sc}")
    sp = {s.Name: footprint_area(s) for s in m.by_type("IfcSpace")}
    add(folder, fn, "What is the net floor area of the room 'living room' in the model, in m²?", round(sp["living room"], 3), M2, "area",
        "ifcopenshell: mesh of the IfcSpace extrusion, area of the faces on its lowest plane (spaces have no quantity sets and no storey)")

folder, fn = "bsmart_wall_with_window", "wall-with-opening-and-window.ifc"
m = ifcopenshell.open(str(ROOT / folder / fn))
name = m.by_type("IfcProject")[0].Name
add(folder, fn, EN_WINDOWS.format(name), len(m.by_type("IfcWindow")), "count", "count", "ifcopenshell: len(by_type('IfcWindow'))")
add(folder, fn, EN_DOORS.format(name), len(m.by_type("IfcDoor")), "count", "count", "ifcopenshell: len(by_type('IfcDoor')); none, so 0")
add(folder, fn, EN_WALLS.format(name), len(m.by_type("IfcWall")), "count", "count", "ifcopenshell: len(by_type('IfcWall'))")

folder, fn = "ifcopenhouse_ifc4", "IfcOpenHouse_IFC4.ifc"
m = ifcopenshell.open(str(ROOT / folder / fn))
name = m.by_type("IfcProject")[0].Name
add(folder, fn, EN_DOORS.format(name), len(m.by_type("IfcDoor")), "count", "count", "ifcopenshell: len(by_type('IfcDoor'))")
add(folder, fn, EN_WINDOWS.format(name), len(m.by_type("IfcWindow")), "count", "count", "ifcopenshell: len(by_type('IfcWindow'))")
add(folder, fn, EN_WALLS.format(name), len(m.by_type("IfcWall")), "count", "count",
    "ifcopenshell: len(by_type('IfcWall')) = 4 IfcWallStandardCase (a subtype of IfcWall)")
add(folder, fn, EN_ROOMS.format(name), len(m.by_type("IfcSpace")), "count", "count",
    "ifcopenshell: len(by_type('IfcSpace')); the model has no spaces, so 0")

folder, fn = "schependomlaan_design_ifc", "IFC Schependomlaan.ifc"
m = ifcopenshell.open(str(ROOT / folder / fn))
name = m.by_type("IfcProject")[0].Name
add(folder, fn, EN_DOORS.format(name), len(m.by_type("IfcDoor")), "count", "count", "ifcopenshell: len(by_type('IfcDoor'))")
add(folder, fn, EN_WINDOWS.format(name), len(m.by_type("IfcWindow")), "count", "count", "ifcopenshell: len(by_type('IfcWindow'))")
add(folder, fn, EN_ROOMS.format(name), len(m.by_type("IfcSpace")), "count", "count", "ifcopenshell: len(by_type('IfcSpace'))")
lv = "01 eerste verdieping"
nd = sum(1 for d in m.by_type("IfcDoor") if storey_of(d) and storey_of(d).Name == lv)
add(folder, fn, f"How many doors are shown on the floor plan of '{lv}'?", nd, "count", "count",
    "ifcopenshell: IfcDoor whose spatial container is the storey")
s308 = [s for s in m.by_type("IfcSpace") if s.Name == "3.08"]
assert len(s308) == 1 and storey_of(s308[0]).Name == "00 begane grond"
add(folder, fn, "What is the net floor area of the room 'badkamer (3.08)' on '00 begane grond', in m²?",
    round(qto_value(s308[0], ["NetFloorArea"]), 2), M2, "area",
    "ifcopenshell: BaseQuantities.NetFloorArea of the IfcSpace (Name 3.08, LongName badkamer), storey by IfcRelAggregates")
bath = [s for s in m.by_type("IfcSpace") if (s.LongName or "").strip().lower() == "badkamer"]
tot = sum(qto_value(s, ["NetFloorArea"]) for s in bath)
add(folder, fn, "How many square meters of tiling should I order for the bathrooms?", round(tot, 2), M2, "area",
    f"ifcopenshell: sum of NetFloorArea of the {len(bath)} IfcSpaces with LongName 'badkamer' "
    "(floor only, no waste allowance, the 'toilet' rooms are not included)")

# ---------------------------------------------------------------- DXF (entities, checked on the render)
folder = "kjdraw_architecture_floor_plan"
for fn in ("architecture-floor-plan.dxf", "architecture-floor-plan.pdf"):
    add(folder, fn, "How many doors (interior doors and entrance doors together) are drawn on this floor plan?", 12, "count", "count",
        "manual, check: A-DOOR layer holds 12 swing arcs, 6 per door row; confirmed on the render", True)
    add(folder, fn, "How many windows are drawn on this floor plan?", 6, "count", "count",
        "manual, check: A-WINDOW layer holds 12 lines = 6 windows of two lines, 6 cyan marks on the render", True)
    add(folder, fn, "How many rooms labelled 'OPEN OFFICE' are there on this floor?", 2, "count", "count",
        "manual, check: two ANNO texts 'OPEN OFFICE'", True)


# ---------------------------------------------------------------- PDFs and images (counted by eye)
def man(folder, fn, q, a, typ, why, expected="answer", unit="count"):
    add(folder, fn, q, a, unit, typ, "manual, check: " + why, True, expected)


folder, fn = "wm_fraser_residence", "Fraser residence Floor Plan.pdf"
man(folder, fn, "How many doors (interior doors and entrance doors together) are drawn on this floor plan?", 14, "count",
    "door swing arcs counted on 200 dpi crops: 6 in the west half, 8 in the east half; the garage has none")
man(folder, fn, "How many rooms labelled 'BED ROOM' are there on this floor?", 3, "count", "three BED ROOM labels")
man(folder, fn, "How many rooms labelled 'WC' are there on this floor?", 2, "count", "two WC labels")
man(folder, fn, "How many bathtubs are drawn on this floor plan?", 1, "count", "one tub drawn in the BATH room")
man(folder, fn, "What is the total net area in m² of all rooms labelled 'GARAGE' on this floor?", None, "area",
    "the drawing has no scale, no dimensions and no printed areas, so an area cannot be derived; abstaining is the correct outcome",
    "abstain", M2)

folder, fn = "wm_747_villa", "747 villa Floor plan.pdf"
man(folder, fn, "How many bedrooms are on this floor plan?", 2, "count", "labels BED ROOM 1 and BED ROOM 2")
man(folder, fn, "How many kitchens are on this floor plan?", 1, "count", "one KITCHEN label")
man(folder, fn, "How many bathrooms or washrooms are on this floor plan, not counting separate WC rooms?", 1, "count", "one BATH label")
man(folder, fn, "What is the total net area in m² of all rooms labelled 'KITCHEN' on this floor?", None, "area",
    "no scale, no dimensions, no printed areas; abstaining is the correct outcome", "abstain", M2)

folder, fn = "wm_gottlieb_ground", "Gottlieb House Ground Floor Plan.pdf"
man(folder, fn, "How many bathrooms or washrooms are on this floor plan, not counting separate WC rooms?", 2, "count",
    "hand sketch inside a PDF (one embedded image); two BATHROOM labels")
man(folder, fn, "How many kitchens are on this floor plan?", 1, "count", "one KITCHEN label")
man(folder, fn, "How many bedrooms are on this floor plan?", 0, "count", "no bedroom label on the sketch")

folder, fn = "wm_milam_1st_floor", "Milam Residence 1st floor architectural plan.png"
man(folder, fn, "How many rooms labelled 'GARAGE' are there on this floor?", 2, "count", "two GARAGE labels")
man(folder, fn, "How many floors (storeys) does this floor plan show?", 1, "count", "one plan on the image")
man(folder, fn, "How many kitchens are on this floor plan?", 1, "count", "one kitchen, labelled K (low-resolution 1960s scan)")

folder, fn = "wm_tyneside_flat", "Tyneside flat floorplan.png"
man(folder, fn, "How many bedrooms are on this floor plan?", 5, "count",
    "left plan Bedroom 1, 2, 3; right plan Bedroom 1, 2 (the image holds the plans of two flats)")
man(folder, fn, "How many kitchens are on this floor plan?", 2, "count", "one Kitchen label in each of the two plans")
man(folder, fn, "How many floors (storeys) does this floor plan show?", 2, "count", "two plans side by side: upper flat and lower flat")

folder, fn = "wm_ballymun_flat", "Typical floor plan of a Ballymun flat, drawn 1965.png"
man(folder, fn, "How many bedrooms are on this floor plan?", 6, "count", "two mirrored four-room flats, bedroom 1, 2, 3 in each (poor 1965 scan)")
man(folder, fn, "How many kitchens are on this floor plan?", 2, "count", "one 'kitchen' label in each flat")
man(folder, fn, "How many bathrooms or washrooms are on this floor plan, not counting separate WC rooms?", 2, "count",
    "one 'bathroom' label in each flat")
man(folder, fn, "How many balconies are on this floor plan?", 2, "count", "one 'balcony' label in each flat")

with open(ROOT / "questions.jsonl", "w", encoding="utf-8") as f:
    for q in OUT:
        f.write(json.dumps(q, ensure_ascii=False) + "\n")
print(len(OUT), "questions,", len({q["drawing"] for q in OUT}), "drawings")
