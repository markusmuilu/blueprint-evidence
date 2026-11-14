"""The apartment summary line printed on Finnish scans: rooms, kitchen type, other spaces, flat area."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.routes import raster_summary as RS  # noqa: E402
from bpqa.routes.raster_area import read_rooms  # noqa: E402


def tok(text, x0, y0, x1, y1, conf=0.95):
    return {"text": text, "conf": conf, "quad": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], "rot": 0}


def test_parse_variants():
    cases = {"3H+KT+S": (3, ["KT", "S"], None), "3H+K+S72m2": (3, ["K", "S"], 72.0), "4H+K80.0m²": (4, ["K"], 80.0),
             "1 H + TUPAK. 42.5m2": (1, ["TUPAK"], 42.5), "2H+KK / 48,0 m2": (2, ["KK"], 48.0), "6H+K= 176 m2": (6, ["K"], 176.0),
             "3h+k+s!": (3, ["K", "S"], None)}
    for text, (n, comps, area) in cases.items():
        assert RS._parse_token(text) == (n, comps, area), text
    for bad in ("4.2H+K+S5G.5", "B34H+K+S81m", "2H", "MH 1", "73,50 m2"):
        assert RS._parse_token(bad) is None, bad


def test_area_in_its_own_token_and_not_a_room():
    toks = [tok("3H+KT+S", 98, 628, 183, 646), tok("73,50 m2", 106, 608, 184, 629),
            tok("OH", 105, 392, 157, 408), tok("22,9 m2", 104, 372, 158, 392), tok("MH 1", 343, 296, 382, 312),
            tok("11,0 m2", 326, 272, 382, 292)]
    s = RS.find_summary(toks)
    assert s["rooms"] == 3 and s["area"] == 73.5 and s["kitchen"] == "kitchen space" and s["extras"] == ["sauna"]
    assert RS.rooms_phrase(s) == "3 rooms plus kitchen space plus sauna"
    rooms, _ = read_rooms(toks)
    assert sorted(r.printed for r in rooms) == [11.0, 22.9]          # 73.5 is not a room


def test_neighbouring_room_area_is_not_the_flat():
    toks = [tok("2H+K", 10, 100, 80, 120), tok("7,5 m2", 10, 125, 80, 145), tok("MH", 200, 100, 230, 120), tok("11,0 m2", 200, 125, 260, 145)]
    s = RS.find_summary(toks)
    assert s["rooms"] == 2 and s["area"] is None
