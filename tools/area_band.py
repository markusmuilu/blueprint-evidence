"""Error band of measured room areas, from the rows `tools/area_eval.py --out` writes (leave-one-out).

    python tools/area_band.py out/rarea/loo_dev.jsonl [more.jsonl ...]

Prints the distribution overall and by measured area (the size a measured answer would state), and the
BANDS literal that `bpqa/routes/raster_area.py` quotes in its answers. Update that literal when the
region code or the scale rule changes.
"""
import json
import sys

import numpy as np

CUTS = (3.0, 6.0)      # measured area classes: under 3 m2 (not answered), 3 to 6, 6 and over


def stats(a, e):
    return {"n": int(len(a)), "median": round(float(np.median(a)), 3), "p80": round(float(np.percentile(a, 80)), 3),
            "p90": round(float(np.percentile(a, 90)), 3), "within5": round(float(np.mean(a <= .05)), 3),
            "within10": round(float(np.mean(a <= .10)), 3), "within20": round(float(np.mean(a <= .20)), 3),
            "signed": round(float(np.median(e)), 3)}


def main(paths):
    rows = [json.loads(line) for p in paths for line in open(p, encoding="utf-8") if line.strip()]
    e = np.array([r["err"] for r in rows])
    a = np.abs(e)
    p = np.array([r["pred"] for r in rows])
    print(f"rooms {len(a)}, plans {len({r['file'] for r in rows})}")
    out = {"all": stats(a, e)}
    for name, m in (("under3", p < CUTS[0]), ("3to6", (p >= CUTS[0]) & (p < CUTS[1])), ("6up", p >= CUTS[1])):
        if m.sum():
            out[name] = stats(a[m], e[m])
    for k, v in out.items():
        print(f"{k:7s} {v}")
    print("BANDS =", json.dumps(out))


if __name__ == "__main__":
    main(sys.argv[1:])
