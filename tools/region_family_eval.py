"""Which wall mask (legacy or stroke) the object-scale approximation should trace its rooms with.

Region sets are built per family (raster_area.hypotheses names them "walls@ppm" for the legacy mask and
"stroke@ppm" for the thick-stroke mask) on every plan of out/objscale/regions' pool, cached one JSON per plan.
Then, with the object scale from the plan's standard objects (printed areas ignored), every room with a printed
area is measured under each policy and scored against the printed value (leave-one-out: the printed areas play no
part in the scale or the choice).

    python tools/region_family_eval.py build --data <root> --regions out/objscale/regions --out <cache dir>
    python tools/region_family_eval.py eval  --data <root> --regions out/objscale/regions --out <cache dir>
"""
import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np

from bpqa import scale_objects as OS
from bpqa.data import data_root


def build_one(args):
    f, out = args
    p = Path(out) / (Path(f).stem + ".json")
    if p.exists():
        return
    import cv2
    from bpqa.routes import raster_area as RA
    try:
        orient, toks, extra = RA.plan_tokens(Path(f), allow_run=False)
        img = cv2.imread(str(f))
        if toks is None or img is None:
            return
        proto, free, others = RA.plan_rooms(toks, orient, (img.shape[1], img.shape[0]), extra)
        hyps = RA.hypotheses(img, toks, proto, others, mode="walls")
        res = {}
        for name, ppm, rooms in hyps:
            res[name] = [{"label": r.label, "printed": r.printed, "pixels": r.pixels, "ok": r.region_ok,
                          "thin": r.extra.get("walls")} for r in rooms]
        p.write_text(json.dumps(res), encoding="utf-8")
    except Exception as e:      # noqa: BLE001
        print("fail", f, e, flush=True)


GROW = 1.15      # the stroke region replaces the legacy one when it is this much larger


def families(h):
    """{family: {ppm: rooms}}"""
    out = {"walls": {}, "stroke": {}}
    for name, rooms in h.items():
        fam, ppm = name.split("@")
        out[fam][float(ppm)] = rooms
    return out


def pick(fam, ppm):
    if not fam:
        return None
    k = min(fam, key=lambda p: abs(np.log(p / ppm)))
    return fam[k]


POLICIES = {
    "legacy": lambda L, S: L if L is not None else S,
    "stroke": lambda L, S: S if S is not None else L,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "eval"])
    ap.add_argument("--data")
    ap.add_argument("--regions", default="out/objscale/regions")
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--stride", type=int, default=1, help="every n-th plan only")
    a = ap.parse_args()
    root = data_root(a.data)
    od = Path(a.out)
    od.mkdir(parents=True, exist_ok=True)
    names = [json.loads(p.read_text(encoding="utf-8"))["file"] for p in sorted(Path(a.regions).glob("*.json"))]
    files = [root / "data" / "cubicasa5k" / n for n in names[::a.stride]]
    if a.cmd == "build":
        with ProcessPoolExecutor(a.jobs) as ex:
            list(ex.map(build_one, [(str(f), str(od)) for f in files]))
        return
    rows = []
    for f in files:
        p = od / (f.stem + ".json")
        if not p.exists():
            continue
        h = json.loads(p.read_text(encoding="utf-8"))
        dets = OS.detections(f, allow_run=False)
        comb = OS.combine(OS.candidates(dets))
        if not comb.known:
            continue
        ppm = 1 / comb.m_per_px
        fm = families(h)
        L, S = pick(fm["walls"], ppm), pick(fm["stroke"], ppm)
        if S is None:
            continue                      # no thick strokes: both policies are the legacy mask
        m2 = comb.m_per_px ** 2
        for i, rl in enumerate(L or []):
            rs = S[i]
            if rl["printed"] is None:
                continue
            row = {"file": f.name, "label": rl["label"], "printed": rl["printed"]}
            lg = rs if (rs["ok"] and (not rl["ok"] or rs["pixels"] > GROW * rl["pixels"])) else rl
            for name, r in (("legacy", rl), ("stroke", rs), ("larger", lg)):
                row[name] = (r["pixels"] * m2 - rl["printed"]) / rl["printed"] if r["ok"] else None
            rows.append(row)
    print("plans with a stroke mask and an object scale:", len({r["file"] for r in rows}), "rooms", len(rows))
    for pol in ("legacy", "stroke", "larger"):
        e = np.array([abs(r[pol]) for r in rows if r[pol] is not None])
        print(pol, "answered", len(e), "median", round(float(np.median(e)), 3), "p80", round(float(np.percentile(e, 80)), 3),
              "within10", round(float((e <= .1).mean()), 3), "signed median",
              round(float(np.median([r[pol] for r in rows if r[pol] is not None])), 3))
    both = [r for r in rows if r["legacy"] is not None and r["larger"] is not None]
    el, es = np.array([abs(r["legacy"]) for r in both]), np.array([abs(r["larger"]) for r in both])
    print("rooms closed under legacy", len(both), "legacy median", round(float(np.median(el)), 3), "larger-of median",
          round(float(np.median(es)), 3), "larger-of better in", int((es < el - 1e-9).sum()), "worse in", int((es > el + 1e-9).sum()),
          "changed", int((np.abs(es - el) > 1e-9).sum()))
    Path(a.out, "rows.json").write_text(json.dumps(rows), encoding="utf-8")


if __name__ == "__main__":
    main()
