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
    sc.objects = OS.tag_objects(OS.candidates(dets(door(0, 0), door(50, 0), door(100, 0), toilet(0, 50))))
    monkeypatch.setattr(RA, "analyse", lambda *_a, **_k: RA.PlanAreas(rooms, [], sc, "walls@43"))
    qtext = "How many square meters of flooring do I need for the bedrooms?"
    ctx = SimpleNamespace(file=Path("none.png"), q=SimpleNamespace(path="data/x/none.png", question=qtext),
                          evidence=False, out_dir=Path("."))
    a = RA.takeoff_answer(ctx, parse(qtext), None)
    assert a.answered and a.tier == "estimate"
    assert abs(a.value - 21.25) < 0.1
    assert "OBJECT SCALE" in a.explanation and "Scale from printed rooms" not in a.explanation
    assert "object scale" in a.explanation and "leave-one-out" in a.explanation
    # the approximation sentence comes first and says where the number comes from
    assert a.explanation.startswith("About 21 m2 (approximation): this drawing prints no room areas")
    assert "3 door openings assumed 0.84 m wide (doors D1 to D3)" in a.explanation
    assert "1 WC pan assumed 0.73 m long (WC1)" in a.explanation
    assert "40 px per m, spread 5%" in a.explanation and "Typical error for rooms of these sizes: about 12%" in a.explanation
    assert a.explanation.count("Check before ordering") >= 1
    assert a.approx and a.approx["source"] == "objects" and len(a.approx["objects"]) == 4
    assert "approximation, never proven" in a.tier_reason
    assert all(it.get("area_kind") == "measured" for it in a.items if "area_kind" in it)


def test_on_by_default_without_env(monkeypatch):
    # the switch is an environment flag read at import; the hook in raster_area only runs when it is on
    import importlib
    monkeypatch.delenv("STOA_OBJECT_SCALE", raising=False)
    mod = importlib.reload(OS)
    try:
        assert mod.ENABLED is mod.DEFAULT_ON is True
    finally:
        importlib.reload(OS)


# ---------------------------------------------------------------- printed door sizes (scale source b)

def tok(text, x, y, w=30, h=10, conf=0.9):
    return {"text": text, "conf": conf, "quad": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]}


def test_module_tokens_doors_only():
    toks = [tok("9x21", 0, 0), tok("09*21", 100, 0), tok("O 10x21", 200, 0), tok("010", 300, 0),
            tok("3-15x14", 400, 0), tok("12x12", 500, 0), tok("10x2", 600, 0), tok("9x21", 0, 0, conf=0.6)]
    m = OS.module_tokens(toks)
    assert [(x["text"], x["w_m"]) for x in m] == [("9x21", 0.9), ("09*21", 0.9), ("O 10x21", 1.0), ("010", 1.0)]
    assert m[3]["h_m"] is None                      # a width-only label
    # window modules and partial reads never count; the same label read twice is kept once


def test_module_candidates_and_agreement():
    k = OS.BOX_PER_MODULE
    # doors drawn at PPM: a 9M door box is 0.9 * k * PPM px long
    d1 = door(0, 100, metres=0.9 * k)
    d2 = door(200, 100, metres=1.0 * k)
    d3 = door(400, 100, metres=0.9 * k)
    mods = OS.module_tokens([tok("9x21", 5, 80), tok("10x21", 205, 80), tok("9x21", 405, 80), tok("9x21", 900, 900)])
    c = OS.module_candidates(mods, [d1, d2, d3])
    assert len(c) == 3 and all(x["cls"] == "door_module" for x in c)     # the far label matches no door
    comb = OS.combine_modules(c)
    assert comb.known and comb.n == 3 and abs(1 / comb.m_per_px - PPM) / PPM < 0.01
    # one printed size alone is not a scale; two that disagree are not either
    assert not OS.combine_modules(c[:1]).known
    bad = OS.module_candidates(OS.module_tokens([tok("9x21", 5, 80), tok("9x21", 205, 80)]),
                               [d1, door(200, 100, metres=1.5)])
    assert not OS.combine_modules(bad).known


def test_printed_sizes_before_standard_objects(monkeypatch):
    k = OS.BOX_PER_MODULE
    hyps = [(f"walls@{p}", p, [f"rooms@{p}"]) for p in RA.PPM_GRID]
    doors = [door(0, 100, metres=0.9 * k), door(200, 100, metres=0.9 * k), toilet(0, 300), door(400, 100), door(600, 100)]
    monkeypatch.setattr(OS, "detections", lambda f, allow_run=True: dets(*doors))
    monkeypatch.setattr(OS, "plan_module_tokens", lambda f, allow_run=True, mirror=False:
                        OS.module_tokens([tok("9x21", 5, 80), tok("9x21", 205, 80)]))
    name, rooms, sc = OS.object_scale("x.png", hyps)
    assert sc.source == "modules" and sorted(c["tag"] for c in sc.objects) == ["D1", "D2"]
    s = OS.scale_sentence(sc)
    assert "2 door sizes printed on the drawing (9x21, openings 0.9 m)" in s and "doors D1 and D2" in s
    # no printed sizes: the standard objects, and the reason the printed sizes were not used is kept
    monkeypatch.setattr(OS, "plan_module_tokens", lambda f, allow_run=True, mirror=False: [])
    name, rooms, sc = OS.object_scale("x.png", hyps)
    assert sc.source == "objects" and "printed door sizes: 0 printed door size(s)" in sc.detail


def test_approximation_evidence_steps(monkeypatch, tmp_path):
    import json
    from bpqa.evidence import marks_path
    m2 = 1 / PPM ** 2
    r = RA.Room("MH1", (110, 110, 130, 120), None, None, (120, 130), pixels=140 * 140, region_ok=True,
                contour=square(140) + 50)
    r.measured = round(r.pixels * m2, 2)
    sc = RA.Scale(m2, [], 0.05, "objects (walls@43)", "OBJECT SCALE (weaker than printed areas)")
    sc.objects, sc.source = OS.tag_objects(OS.candidates(dets(door(10, 10), door(60, 10), door(110, 10), door(160, 10)))), "objects"
    monkeypatch.setattr(RA, "analyse", lambda *_a, **_k: RA.PlanAreas([r], [], sc, "walls@43"))
    qtext = "How big is the bedroom?"
    ctx = SimpleNamespace(file=Path("none.png"), q=SimpleNamespace(path="data/x/none.png", question=qtext),
                          evidence=True, out_dir=tmp_path)
    a = RA.takeoff_answer(ctx, parse(qtext), np.full((260, 300, 3), 255, np.uint8))
    assert a.answered and a.tier == "estimate" and a.evidence
    mk = json.loads(marks_path(Path(a.evidence)).read_text(encoding="utf-8"))
    st = mk["steps"]["1"]
    assert [x["kind"] for x in st] == ["label", "scale", "region"]
    assert st[0]["title"] == "Room found" and "bedroom" in st[0]["note"]
    assert st[1]["text"] == "4 doors x 0.84 m" and st[1]["note"] == "40 px/m (spread 5%)" and len(st[1]["rects"]) == 4
    assert st[1]["caps"][0] == "D1 door 0.84 m"
    assert st[2]["title"] == "Room outline" and st[2]["note"].startswith("about 12 m2")
    so = [m for m in mk["marks"] if m["kind"] == "scale_object"]
    assert len(so) == 4 and all(m["tone"] == "amber" and m["label"].endswith("door 0.84 m") for m in so)
    # scale objects are context: never pinned as answer items
    assert not [m for m in mk["marks"] if m["kind"] == "box" and m.get("tone") == "amber" and m.get("n") is None]
