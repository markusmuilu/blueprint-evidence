"""Label reading fixes: PE and combined KH+WC labels, singular size questions, agreement by absence, full-word
labels against the detector, one shared label reading for the area route.

    python -m pytest tests/test_label_reading.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bpqa.routes import raster_area as ra, raster_rooms as rr  # noqa: E402
from bpqa.routes.labels import find_rooms, full_word, match, parts, read_labels  # noqa: E402
from bpqa.takeoff import parse_takeoff, room_types  # noqa: E402


def tok(text, x, y=0, conf=0.95, rot=0):
    return {"text": text, "conf": conf, "quad": [[x, y], [x + 60, y], [x + 60, y + 20], [x, y + 20]], "rot": rot, "mirror": False}


def test_pe_is_bathroom():
    # CubiCasa5K train+val: all 45 spaces named PE are Bath spaces
    assert match("bathroom", "PE") and room_types("PE 5,3") == {"bathroom"}
    assert not match("kitchen", "PE")


def test_plain_h_is_not_a_bedroom():
    # CubiCasa5K train+val: all 1203 spaces named H are class Room, none Bedroom; the gold does not count them
    assert not match("bedroom", "H") and not match("bedroom", "HUONE")
    assert find_rooms([tok("H", 0), tok("HUONE", 100)], "bedroom") == []


def test_combined_bath_wc_label():
    assert parts("KHWC") == ["KH", "WC"] and parts("KPH / WC") == ["KPH", "WC"]
    assert room_types("KHWC") == {"bathroom", "wc"}
    assert [h["part"] for h in find_rooms([tok("KHWC", 0)], "wc_room")] == ["WC"]
    assert parts("KKWC") == ["KKWC"]


def test_singular_size_question():
    for q, t in [("How big is the kitchen?", "kitchen"), ("How large is the bathroom?", "bathroom"),
                 ("What size is the kitchen?", "kitchen"), ("Kuinka iso on keittiö?", "kitchen")]:
        assert parse_takeoff(q)["types"] == [t], q
    assert parse_takeoff("How big is the house?") is None
    assert parse_takeoff("How many kitchens are there?") is None


CFG = {"thr": 0.3, "band": [0.2, 0.4], "prefer": "ocr_proven", "proven_rule": "agree"}


def test_agreement_on_zero_is_not_proven():
    v, proven, which = rr.combine("wc_room", 0, True, 0, CFG)
    assert (v, which) == (0, "both") and not proven
    assert rr.combine("wc_room", 2, False, 2, CFG)[1]


def test_full_word_labels_beat_empty_detector():
    assert full_word("bedroom", "BEDROOM") and not full_word("bedroom", "MH") and not full_word("bedroom", "BEDROCM")
    assert rr.combine("bedroom", 5, False, 0, CFG, ocr_words=True) == (5, False, "ocr")
    assert rr.combine("bedroom", 5, False, 0, CFG, ocr_words=False) == (0, False, "det")
    assert rr.combine("bedroom", 1, False, 3, CFG, ocr_words=True)[0] == 3      # detector finds more: unchanged


def test_shared_reading_sees_labels_in_every_rotation():
    toks = [tok("AULA", 0), tok("WC", 200, rot=180), tok("WC", 400, rot=270)]
    assert sorted(t["text"] for t in read_labels(toks)) == ["AULA", "WC", "WC"]

    class PA:
        rooms = []
    assert ra.shared_label_texts(toks, PA()) == ["AULA", "WC"]
    assert ra.labels_of_types(toks, {"wc"}, {"wc": "wc_room"}) == {"wc": ["WC", "WC"]}
