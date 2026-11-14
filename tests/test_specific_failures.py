"""Fixes for known failures: not-to-scale notes, drawn rooms that hold another room's label, Finnish full-word
labels, the stroke-mask region merge and the narrow-room retry."""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa import not_to_scale as N  # noqa: E402
from bpqa.routes import labels as L  # noqa: E402
from bpqa.routes import raster_area as RA  # noqa: E402
from bpqa.routes import raster_rooms as RR  # noqa: E402


def tok(text, x0=0, y0=0, w=100, h=20, conf=0.95):
    return {"text": text, "conf": conf, "quad": [[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h]], "rot": 0}


def test_not_to_scale_phrases():
    for s in ["EI MITTAKAAVASSA.", "Suuntaa antava, ei mittakaavassa", "POHUAKUVA ON OHUEELLINEN.", "Not to scale", "NTS",
              "unmaßstäblich", "ej skalenlig", "pas à l'échelle", "EIMITTAKAAVASSA"]:
        assert N.hit(s), s
    for s in ["Mittakaavassa 1:100", "SCALE 1:50", "Kitchen", "HUONE", "Bedroom 2", "ANTS"]:
        assert not N.hit(s), s


def test_not_to_scale_find_keeps_one_per_place():
    f = N.find([tok("EI MITTAKAAVASSA.", 10, 10), tok("EIMITTAKAAVASSA.", 12, 11, conf=0.9), tok("OH", 300, 300)])
    assert len(f) == 1 and f[0]["text"] == "EI MITTAKAAVASSA."
    assert "not to scale" in N.warning(f, "this approximation") and "may be off" in N.warning(f)


def test_finnish_full_words_and_joined_labels():
    assert L.parts("KEITTIO.RUOKAILU") == ["KEITTIO", "RUOKAILU"]
    assert L.parts("W.C") == ["WC"]
    assert L.find_rooms([tok("KEITTIO.RUOKAILU")], "kitchen")
    hit = L.find_rooms([tok("PESUIHUONE")], "bathroom")      # one OCR error in a full word
    assert hit and hit[0]["fuzzy"] == "PESUHUONE"
    assert L.find_rooms([tok("SAUNA")], "sauna") and L.full_word("sauna", "SAUNA")


def test_drawn_room_with_another_rooms_label_is_dropped():
    toks = [tok("Bedroom", 100, 100), tok("Shower", 500, 100), tok("K", 700, 100)]
    other = RR.other_type_labels(toks, "bathroom")
    assert [h["part"] for h in other] == ["BEDROOM"]          # the one-letter K and the Shower word are not other labels
    boxes = [{"cls": "bathroom", "conf": 0.39, "xyxy": [50, 50, 250, 200]},
             {"cls": "bathroom", "conf": 0.49, "xyxy": [450, 50, 650, 200]}]
    kept, dropped = RR.drop_labelled_other(boxes, other)
    assert [k["conf"] for k in kept] == [0.49] and dropped[0]["why"] == "Bedroom"
    assert len(RR.shower_labels(toks, kept)) == 1


def test_stroke_region_replaces_a_truncated_legacy_one():
    def room(px, ok=True):
        return RA.Room("KPH", None, None, None, (0, 0), pixels=px, region_ok=ok, contour="c%d" % px)
    legacy, stroke = [room(1000), room(1000), room(1000, False)], [room(1500), room(1050), room(900)]
    hyps = [("walls@43", 43, legacy), ("stroke@43", 43, stroke), ("stroke@90", 90, [room(1), room(1), room(1)])]
    assert RA.merge_stroke(hyps, "walls@43", legacy, 43.0) == 2
    assert [r.pixels for r in legacy] == [1500, 1000, 900] and legacy[0].extra["walls"] == "stroke"
    assert RA.merge_stroke(hyps, "stroke@43", legacy, 43.0) == 0
