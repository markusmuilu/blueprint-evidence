"""Scan hunt 15 Nov: unitless printed areas, living-kitchen labels, fixture marks, a stray mark in the flat summary,
whole-flat totals that leave out the main rooms."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bpqa import takeoff  # noqa: E402
from bpqa.routes import raster_area as RA  # noqa: E402
from bpqa.routes import raster_summary as RS  # noqa: E402
from bpqa.routes.labels import is_label  # noqa: E402


def tok(text, x0, y0, x1, y1, conf=0.99):
    return {"text": text, "conf": conf, "quad": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], "rot": 0}


def test_unitless_areas_under_labels_and_joined_to_them():
    toks = [tok("K20.0", 495, 346, 532, 360), tok("MH", 762, 705, 784, 718), tok("10,2", 751, 724, 775, 737),
            tok("MH", 390, 727, 412, 741), tok("10,0", 377, 746, 404, 761), tok("12*16", 1094, 344, 1127, 360),
            tok("3.8", 567, 792, 587, 806)]
    rooms, free = RA.read_rooms(toks)
    got = sorted((r.label, r.printed) for r in rooms)
    assert got == [("K", 20.0), ("MH", 10.0), ("MH", 10.2)]          # 3.8 has no label above it: not an area
    assert all(r.extra.get("unitless") for r in rooms)


def test_unitless_numbers_ignored_when_the_plan_prints_units_or_reads_one():
    with_unit = [tok("MH", 100, 100, 130, 115), tok("10,2", 100, 120, 130, 135), tok("OH", 300, 100, 330, 115),
                 tok("25,5 m2", 300, 120, 360, 135)]
    rooms, _ = RA.read_rooms(with_unit)
    assert [(r.label, r.printed) for r in rooms if r.printed] == [("OH", 25.5)]
    one = [tok("MH", 100, 100, 130, 115), tok("10,2", 100, 120, 130, 135)]
    rooms, _ = RA.read_rooms(one)
    assert not [r for r in rooms if r.printed]


def test_living_kitchen_label_is_a_kitchen():
    assert is_label("OLOKEITTIÖ") and "kitchen" in takeoff.room_types("OLOKEITTIO")


def test_lower_case_kk_is_a_fixture_mark_not_a_room():
    assert not RA.room_word("kk") and RA.room_word("KK")


def test_flat_summary_with_a_stray_mark_before_the_plus():
    p = RS._parse_token("2H-+KK")
    assert p is not None and p[0] == 2 and p[1] == ["KK"]
    assert RS._parse_token("3H+K+S")[1] == ["K", "S"]
