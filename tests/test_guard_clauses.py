"""bpqa/constraints.py: clauses the answer dropped (morning check 15 Nov, N1 to N4): negation, door or window counts of
one room, baseboard answered as an area, "A and B"."""
from types import SimpleNamespace

from bpqa.answer import Answer
from bpqa.constraints import _length_as_area, _pair, detect, unused


def kinds(q):
    return {(c["kind"], c["value"]) for c in detect(q)}


def test_negation_detected():
    assert ("negation", "type") in kinds("How many rooms are not bedrooms?")
    assert ("negation", "type") in kinds("How many sinks excluding the kitchen?")
    assert ("negation", "scope") in kinds("How many bedrooms excluding apartment A?")
    assert ("negation", "type") in kinds("Montako huonetta, jotka eivat ole makuuhuoneita?")
    # the apartment is the exclusion, not the scope
    assert ("group", "A") not in kinds("How many bedrooms excluding apartment A?")


def test_dataset_exclusions_are_definitions():
    for q in ["How many doors are shown on this floor plan? Count doorways fitted with a door; open doorways without "
              "a door leaf do not count.",
              "Kuinka monta ovea pohjapiirustuksessa on? Laske oviaukot, joissa on ovi; pelkkiä oviaukkoja ilman "
              "ovilehteä ei lasketa.",
              "How many doors (interior doors and entrance doors together) are drawn on this floor plan?",
              "Wie viele Türen (Innentüren und Eingangstüren zusammen) sind auf diesem Grundriss eingezeichnet?"]:
        assert not {k for k in kinds(q) if k[0] in ("negation", "room_count")}, q


def _ctx(q):
    return SimpleNamespace(q=SimpleNamespace(question=q, ext="pdf"), model_ifc=lambda: None, sibling=lambda e: None)


def _query(obj, measure="count", **extra):
    return SimpleNamespace(obj=obj, measure=measure, label=None, room_no=None, apartment=None, level=None,
                           variant=None, extra=extra)


def test_negation_applied_or_not():
    q = "How many bathrooms or washrooms (e.g. KH, KPH, PH, PESUH, SH) are on this floor plan, not counting separate WC rooms?"
    a = Answer(value=4, unit="count", route="dxf-geometry", tier="proven", explanation="4 = rooms labelled as bathroom")
    assert not [m for m in unused(_ctx(q), _query("bathroom"), a) if m["kind"] == "negation"]
    q = "Kuinka monta kylpy- tai pesuhuonetta (esim. KH, KPH, PH, PESUH, SH) pohjapiirustuksessa on, erillisiä WC-tiloja lukuun ottamatta?"
    assert not [m for m in unused(_ctx(q), _query("bathroom"), a) if m["kind"] == "negation"]
    q = "How many rooms are not bedrooms?"
    a = Answer(value=9, unit="count", route="dxf-geometry", tier="proven", explanation="9 = rooms labelled as bedroom")
    assert [m for m in unused(_ctx(q), _query("bedroom"), a) if m["kind"] == "negation"]
    q = "How many sinks excluding the kitchen?"
    a = Answer(value=2, unit="count", route="raster-detector", tier="proven", explanation="2 box(es) of sink")
    assert [m for m in unused(_ctx(q), _query("sink"), a) if m["kind"] == "negation"]
    q = "What is the net floor area of apartment A in m², summing all its rooms except balconies, loggias, terraces?"
    a = Answer(value=163.57, unit="m2", route="dxf-geometry", tier="proven", explanation="indoor rooms inside apartment A")
    assert not [m for m in unused(_ctx(q), _query("apartment", "area", group_applied="A"), a) if m["kind"] == "negation"]


def test_room_door_counts():
    for q in ["How many doors does the kitchen have?", "How many doors does the printer room have?",
              "How many windows in the bedrooms?", "Kuinka monta ovea makuuhuoneissa on?"]:
        assert any(k[0] == "room_count" for k in kinds(q)), q
    for q in ["How many doors and windows?", "How many windows are shown on this floor plan?"]:
        assert not any(k[0] == "room_count" for k in kinds(q)), q
    a = Answer(value=30, unit="count", route="dxf-geometry", tier="proven", explanation="30 = door outlines on layer DOOR")
    assert [m for m in unused(_ctx("How many doors does the kitchen have?"), _query("door"), a) if m["kind"] == "room_count"]


def test_baseboard_as_area_withdrawn():
    a = Answer(value=15.2, unit="m2", route="dxf-geometry", tier="proven", explanation="outline of room 3.16")
    assert _length_as_area(_ctx("How much baseboard for room 3.16?"), None, a).value is None
    a = Answer(value=140.03, unit="m", route="dxf-geometry", tier="proven", explanation="baseboard")
    assert _length_as_area(_ctx("How many metres of baseboard for the bedrooms?"), None, a).value == 140.03
    a = Answer(value=12.0, unit="m2", route="dxf-geometry", tier="proven", explanation="floor")
    assert _length_as_area(_ctx("What is the area of the kitchen?"), None, a).value == 12.0


def test_pairs():
    assert _pair("What is the total area of apartment A and B?")[1:] == ("apartment", "A", "B")
    assert _pair("What is the area of room 105 and room 106?")[1:] == ("room", "105", "106")
    assert _pair("Mikä on asuntojen A ja B pinta-ala?")[2:] == ("A", "B")
    assert _pair("How many doors and windows?") is None
    assert _pair("What is the area of the kitchen and the bathroom?") is None
    assert _pair("How many rooms in apartment A and how many in B?") is None
