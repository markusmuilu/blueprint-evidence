"""Object scale (bpqa/scale_objects.py): candidates from detector boxes, agreement rule, region choice, and the
raster takeoff answer it feeds (always an estimate, naming the objects, the scale and the band)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa import scale_objects as OS  # noqa: E402
from bpqa.query import parse  # noqa: E402
from bpqa.routes import raster_area as RA  # noqa: E402

PPM = 40.0      # pixels per metre of the synthetic plan


def door(x, y, conf=0.9, vertical=False, metres=None):
    L = (metres or OS.OBJECTS["door"]["metres"]) * PPM
    w, h = (6, L) if vertical else (L, 6)
    return {"cls": "door", "conf": conf, "xyxy": [x, y, x + w, y + h]}


def toilet(x, y, conf=0.9):
    L = OS.OBJECTS["toilet"]["metres"] * PPM
    return {"cls": "toilet", "conf": conf, "xyxy": [x, y, x + L, y + L / 1.7]}


def dets(*boxes):
    out = {}
    for b in boxes:
        pol = OS.OBJECTS[b["cls"]]["policy"]
        out.setdefault(pol, []).append(b)
    return out


def test_candidates_filter():
    c = OS.candidates(dets(door(0, 0), door(100, 0, vertical=True), toilet(200, 0)))
    assert [x["cls"] for x in c] == ["door", "door", "toilet"]
    assert all(abs(x["m_per_px"] - 1 / PPM) < 1e-3 for x in c)
    # a low score, or a door box that is not elongated (swing or double leaf), gives no candidate
    sq = {"cls": "door", "conf": 0.9, "xyxy": [0, 0, 34, 30]}
    assert OS.candidates(dets(door(0, 0, conf=0.1), sq)) == []
    # a sink or a shower is not a standard object
    assert OS.candidates({"sink": [{"cls": "sink", "conf": 0.9, "xyxy": [0, 0, 30, 10]}]}) == []


def test_combine_agreement():
    c = OS.candidates(dets(door(0, 0), door(50, 0, metres=0.83), door(100, 0, metres=0.87), toilet(0, 50)))
    comb = OS.combine(c)
    assert comb.known and comb.n == 4
    assert abs(1 / comb.m_per_px - PPM) / PPM < 0.03
    # too few objects
    assert not OS.combine(c[:2]).known
    # objects that disagree (two plans pasted at different scales): no scale, with the reason
    bad = OS.candidates(dets(door(0, 0), door(50, 0), door(100, 0, metres=1.6), door(150, 0, metres=1.7)))
    comb = OS.combine(bad)
    assert not comb.known and "disagree" in comb.why


def test_pick_regions_nearest():
    hyps = [(f"walls@{p}", p, [p]) for p in RA.PPM_GRID]
    assert OS.pick_regions(hyps, 41.0) == ("walls@43", [43])
    assert OS.pick_regions(hyps, 25.0) == ("walls@24", [24])


def test_object_scale_hook(monkeypatch):
    hyps = [(f"walls@{p}", p, [f"rooms@{p}"]) for p in RA.PPM_GRID]
    monkeypatch.setattr(OS, "detections", lambda f, allow_run=True: dets(door(0, 0), door(50, 0), door(100, 0), toilet(0, 50)))
    name, rooms, sc = OS.object_scale("x.png", hyps)
    assert name == "walls@43" and rooms == ["rooms@43"]
    assert sc.known and OS.is_object(sc) and sc.method.startswith("objects")
    assert abs(sc.m2_per_px - 1 / PPM ** 2) / (1 / PPM ** 2) < 0.06
    assert "OBJECT SCALE" in sc.detail and "3 door openings" in sc.detail and "1 WC pan" in sc.detail
    monkeypatch.setattr(OS, "detections", lambda f, allow_run=True: dets(door(0, 0)))
    name, rooms, why = OS.object_scale("x.png", hyps)
    assert rooms is None and "needed" in why


def square(side):
    return np.array([[[0, 0]], [[side, 0]], [[side, side]], [[0, side]]], dtype=np.int32)


def test_takeoff_with_object_scale(monkeypatch):
    m2 = 1 / PPM ** 2
    rooms = []
    for i, (lab, side) in enumerate((("MH1", 140), ("MH2", 120))):     # 12.25 and 9.0 m2, nothing printed
        box = (10 * i, 10, 10 * i + 8, 18)
        r = RA.Room(lab, box, None, None, (10 * i, 10), pixels=side * side, region_ok=True, contour=square(side))
        r.measured = round(r.pixels * m2, 2)
        rooms.append(r)
    sc = RA.Scale(m2, [], 0.05, "objects (walls@43)", "OBJECT SCALE (weaker than printed areas): median of 4 standard objects")
    sc.objects = OS.candidates(dets(door(0, 0), door(50, 0), door(100, 0), toilet(0, 50)))
    monkeypatch.setattr(RA, "analyse", lambda *_a, **_k: RA.PlanAreas(rooms, [], sc, "walls@43"))
    qtext = "How many square meters of flooring do I need for the bedrooms?"
    ctx = SimpleNamespace(file=Path("none.png"), q=SimpleNamespace(path="data/x/none.png", question=qtext),
                          evidence=False, out_dir=Path("."))
    a = RA.takeoff_answer(ctx, parse(qtext), None)
    assert a.answered and a.tier == "estimate"
    assert abs(a.value - 21.25) < 0.1
    assert "OBJECT SCALE" in a.explanation and "Scale from printed rooms" not in a.explanation
    assert "object scale" in a.explanation and "leave-one-out" in a.explanation
    assert all(it.get("area_kind") == "measured" for it in a.items if "area_kind" in it)


def test_off_by_default_without_env(monkeypatch):
    # the switch is an environment flag read at import; the hook in raster_area only runs when it is on
    import importlib
    monkeypatch.delenv("STOA_OBJECT_SCALE", raising=False)
    mod = importlib.reload(OS)
    try:
        assert mod.ENABLED is mod.DEFAULT_ON
    finally:
        importlib.reload(OS)
