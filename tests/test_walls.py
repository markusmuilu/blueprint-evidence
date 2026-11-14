"""Wall-surface takeoff: inputs read from the question, surfaces, and the arithmetic. python tests/test_walls.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa import walls as W  # noqa: E402
from bpqa.query import parse  # noqa: E402


def check(cond, msg, bad):
    if not cond:
        print("FAIL", msg)
        bad.append(msg)


def main():
    bad = []
    i = W.parse_inputs("How many m2 of tiles should I order for the bathrooms with a ceiling height of 2.6 m and 15% waste?")
    check(i.get("ceiling_m") == 2.6 and i.get("waste_pct") == 15 and i["order"], f"inputs {i}", bad)
    i = W.parse_inputs("Wall tiles to 1.2 m in the WC, no waste")
    check(i.get("tile_m") == 1.2 and i.get("waste_pct") == 0 and i["order"], f"inputs {i}", bad)   # material asked: order (0% waste)
    for q in ("tiles to 1.2 m height for the bathroom", "flooring for the bedrooms with 10% waste"):
        check(W.parse_inputs(q)["order"], f"order intent {q}", bad)       # QA 14 Nov: waste was left out of the value
    for q in ("What is the floor area of the bathroom?", "How much tiling area does the bathroom have?"):
        check(not W.parse_inputs(q)["order"], f"net intent {q}", bad)
    i = W.parse_inputs("tiling height 210 cm for the bathroom walls")
    check(abs(i.get("tile_m", 0) - 2.1) < 1e-9, f"inputs {i}", bad)

    tk = parse("How many square meters of tiling should I order for the toilets?").extra["takeoff"]
    check(W.surfaces(tk)[:2] == (True, True), "toilet tiles = floor + walls", bad)
    tk = parse("How many m2 of floor tiles for the bathrooms?").extra["takeoff"]
    check(W.surfaces(tk)[:2] == (True, False), "floor tiles = floor only", bad)
    tk = parse("How many m² of wall tiles for the bathroom?").extra["takeoff"]
    check(W.surfaces(tk)[:2] == (False, True), "wall tiles = walls only", bad)
    tk = parse("How many m2 of flooring do I need for the bedrooms?").extra["takeoff"]
    check(W.surfaces(tk)[:2] == (True, False), "flooring = floor only", bad)

    # 4 x 3 m room, 2.5 m high: gross 35; a 0.9 x 2.0 door; a 1.0 m window from 1.0 to 2.2 m
    room = {"label": "Bath", "floor_area": 12.0, "perimeter": 14.0, "height": 2.5, "height_src": "model",
            "openings": [{"kind": "door", "width": 0.9, "z0": 0.0, "z1": 2.0},
                         {"kind": "window", "width": 1.0, "z0": 1.0, "z1": 2.2}]}
    w = W.room_walls(room, {})
    check(abs(w["gross"] - 35.0) < 1e-9 and abs(w["net"] - (35 - 1.8 - 1.2)) < 1e-9, f"full height {w}", bad)
    w = W.room_walls(room, {"tile_m": 1.2})
    check(abs(w["gross"] - 16.8) < 1e-9 and abs(w["net"] - (16.8 - 0.9 * 1.2 - 1.0 * 0.2)) < 1e-9, f"tiled to 1.2 m {w}", bad)
    tk = parse("How many square meters of tiling should I order for the bathrooms?").extra["takeoff"]
    v, items, expl, tot = W.summary([room], tk, "How many square meters of tiling should I order for the bathrooms?")
    check(abs(v - round((12 + 32.0) * 1.10, 2)) < 0.011 and tot["waste_pct"] == 10, f"order value {v} {tot}", bad)
    v, *_ = W.summary([room], tk, "What is the tile area of the bathrooms?")
    check(abs(v - 44.0) < 0.011, f"net value {v}", bad)
    room2 = dict(room, height=None)
    v, items, expl, tot = W.summary([room2], tk, "tiles for the bathrooms")
    check("ASSUMED" in items[0]["height_source"] and items[0]["height_m"] == W.DEFAULT_CEILING_M, "assumed height stated", bad)
    print("ok" if not bad else f"{len(bad)} failures")
    return len(bad)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
