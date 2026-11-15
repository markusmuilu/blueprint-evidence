"""Final judge review 15 Nov: never proven on a default the question did not state (waste, assumed height, walls
taken as tiled); 'to order' adds the default waste on the sheet route as on the others."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bpqa import takeoff, walls  # noqa: E402
from bpqa.answer import Answer  # noqa: E402
from bpqa.router import defaults_estimate  # noqa: E402

ROOM = {"label": "Bad", "floor_area": 4.0, "perimeter": 8.0, "height": 2.6, "height_src": "IfcSpace Height",
        "openings": []}


def _wall_defaults(question, tk, room=ROOM):
    return walls.summary([dict(room)], tk, question)[3]["defaults"]


def test_default_waste_and_implied_walls_are_defaults():
    tk = {"types": ["bathroom"], "material": "tiles", "surface": "floor", "floor_explicit": False}
    d = _wall_defaults("How many m2 of tiles should I order for the bathrooms?", tk)
    assert any("default 10% waste" in x for x in d) and any("full height" in x for x in d)


def test_stated_waste_and_model_height_on_asked_surfaces_leave_no_default():
    tk = {"types": ["bathroom"], "material": "tiles", "surface": "floor+walls", "floor_explicit": True}
    assert _wall_defaults("How many m2 of tiles for the bathroom floor and walls should I order with 5% waste?", tk) == []
    assert _wall_defaults("Tiles to order for the bathroom floor and walls, no waste", tk) == []


def test_assumed_ceiling_height_is_a_default():
    tk = {"types": ["bathroom"], "material": "tiles", "surface": "floor+walls", "floor_explicit": True}
    room = dict(ROOM, height=None, height_src="")
    d = _wall_defaults("Tiles for the bathroom floor and walls with 5% waste", tk, room)
    assert any("assumed ceiling height" in x for x in d)


def test_floor_takeoff_with_default_waste_lists_it():
    tk = takeoff.parse_takeoff("How many square meters of floor tiles should I order for the bathrooms?")
    assert tk["waste_src"].startswith("default")
    _, items, _ = takeoff.summary([{"label": "Bad", "area": 4.0, "source": "x"}], tk)
    assert items[-1]["totals"]["defaults"]
    tk = takeoff.parse_takeoff("How many square meters of floor tiles should I order for the bathrooms with 5% waste?")
    _, items, _ = takeoff.summary([{"label": "Bad", "area": 4.0, "source": "x"}], tk)
    assert items[-1]["totals"]["defaults"] == []


def test_router_demotes_proven_and_keeps_the_number():
    a = Answer(value=86.47, unit="m2", route="ifc", tier="proven",
               items=[{"n": 1}, {"totals": {"defaults": [walls.default_waste_reason()]}}])
    defaults_estimate(a)
    assert a.tier == "estimate" and a.value == 86.47 and "say 'no waste'" in a.tier_reason
    b = Answer(value=10.0, unit="m2", route="ifc", tier="proven", items=[{"totals": {"defaults": []}}])
    assert defaults_estimate(b).tier == "proven"


def test_order_word():
    assert takeoff.says_order("how many m2 of tiling should i order for the toilets")
    assert not takeoff.says_order("how many m2 of tiling for the toilets")
