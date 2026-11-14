"""Vector and IFC consistency fixes (branch vector-ifc-consistency): python -m pytest tests/test_vecfix.py

Synthetic stand-ins, no dataset needed; the end-to-end cases at the bottom run only when the full
dataset is found (STOA_DATA, or ../data/stoa-full/junction-dataset next to the repo).
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from PIL import Image, ImageDraw
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa import takeoff
from bpqa.evidence import font, wrap_px
from bpqa.query import parse
from bpqa.routes import dxf_swiss, ifc_route
from bpqa.routes.scope_guard import ambiguous_room, single_room_problem


# ---- kitchen asked in the singular, several rooms ----------------------------------------------

def test_singular_type_sets_single_type_not_single_room():
    q = parse("How big is the kitchen?")
    assert q.extra["takeoff"]["single_type"] is True
    assert not q.extra.get("single_room")
    assert single_room_problem(q, [1, 2, 3]) is None          # several matches are summed, not refused


def test_plural_and_quoted_keep_their_meaning():
    assert not parse("How big are the kitchens in total?").extra["takeoff"].get("single_type")
    q = parse("What is the net floor area of the room 'Living room'?")
    assert q.extra.get("single_room")                          # a quoted name keeps the abstaining guard
    assert "names one room" in single_room_problem(q, ["a", "b"])


def test_ambiguous_room_abstention_is_final():
    a = ambiguous_room("dxf-geometry", "the question names one room but 4 rooms match")
    assert not a.answered and a.final


def test_scope_sentence_and_reason():
    tk = {"types": ["kitchen"], "single_type": True}
    labels = ["Kueche 2.02", "Kueche 2.15"]
    assert "names one kitchen; all 2 matching rooms are summed" in takeoff.scope_sentence(tk, labels)
    assert "2 rooms match" in takeoff.scope_reason(tk, labels)
    assert takeoff.scope_reason(tk, labels[:1]) == ""          # one room: nothing to say, proven
    assert takeoff.scope_reason({"types": ["kitchen"]}, labels) == ""   # plural question: proven sum


def test_combined_room_is_named_and_estimated():
    tk = {"types": ["kitchen"]}
    labels = [f"ELUTUBA+KÖÖK ({i}-1)" for i in range(3)] + ["Küche (2)"]
    comb = takeoff.combined_rooms(tk, labels)
    assert len(comb) == 1 and comb[0][1] == 3
    s = takeoff.scope_sentence(tk, labels)
    assert "ELUTUBA+KÖÖK (x3)" in s and "living room" in s and "whole area" in s
    assert "combined room name" in takeoff.scope_reason(tk, labels)
    assert takeoff.combined_rooms(tk, ["Küche (2)", "Bad"]) == []


# ---- wording of the toilet fallback when nothing can confirm a toilet ----------------------------

def _tk(source=None, confirmed=False):
    tr = {"confirmed": confirmed, "rooms": ["Bad"], "unlabelled": 0, "left_out": [], "unknown": [],
          "other_types": [], "why": ""}
    if source:
        tr["fixture_source"] = source
    return {"types": ["bathroom"], "toilet_rooms": tr}


def test_model_without_wc_fixtures_says_so():
    s = takeoff.interpretation(_tk("model"), [("Bad (3)", 12.13)])
    assert "no WC fixtures in the model" in s and "named as bathrooms" in s
    assert "rooms that contain a toilet" not in s
    assert "bathroom names, no WC fixtures in the model" in takeoff.fallback_reason(_tk("model"))


def test_drawing_without_toilet_symbols_says_so():
    s = takeoff.interpretation(_tk(), [("Bad 2.03", 5.73)])
    assert "no toilet symbols drawn" in s and "rooms that contain a toilet" not in s


def test_confirmed_wording_unchanged():
    s = takeoff.interpretation(_tk(confirmed=True), [("Bad", 5.0)])
    assert s.startswith("No separate WC room on this floor; using the rooms that contain a toilet")


# ---- bathrooms without a bathroom label: rooms holding wet fixtures ------------------------------

def test_fixture_room_fallback():
    rooms = [{"name": "Korridor", "number": "2.01"}, {"name": None, "number": "2.04", "id": 1},
             {"name": None, "number": "2.05", "id": 2}, {"name": None, "number": None, "id": 3}]
    kinds = {1: {"toilet", "washbasin"}, 2: {"washbasin"}, 3: {"toilet"}}
    tk = {"types": ["bathroom"]}
    used, tk2 = takeoff.fixture_room_fallback(rooms, tk, lambda r: kinds.get(r.get("id"), set()))
    assert [r["number"] for r in used] == ["2.04"]               # a basin alone says nothing; no number = not a room
    assert tk2["fixture_rooms"]["kinds"] == ["toilet"]
    assert "unnamed room" in takeoff.fallback_reason(tk2)
    assert "No room on this floor is labelled as a bathroom" in takeoff.interpretation(tk2, [("unlabelled room 2.04", 3.4)])
    # a labelled bathroom on the floor: no fallback
    assert takeoff.fixture_room_fallback(rooms + [{"name": "Bad", "number": "2.02"}], tk, lambda r: {"toilet"})[0] == []
    # other room types are not touched
    assert takeoff.fixture_room_fallback(rooms, {"types": ["kitchen"]}, lambda r: {"toilet"})[0] == []


# ---- sinks ----------------------------------------------------------------------------------

def test_bare_sink_question_parses_as_sink_count():
    q = parse("How many sinks are drawn on this floor plan?")
    assert (q.measure, q.obj) == ("count", "sink")
    q2 = parse("How many sinks and wash basins (kitchen and bathroom combined) are drawn on this floor plan?")
    assert q2.obj == "sink"
    assert parse("How many washbasins are drawn on this floor plan?").obj == "washbasin"


# ---- doors only on the room's own walls ----------------------------------------------------------

def _leaf(x0, y0, w=0.9, t=0.14):
    return [(x0, y0), (x0 + w, y0), (x0 + w, y0 + t), (x0, y0 + t), (x0, y0)]


def test_door_in_a_neighbours_wall_is_not_deducted():
    room = {"name": "Bad", "number": "2.03", "poly": box(0, 0, 3, 2), "area_label": None, "unit": None}
    on_wall = _leaf(1.0, 2.0)                  # touches the top outline: in this room's wall
    next_door = _leaf(1.0, 2.0 + 0.25)         # 0.25 m beyond the wall: a neighbour's door
    fl = NS(doors=[on_wall, next_door], windows=[], room_area=lambda r: round(r["poly"].area, 2))
    out = dxf_swiss.swiss_wall_rooms(fl, [room])
    assert len(out[0]["openings"]) == 1
    assert out[0]["openings"][0]["bbox"][1] == pytest.approx(2.0)


# ---- IFC: a storey without the asked room type ---------------------------------------------------

def _sp(i, name, long_name=None):
    return NS(id=lambda i=i: i, Name=name, LongName=long_name)


class _M:
    def __init__(self, by_storey):
        self.spaces = [s for v in by_storey.values() for s in v]
        self.st = {s.id(): NS(Name=n, id=lambda n=n: hash(n)) for n, v in by_storey.items() for s in v}

    def entities(self, obj):
        return self.spaces

    def storey_of(self, sp):
        return self.st[sp.id()]


def test_no_room_of_type_names_the_storey_and_where_it_is_elsewhere():
    ground = [_sp(1, "Toilet"), _sp(2, "Toilet"), _sp(3, "Gang")]
    basement = [_sp(4, "Bad - Dame"), _sp(5, "Bad - Herre")]
    M = _M({"Grundplan": ground, "Kaelder": basement})
    storey = M.st[1]
    s = ifc_route.no_room_of_type(M, storey, ground, ["bathroom"])
    assert s.startswith("None on this floor: 0 m2.")
    assert "storey Grundplan" in s and "Toilet x2" in s
    assert "Bad - Dame is on Kaelder" in s                    # other storeys are said apart
    assert "2 WC rooms" in s                                   # the toilet rooms are listed, not counted
    assert "Kaelder" not in s.split("Elsewhere")[0]            # the storey's own room list has no basement rooms


def test_short_reason_never_cuts_inside_a_bracket():
    r = ("interpretation: no separate WC room, so 'toilets' read as the rooms that contain a toilet "
         "(bathroom names, no WC fixtures in the model)")
    s = ifc_route.short_reason(r)
    assert s.count("(") == s.count(")") and s.count("'") % 2 == 0
    assert ifc_route.short_reason("short") == "short"


# ---- evidence header wraps instead of running off the edge ---------------------------------------

def test_wrap_px_fits_the_width():
    f = font(19)
    text = ("Answer [ESTIMATE: assumed heights; unnamed rooms with wet fixtures read as bathrooms]: 101.23 m2 "
            "(floor 14.06 + walls 77.97 + 10% waste 9.20; order quantity incl. waste)   route: dxf-geometry (cost $0)")
    lines = wrap_px(text, f, 800)
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    assert len(lines) > 1 and all(d.textlength(ln, font=f) <= 800 for ln in lines)
    assert " ".join(lines) == " ".join(text.split())
    assert wrap_px("x" * 400, f, 300)[0] != "x" * 400          # one long word is cut, not overrun
    cut = wrap_px(text, f, 300, 2)
    assert len(cut) == 2 and cut[-1].endswith("...")


# ---- end to end on the dataset (skipped when absent) ---------------------------------------------

def _root():
    for c in (os.environ.get("STOA_DATA"), Path(__file__).resolve().parents[2] / "data/stoa-full/junction-dataset"):
        if c and (Path(c) / "questions.jsonl").exists():
            return Path(c)
    return None


needs_data = pytest.mark.skipif(_root() is None, reason="dataset not found")


def _ask(path, question):
    from bpqa.data import Question
    from bpqa.router import Context, solve
    ctx = Context(root=_root(), q=Question(qid=-1, path=path, question=question), out_dir=Path("out/evidence"),
                  evidence=False, nemotron=False)
    return solve(ctx)[1]


@needs_data
def test_e2e_fzk_attic_has_no_bathroom_or_kitchen():
    p = "data/ifc_models/kit-fzk-haus_ac20-fzk-haus_plan-02-dachgeschoss.pdf"
    a = _ask(p, "What is the total area of the bathrooms?")
    assert a.answered and a.value == 0 and a.tier == "proven" and "Galerie" in a.explanation
    a = _ask(p, "How big is the kitchen?")
    assert a.value == 0 and "Küche is on Erdgeschoss" in a.explanation


@needs_data
def test_e2e_sinks_and_toilets_on_ifc_without_fixtures():
    p = "data/ifc_models/kit-fzk-haus_ac20-fzk-haus_plan-01-erdgeschoss.pdf"
    a = _ask(p, "How many sinks are drawn?")
    assert not a.answered and "cannot prove" in a.abstain and "no sink objects" in a.abstain
    a = _ask(p, "How many toilets (WC seats) are drawn?")
    assert a.value == 0 and "0 WC fixture objects" in a.explanation
    a = _ask(p, "How many m2 of tiling do I need for the toilets?")
    assert "no WC fixtures in the model" in a.explanation and a.tier == "estimate"


@needs_data
def test_e2e_swiss_sink_and_bathroom_fallback():
    p = "data/swiss_dwellings/site1000_bldg2082_floor9385.pdf"
    a = _ask(p, "How many sinks and wash basins (kitchen and bathroom combined; a double-bowl sink counts as one) are drawn on this floor plan?")
    assert a.value == 4 and a.tier == "estimate" and "kitchen sinks are not told apart" in a.tier_reason
    area = _ask(p, "What is the total area of the bathrooms?")
    til = _ask(p, "How many m2 of tiling do I need for the bathrooms?")
    assert area.answered and til.answered and area.tier == til.tier == "estimate"
    assert "No room on this floor is labelled as a bathroom" in area.explanation
    assert area.value == pytest.approx(14.06) and "floor 14.06" in til.explanation


@needs_data
def test_e2e_kitchen_singular_is_a_sum_and_an_estimate():
    a = _ask("data/swiss_dwellings/site1000_bldg2082_floor9385.pdf", "How big is the kitchen?")
    assert a.value == pytest.approx(34.59) and a.tier == "estimate" and "all 4 matching rooms are summed" in a.explanation
    a = _ask("data/ifc_models/bsc-esplanades_1807-ep-ar-v18_plan-01-1-korrus.pdf", "How big is the kitchen?")
    assert a.tier == "estimate" and "Combined room name" in a.explanation
