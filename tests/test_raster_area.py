"""Raster takeoff on a synthetic plan: printed rooms stay printed, measured rooms are estimates with the band
stated, rooms under MIN_MEASURED_M2 are not answered, walls are an estimate with openings not deducted.
python tests/test_raster_area.py"""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.query import parse  # noqa: E402
from bpqa.routes import raster_area as RA  # noqa: E402


def check(cond, msg, bad):
    if not cond:
        print("FAIL", msg)
        bad.append(msg)


def square(side):
    return np.array([[[0, 0]], [[side, 0]], [[side, side]], [[0, side]]], dtype=np.int32)


def room(label, printed, side, i):
    px = side * side
    box = (10 * i, 10, 10 * i + 8, 18)
    return RA.Room(label, box, printed, box if printed is not None else None, (10 * i, 10), pixels=px, region_ok=True,
                   contour=square(side), measured=None)


def plan(rooms, m2_per_px=0.0004):
    for r in rooms:
        r.measured = round(r.pixels * m2_per_px, 2)
    return RA.PlanAreas(rooms, [], RA.Scale(m2_per_px, [0, 1], 0.01, "printed-areas (test)", "2 of 2 rooms agree"), "test")


def ask(question, rooms):
    q = parse(question)
    ctx = SimpleNamespace(file=Path("none.png"), q=SimpleNamespace(path="data/x/none.png", question=question),
                          evidence=False, out_dir=Path("."))
    orig = RA.analyse
    RA.analyse = lambda *_a, **_k: plan(rooms)
    try:
        return RA.takeoff_answer(ctx, q, None)
    finally:
        RA.analyse = orig


def main():
    bad = []
    # scale 0.0004 m2/px: side 173 px = 12 m2, side 141 = 8 m2, side 134 = 7.2 m2, side 70 = 1.96 m2
    base = [room("MH1", 12.0, 173, 0), room("MH2", 8.0, 141, 1)]
    a = ask("How many square meters of flooring do I need for the bedrooms?", base + [room("MH3", None, 134, 2)])
    check(a.answered and a.tier == "estimate", f"measured room makes an estimate: {a.abstain} {a.tier}", bad)
    if a.answered:
        check(abs(a.value - (12.0 + 8.0 + 7.18)) < 0.1, f"sum printed + measured: {a.value}", bad)
        check("MEASURED" in a.explanation and "printed on the plan: MH1 12.00, MH2 8.00" in a.explanation, "printed vs measured stated", bad)
        check("Scale from printed rooms" in a.explanation and "check before ordering" in a.explanation, "scale source and band stated", bad)
        kinds = [it["area_kind"] for it in a.items]
        check(kinds == ["printed", "printed", "measured"], f"item kinds {kinds}", bad)
        check(a.uncertain and a.uncertain[0]["room"] == "MH3", "measured room listed as uncertain", bad)
    a = ask("How many square meters of flooring do I need for the bedrooms?", base)
    check(a.answered and a.tier is None and a.value == 20.0, f"all printed stays printed, no estimate tier: {a.value} {a.tier}", bad)
    a = ask("How many square meters of flooring do I need for the bedrooms?", base + [room("MH3", None, 70, 2)])
    check(not a.answered and "under 3 m2" in a.abstain, f"tiny measured region abstains: {a.abstain}", bad)

    a = ask("How many square meters of paint for the walls and floor of the bedrooms?", base + [room("MH3", None, 134, 2)])
    check(a.answered and a.tier == "estimate", "walls answer is an estimate", bad)
    if a.answered:
        tot = a.items[-1]["totals"]
        per = 4 * 173 * 0.02 + 4 * 141 * 0.02 + 4 * 134 * 0.02       # 0.02 m per px
        check(abs(tot["walls_m2"] - per * 2.5) < 1.0, f"walls = perimeter x 2.5: {tot['walls_m2']} vs {per * 2.5}", bad)
        check(abs(tot["floor_m2"] - 27.18) < 0.1, f"floor line separate: {tot['floor_m2']}", bad)
        check("openings are NOT deducted" in a.explanation and "ASSUMED ceiling height" in a.explanation, "assumptions stated", bad)
        check(all(it.get("openings_not_deducted") for it in a.items[:-1]), "openings flagged per room", bad)
    a = ask("How many square meters of floor tiles do I need for the bedrooms?", base)
    check(a.answered and "walls" not in a.explanation.split("Takeoff")[0], "floor-only question stays floor only", bad)
    a = ask("Wall tiles for the bedrooms with ceiling height 2.4 m", base)
    check(a.answered and abs(a.items[0]["height_m"] - 2.4) < 1e-9, "ceiling height editable from the question", bad)

    nosc = [room("MH1", 12.0, 173, 0), room("MH2", None, 141, 1)]
    orig = RA.analyse
    q = parse("How many m2 of wall tiles for the bedrooms?")
    check(q.extra.get("takeoff") is not None, "wall question parses as takeoff", bad)
    pa = plan(nosc)
    pa.scale = RA.Scale(None, [], 0, "none", "1 room")
    RA.analyse = lambda *_a, **_k: pa
    ctx = SimpleNamespace(file=Path("none.png"), q=SimpleNamespace(path="data/x/none.png", question="How many m2 of wall tiles for the bedrooms?"),
                          evidence=False, out_dir=Path("."))
    a = RA.takeoff_answer(ctx, q, None)
    RA.analyse = orig
    check(not a.answered, "no scale: abstains", bad)
    print("raster_area tests:", "FAIL" if bad else "ok")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
