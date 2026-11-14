"""Effect of raster_area.retry_narrow (rooms whose region holds under 75% of the printed area are measured again
with a smaller doorway bridge) on plans that print room areas.

For every n-th plan of out/objscale/regions: analyse() with the retry off, then the retry on a copy of its rooms.
Reports, over every printed room with a region, the share of triggers and accepted retries and the error of the
measured region against the printed area before and after, and how many printed values stop disagreeing (more than
10%) with their region.

    python tools/narrow_eval.py --data <root> --regions out/objscale/regions --stride 5 [--out rows.jsonl]
"""
import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np

from bpqa.data import data_root
from bpqa.routes import raster_area as RA


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    ap.add_argument("--regions", default="out/objscale/regions")
    ap.add_argument("--stride", type=int, default=5)
    ap.add_argument("--out")
    a = ap.parse_args()
    root = data_root(a.data)
    names = [json.loads(p.read_text(encoding="utf-8"))["file"] for p in sorted(Path(a.regions).glob("*.json"))][::a.stride]
    RA.NARROW_RETRY = False
    rows, plans = [], 0
    for n in names:
        f = root / "data" / "cubicasa5k" / n
        try:
            pa = RA.analyse(str(f), allow_run=False)
            if pa is None or not pa.scale.known or "@" not in pa.regions:
                continue
            orient, toks, extra = RA.plan_tokens(f, allow_run=False)
            img = cv2.imread(str(f))
            proto, free, others = RA.plan_rooms(toks, orient, (img.shape[1], img.shape[0]), extra)
            for r in extra:
                for b in (r.label_box, r.area_box):
                    toks = toks + [{"quad": [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]], "text": "", "conf": 1.0}]
            rooms = copy.deepcopy(pa.rooms)
            before = {i: r.measured for i, r in enumerate(rooms)}
            RA.retry_narrow(img, toks, others, rooms, pa.regions, pa.scale)
        except Exception as e:      # noqa: BLE001
            print("fail", n, e, flush=True)
            continue
        plans += 1
        for i, r in enumerate(rooms):
            if r.printed and r.region_ok and before[i] is not None:
                rows.append({"file": n, "label": r.label, "printed": r.printed, "before": before[i], "after": r.measured,
                             "retried": r.extra.get("retried")})
    eb = np.array([abs(r["before"] - r["printed"]) / r["printed"] for r in rows])
    ea = np.array([abs(r["after"] - r["printed"]) / r["printed"] for r in rows])
    ch = [r for r in rows if r["retried"]]
    print(f"plans {plans}, printed rooms with a region {len(rows)}, retried {len(ch)} in {len({r['file'] for r in ch})} plans")
    print(f"error vs printed, all rooms: before median {np.median(eb):.3f} within10 {np.mean(eb <= .1):.3f}; "
          f"after median {np.median(ea):.3f} within10 {np.mean(ea <= .1):.3f}")
    if ch:
        cb = np.array([abs(r["before"] - r["printed"]) / r["printed"] for r in ch])
        ca = np.array([abs(r["after"] - r["printed"]) / r["printed"] for r in ch])
        print(f"retried rooms: before median {np.median(cb):.2f}, after {np.median(ca):.2f}; "
              f"printed values newly agreeing within 10%: {int(((cb > .1) & (ca <= .1)).sum())}")
    if a.out:
        Path(a.out).write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


if __name__ == "__main__":
    main()
