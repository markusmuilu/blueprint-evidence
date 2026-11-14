"""Scale assumption (CHANGELOG "scale assumption"): the size behind an approximation, set in the question or in the
Approximation box; the parse, the server-side scale with the person's sizes, the sheet conversion, and the app's
instant rescale (the scale-assumption block of app/static/app.js, run in node)."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from bpqa import scale_objects as OS
from bpqa import sheet as S
from bpqa.takeoff import parse_assumption

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- the question

@pytest.mark.parametrize("q, said, rest", [
    ("how big are the bedrooms if the doors are 0.9 m wide", {"door_m": 0.9}, "how big are the bedrooms"),
    ("kitchen area, scale 1:100 on A3", {"ratio": 100, "sheet": "A3"}, "kitchen area"),
    ("What is the bathroom area? The WC is 0.7 m long", {"wc_m": 0.7}, "What is the bathroom area?"),
    ("bedroom area assuming 90 cm doors", {"door_m": 0.9}, "bedroom area"),
    ("bedroom area, door width 900 mm", {"door_m": 0.9}, "bedroom area"),
    ("area of the bathroom with doors 0.8 m wide and the bathtub is 1.6 m long", {"door_m": 0.8, "bath_m": 1.6},
     "area of the bathroom"),
    ("classroom area at scale 1:200, sheet A2", {"ratio": 200, "sheet": "A2"}, "classroom area"),
    ("kitchen area at 1:50", {"ratio": 50}, "kitchen area"),
    ("makuuhuoneiden pinta-ala jos ovet ovat 0,9 m leveitä", {"door_m": 0.9}, "makuuhuoneiden pinta-ala"),
])
def test_parse(q, said, rest):
    assert parse_assumption(q) == (said, rest)


@pytest.mark.parametrize("q", [
    "how many doors are 0.9 m wide",          # a question about doors, not an assumption
    "How many doors are there?",
    "What is the scale of this drawing?",
    "bedroom area if the doors are 3 m wide",  # out of range: not read, the question stays whole
    "what is the area at the kitchen",
])
def test_parse_leaves_questions(q):
    assert parse_assumption(q) == ({}, q)


def test_nothing_left_keeps_the_question():
    assert parse_assumption("the WC is 0.7 m long") == ({"wc_m": 0.7}, "the WC is 0.7 m long")


def test_norm_assume():
    assert OS.norm_assume(None) is None
    assert OS.norm_assume({}) == {}
    assert OS.norm_assume({"door_m": "0.904", "ratio": 99.6, "sheet": "a3", "x": 1}) == {"door_m": 0.9, "ratio": 100, "sheet": "A3"}
    assert OS.norm_assume({"door_m": 3, "wc_m": 0.2, "ratio": 1, "sheet": "B4"}) == {}
    assert OS.assume_key({"door_m": 0.84}) == ()          # the default is no assumption (same cache entry)
    assert OS.assume_key({"door_m": 0.9, "ratio": 100}) == (("door_m", 0.9),)


# ---------------------------------------------------------------- the scale with the person's sizes

def _dets():
    # 4 door openings 40 px long (0.84 m -> 0.021 m/px) and 1 WC pan 35 px long (0.73 m -> 0.02086 m/px)
    doors = [{"cls": "door", "conf": 0.9, "xyxy": [0, i * 50, 40, i * 50 + 6]} for i in range(4)]
    wc = [{"cls": "toilet", "conf": 0.9, "xyxy": [100, 0, 112, 35]}]
    return {"door": doors, "toilet": wc}


def test_door_width_moves_only_the_doors():
    base = OS.combine(OS.candidates(_dets()))
    mine = OS.combine(OS.candidates(_dets(), OS.objects_with({"door_m": 0.9})))
    assert base.known and mine.known
    assert base.m_per_px == pytest.approx(0.84 / 40)
    # median of 4 doors at 0.9/40 and the WC at 0.73/35: the doors carry the median, the WC does not move
    assert mine.m_per_px == pytest.approx(np.median([0.9 / 40] * 4 + [0.73 / 35]))
    assert [c["metres"] for c in mine.used] == [0.9] * 4 + [0.73]
    # the app recomputes the same median from the candidates in the answer
    cands = [{"cls": c["cls"], "px": c["px"], "metres": c["metres"]} for c in base.used]
    assert OS.median_m_per_px(cands, {"door_m": 0.9}) == pytest.approx(mine.m_per_px)


def test_even_count_median_is_the_mean_of_the_middle_two():
    cands = [{"cls": "door", "px": 40.0, "metres": 0.84}, {"cls": "door", "px": 42.0, "metres": 0.84},
             {"cls": "toilet", "px": 35.0, "metres": 0.73}, {"cls": "door", "px": 41.0, "metres": 0.84}]
    v = sorted([0.9 / 40, 0.9 / 42, 0.73 / 35, 0.9 / 41])
    assert OS.median_m_per_px(cands, {"door_m": 0.9}) == pytest.approx((v[1] + v[2]) / 2)


def test_labels_say_your_value():
    class Sc:                        # the parts of raster_area.Scale the texts read
        pass
    sc = Sc()
    sc.objects = OS.tag_objects(OS.candidates(_dets(), OS.objects_with({"door_m": 0.9})))
    sc.source, sc.spread = "objects", 0.05
    sc.m2_per_px = OS.combine(sc.objects).m_per_px ** 2
    sc.assumed = {"door_m": 0.9}
    assert OS.scale_short(sc) == "4 doors x 0.90 m (your value) + 1 WC x 0.73 m"
    assert "assumed 0.90 m wide (your value, default 0.84 m)" in OS.scale_sentence(sc)
    p = OS.assume_payload(sc, {"door_m": 0.9})
    assert p["kind"] == "objects" and p["assumed"] == {"door_m": 0.9} and p["using"] == {"door_m": 0.9, "wc_m": 0.73}
    assert p["counts"] == {"door_m": 4, "wc_m": 1, "bath_m": 0} and len(p["candidates"]) == 5


def test_printed_door_sizes_ignore_an_assumed_width():
    class Sc:
        pass
    sc = Sc()
    sc.objects = [{"cls": "door_module", "xyxy": [0, 0, 40, 6], "px": 40.0, "text": "9x21", "w_m": 0.9, "metres": 0.846}]
    sc.source, sc.m2_per_px, sc.assumed = "modules", (0.846 / 40) ** 2, {}
    p = OS.assume_payload(sc, {"door_m": 0.95})
    assert p["kind"] == "modules" and "candidates" not in p and p["ignored"] == ["door_m"]


# ---------------------------------------------------------------- a big sheet: the person's ratio and sheet size

def _sheet_reading():
    sc = S.SheetScale(14000 / 594 * 1000 / 200, "printed ratio and sheet size, scale bar agrees", True, "read",
                      {"denom": 200}, {"name": "A2", "mm": (594, 420), "px_per_mm": 14000 / 594, "box": [0, 0, 1, 1]},
                      {"ppm": 117.9})
    return {"size": [14000, 9899], "scale": sc.as_dict(), "gross_m2": 2000.0,
            "labels": [{"text": "Classroom", "area_m2": 60.0, "perimeter_m": 32.0}, {"text": "Title", "area_m2": None}]}


def test_sheet_ratio_and_sheet():
    d = _sheet_reading()
    d2 = S.assumed(d, {"ratio": 100, "sheet": "A3"})
    # 1:200 on A2 -> 117.85 px/m; 1:100 on A3 -> 14000/420*1000/100 = 333.3 px/m; k = 117.85 / 333.3
    k = (14000 / 594 * 1000 / 200) / (14000 / 420 * 1000 / 100)
    assert d2["labels"][0]["area_m2"] == pytest.approx(round(60 * k * k, 2))
    assert d2["labels"][0]["perimeter_m"] == pytest.approx(round(32 * k, 2))
    assert d2["gross_m2"] == pytest.approx(round(2000 * k * k, 1))
    assert d["labels"][0]["area_m2"] == 60.0                     # the cached reading is not touched
    assert S.scale_of(d2).ppm == pytest.approx(14000 / 420 * 1000 / 100)
    assert S.read_scale(d2).ppm == pytest.approx(S.scale_of(d).ppm)
    p = S.assume_payload(d2)
    assert p["assumed"] and p["ratio"] == 100 and p["sheet"] == "A3" and p["read_ratio"] == 200 and p["read_sheet"] == "A2"
    assert S.assumed(d, {"ratio": 200}) is d                      # the ratio read: nothing changes
    assert S.assumed(d, {"door_m": 0.9}) is d


def test_sheet_without_a_printed_ratio_has_no_control():
    d = _sheet_reading()
    d["scale"] = {**d["scale"], "source": "scale bar", "ratio": None}
    assert S.assume_payload(d) is None and S.assumed(d, {"ratio": 100}) is d


# ---------------------------------------------------------------- the app's instant rescale (node)

def _js(expr_lines: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    src = (REPO / "app" / "static" / "app.js").read_text(encoding="utf-8")
    a, b = src.index("/* ================================================================ BEGIN scale-assumption"), \
        src.index("/* END scale-assumption (app.js)")
    stub = "const state = {}; const esc = (s) => String(s); const m2 = (s) => s; const $ = () => null;\n"
    out = subprocess.run([node, "-e", stub + src[a:b] + "\n" + expr_lines], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_js_rescale_areas_lengths_and_waste():
    tiling = {"value": 40.64, "unit": "m2", "approx": {"rooms": [{"area_m2": 8.24}], "assume": {}},
              "items": [{"n": 1, "floor_m2": 8.24, "perimeter_m": 11.48, "gross_wall_m2": 28.71, "net_wall_m2": 28.71,
                         "openings_m2": 0},
                        {"totals": {"floor_m2": 8.24, "walls_m2": 28.71, "net_m2": 36.95, "waste_pct": 10.0, "waste_m2": 3.69,
                                    "order_m2": 40.64, "value_is": "order quantity incl. waste"}}]}
    floor = {"value": 14.71, "unit": "m2", "approx": {"rooms": [{"area_m2": 14.71}], "assume": {}},
             "items": [{"n": 1, "area_m2": 14.71}]}
    base = {"value": 15.73, "unit": "m", "approx": {"rooms": [], "assume": {}},
            "items": [{"n": 1, "perimeter_m": 15.73, "net_m": 15.73},
                      {"totals": {"unit": "m", "perimeter_m": 15.73, "net_m": 15.73, "waste_pct": 0, "waste_m": 0, "order_m": 15.73}}]}
    r = _js(f"const k = 1.1; console.log(JSON.stringify([rescaleAnswer({json.dumps(tiling)}, k), "
            f"rescaleAnswer({json.dumps(floor)}, k), rescaleAnswer({json.dumps(base)}, k)]));")
    t, f, b = r
    k = 1.1
    tot = t["items"][1]["totals"]
    assert t["items"][0]["floor_m2"] == round(8.24 * k * k, 2)
    assert t["items"][0]["net_wall_m2"] == round(28.71 * k, 2)        # perimeter x height: x k
    assert tot["net_m2"] == round(round(8.24 * k * k, 2) + round(28.71 * k, 2), 2)
    assert tot["waste_m2"] == round(tot["net_m2"] * 0.1, 2) and t["value"] == round(tot["net_m2"] + tot["waste_m2"], 2)
    assert f["value"] == round(14.71 * k * k, 2) and f["items"][0]["area_m2"] == round(14.71 * k * k, 2)
    assert f["approx"]["rooms"][0]["area_m2"] == round(14.71 * k * k, 2)
    assert b["value"] == round(15.73 * k, 2) and b["items"][1]["totals"]["order_m"] == round(15.73 * k, 2)


def test_js_scale_factor_matches_the_server_median():
    cands = [{"cls": "door", "px": 40.0 + i, "metres": 0.84} for i in range(7)] + [{"cls": "toilet", "px": 35.0, "metres": 0.73}]
    m_old = OS.median_m_per_px(cands, {})
    m_new = OS.median_m_per_px(cands, {"door_m": 0.9})
    a = {"approx": {"assume": {"kind": "objects", "candidates": cands, "m_per_px": m_old}}}
    r = _js(f"console.log(JSON.stringify(scaleFactor({json.dumps(a)}, {{door_m: 0.9, wc_m: 0.73}})));")
    assert r == pytest.approx(m_new / m_old)
    sh = {"approx": {"assume": {"kind": "ratio", "px_per_m": 14000 / 594 * 1000 / 200, "long_px": 14000,
                                "sheets": {"A2": 594, "A3": 420}}}}
    r = _js(f"console.log(JSON.stringify(scaleFactor({json.dumps(sh)}, {{ratio: 100, sheet: 'A3'}})));")
    assert r == pytest.approx((14000 / 594 * 1000 / 200) / (14000 / 420 * 1000 / 100))


# ---------------------------------------------------------------- an override keeps the default rooms (QA 14 Nov)

def test_override_keeps_the_rooms_and_changes_only_the_conversion(monkeypatch):
    from bpqa.routes import raster_area as RA
    monkeypatch.setattr(OS, "detections", lambda f, allow_run=True: _dets())
    comb = OS.combine(OS.candidates(_dets()))
    sc = RA.Scale(comb.m_per_px ** 2, [], comb.iqr, "objects (walls@43)", "default")
    sc.objects, sc.source, sc.assumed = OS.tag_objects(comb.used), "objects", {}
    rooms = [RA.Room("MH", None, None, None, (0, 0)), RA.Room("KPH", None, None, None, (5, 5))]
    for r, px in zip(rooms, (30000, 12000)):
        r.pixels, r.region_ok, r.extra = px, True, {}
        r.measured = round(px * sc.m2_per_px, 2)
    pa = RA.PlanAreas(rooms, [], sc, "walls@43")
    for door in (0.7, 0.9, 1.2):
        kept = OS.keep_regions(pa, "plan.png", {"door_m": door})
        k = OS.median_m_per_px([{"cls": c["cls"], "px": c["px"], "metres": c["metres"]} for c in comb.used], {"door_m": door}) / comb.m_per_px
        assert kept.regions == "walls@43" and [r.pixels for r in kept.rooms] == [30000, 12000]
        for r0, r1 in zip(pa.rooms, kept.rooms):
            assert r1.measured == pytest.approx(r0.pixels * sc.m2_per_px * k * k, abs=0.006)
        assert kept.scale.assumed == {"door_m": door} and "your value" in OS.scale_short(kept.scale)
    assert pa.rooms[0].measured == round(30000 * sc.m2_per_px, 2)        # the cached default is not touched
    printed = RA.PlanAreas(rooms, [], RA.Scale(0.0004, [0, 1], 0.01, "printed-areas", ""), "walls@43")
    assert OS.keep_regions(printed, "plan.png", {"door_m": 0.9}) is printed      # printed areas assume nothing
    assert OS.keep_regions(RA.PlanAreas(rooms, [], RA.Scale(None, [], 0, "none", ""), "x"), "plan.png", {"door_m": 0.9}) is None
