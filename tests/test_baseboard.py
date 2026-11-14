"""Baseboard (skirting) length: the question parse and the arithmetic. pytest tests/test_baseboard.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa import baseboard as B, walls as W  # noqa: E402
from bpqa.query import parse  # noqa: E402

QUESTIONS = [
    "How many linear meters of baseboard do I need for the bedrooms?",
    "Total skirting board length for the kitchen and the bathrooms",
    "Kuinka monta metriä jalkalistaa tarvitaan makuuhuoneisiin?",
    "Wie viele Meter Sockelleisten brauche ich für die Schlafzimmer?",
    "Hur många meter golvlist behövs i sovrummen?",
    "Combien de mètres de plinthes pour les chambres ?",
    "Linear metres of kickboard for all rooms",
]


def test_parsed_as_baseboard_takeoff():
    for q in QUESTIONS:
        p = parse(q)
        assert p.variant == "type_sum" and p.extra["takeoff"]["surface"] == "baseboard", q
        assert W.surfaces(p.extra["takeoff"])[:2] == (False, True)


def test_types_and_all_rooms():
    tk = parse(QUESTIONS[0]).extra["takeoff"]
    assert tk["types"] == ["bedroom"] and not tk.get("all_rooms")
    tk = parse("baseboard length for all rooms").extra["takeoff"]
    assert tk["all_rooms"] and "balcony" not in tk["types"] and "bedroom" in tk["types"]
    tk = parse("How many metres of skirting do I need?").extra["takeoff"]
    assert tk["all_rooms"]                      # no room type named: every room
    tk = parse("skirting for all rooms except the bathrooms").extra["takeoff"]
    assert tk["all_rooms"] and tk["excluded_types"] == ["bathroom"] and "bathroom" not in tk["types"]


def test_waste_only_when_stated():
    assert "waste_pct" not in parse(QUESTIONS[0]).extra["takeoff"]
    assert parse("baseboard for the bedrooms with 10% waste").extra["takeoff"]["waste_pct"] == 10
    assert parse("baseboard for the bedrooms, no waste").extra["takeoff"]["waste_pct"] == 0


def test_not_a_baseboard_question():
    assert B.parse_baseboard("How many m2 of tiles for the bathrooms?") is None
    assert parse("How many m2 of tiles for the bathrooms?").extra["takeoff"]["surface"] != "baseboard"


def _rooms():
    door = {"kind": "door", "id": "d1", "width": 0.9, "z0": 0, "z1": 2.0, "src": "door"}
    win = {"kind": "window", "id": "w1", "width": 1.2, "z0": 0.9, "z1": 2.1, "src": "window"}
    return [{"label": "Bedroom 1", "number": "1", "floor_area": 12.0, "perimeter": 14.0, "openings": [door, win]},
            {"label": "Bedroom 2", "number": "2", "floor_area": 9.0, "perimeter": 12.0,
             "openings": [dict(door, id="d2"), dict(door, id="d3", width=0.8)]}]


def test_perimeter_minus_doors_windows_ignored():
    tk = parse(QUESTIONS[0]).extra["takeoff"]
    value, items, expl, totals = W.summary(_rooms(), tk, QUESTIONS[0])
    assert value == round(14 - 0.9 + 12 - 1.7, 2) == 23.4
    assert totals["unit"] == "m" and totals["doors_m"] == 2.6 and totals["windows_ignored"] == 1
    assert [it["net_m"] for it in items] == [13.1, 10.3]
    assert "m of baseboard" in expl and "m2" not in expl.split("Per room")[0]


def test_waste_stated_is_added_to_value():
    tk = parse("baseboard for the bedrooms with 10% waste").extra["takeoff"]
    value, _, _, totals = W.summary(_rooms(), tk, "")
    assert value == round(23.4 * 1.1, 2) and totals["waste_m"] == round(23.4 * 0.1, 2)
    assert "m of baseboard" in W.answer_line(value, totals, "ifc")


def test_door_wider_than_perimeter_never_negative():
    r = {"label": "x", "floor_area": 1, "perimeter": 1.0, "openings": [{"kind": "door", "width": 2.0}]}
    assert B.room_baseboard(r)["net"] == 0.0


def test_select_rooms_drops_balcony_and_excluded():
    tk = parse("skirting for all rooms except the bathrooms").extra["takeoff"]
    rooms = [{"name": n} for n in ("Balkon", "Bad", "Zimmer", "Gang", "")]
    assert [r["name"] for r in B.select_rooms(rooms, tk)] == ["Zimmer", "Gang", ""]
