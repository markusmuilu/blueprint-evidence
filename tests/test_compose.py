"""Composite questions (bpqa/compose.py) and the deterministic reading additions (bpqa/understand.py)."""
from bpqa import compose as C
from bpqa import phrasing as P
from bpqa import understand as U
from bpqa.answer import Answer


def test_plans():
    assert C.plan("How many doors and windows?") == {"kind": "parts", "parts": ["How many doors?", "How many windows?"]}
    assert C.plan("kitchen and bathroom area")["parts"] == ["What is the area of the kitchens?",
                                                            "What is the area of the bathrooms?"]
    assert C.plan("list the bedrooms and their sizes") == {"kind": "list", "types": ["bedroom"]}
    assert C.plan("what rooms are there and how big")["kind"] == "list"
    p = C.plan("What is the smallest bathroom?")
    assert p["kind"] == "extreme" and p["types"] == ["bathroom"] and not p["max"]
    assert C.plan("Which bedroom is the largest?")["which"]
    p = C.plan("How many rooms are over 20 m2?")
    assert p["kind"] == "condition" and p["gt"] and p["threshold"] == 20 and p["count"]
    assert abs(C.plan("list the rooms over 200 sq ft")["threshold"] - 18.581) < 0.01
    assert C.plan("list rooms under 8 square meters")["threshold"] == 8
    assert C.plan("what share of the floor is bathrooms") == {"kind": "share", "types": ["bathroom"]}
    assert C.plan("total bedroom area apartment by apartment") == {"kind": "per_apartment", "types": ["bedroom"]}
    assert C.plan("Which floor is this plan?") == {"kind": "which_storey"}
    assert C.plan("Wie groß ist die Küche, und wie viele Fenster gibt es?")["kind"] == "parts"


def test_not_composite():
    # one route answers these, and the dataset templates (quoted names) are never composite
    for q in ["How many showers and bathtubs are there?", "How many sinks and wash basins are shown?",
              "biggest room?", "How many doors are shown on this floor plan?",
              "Wie viel Quadratmeter Fliesen brauche ich fuer Bad und WC?", "total area of the kitchen and bathroom together",
              "How many doors are shown on the floor plan of 'Erdgeschoss'?",
              "What is the total amount of tiling needed in all bathrooms, and what is the breakdown by bathroom?",
              "Can you give me the list of all room types?"]:
        assert C.plan(q) is None, q


def test_rows_and_list_answer():
    base = Answer(value=30.0, unit="m2", route="dxf-geometry", tier="proven", items=[
        {"number": "1.01", "name": "Schlafzimmer", "area_m2": 12.0, "unit": "A"},
        {"number": "1.02", "name": "Schlafzimmer", "area_m2": 18.0, "unit": "B"}])
    rows = C.rows_of(base)
    assert [r["area"] for r in rows] == [12.0, 18.0] and [r["unit"] for r in rows] == ["A", "B"]
    assert C._fmt(rows) == "1.01 Schlafzimmer 12.00 m2; 1.02 Schlafzimmer 18.00 m2"


def test_storey_words_and_units():
    q = P.read("How many windows on the top floor?", use_llm=False)
    assert q.extra["storey_ref"]["kind"] == "top" and q.extra["unresolved_scope"]
    assert P.read("Montako ikkunaa on ylimmässä kerroksessa?", use_llm=False).extra["storey_ref"]["kind"] == "top"
    assert P.read("How many doors in the basement?", use_llm=False).extra["storey_ref"]["kind"] == "basement"
    assert P.read("What is the area of the bedrooms in square feet?", use_llm=False).extra["unit_out"] == "sqft"
    # quoted storeys stay the rules' own
    q = P.read("How many doors are shown on the floor plan of 'Erdgeschoss'?", use_llm=False)
    assert q.level == "Erdgeschoss" and "storey_ref" not in q.extra


class _S:
    def __init__(self, name, elev, i):
        self.Name, self.Elevation, self._i = name, elev, i

    def id(self):
        return self._i


class _M:
    len_scale = 0.001

    def __init__(self, storeys, rooms):
        self.storeys, self._rooms = storeys, rooms

    def entities(self, obj):
        return self._rooms if obj == "room" else []

    def storey_of(self, el):
        return el


def test_resolve_storey():
    st = [_S("Keller", -2800, 1), _S("Erdgeschoss", 0, 2), _S("1. OG", 2800, 3), _S("Dach", 5600, 4)]
    M = _M(st, [st[0], st[1], st[2]])          # rooms on the first three; the roof holds none
    assert U.resolve_storey(M, "top")[0].Name == "1. OG"
    assert U.resolve_storey(M, "ground")[0].Name == "Erdgeschoss"
    assert U.resolve_storey(M, "basement")[0].Name == "Keller"
    assert U.resolve_storey(M, "attic")[0] is None
    assert U.resolve_storey(M, "upper")[0].Name == "1. OG"     # two main floors with rooms


def test_unknown_compound_room_blocks_the_all_rooms_reading():
    q = P.read("Mikä on pukuhuoneiden pinta-ala?", use_llm=False)
    assert q.extra.get("blocked")
    assert P.read("Mikä on makuuhuoneiden pinta-ala?", use_llm=False).extra["takeoff"]["types"] == ["bedroom"]
