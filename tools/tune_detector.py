"""Tune the raster-detector count policy on Stoa train-split (dev) images the detector did not train on.

    python tools/tune_detector.py --stoa ../data/stoa-full/junction-dataset --images ../data/detector/ds_cubi/tune_images.txt
    python tools/tune_detector.py ... --model models/detector_tiles.pt --tile 1024 --tile-scale 2 --tta
    python tools/tune_detector.py                     # preview dev images

Per object (toilet, sink, shower_or_bath, door, window), for the given model and detection mode:
  - the score threshold t that maximises exact-count accuracy with every image answered (the count the
    route gives, in either tier)
  - the "proven" tier: an uncertainty band around t, a number k (estimate when k or more boxes score
    inside the band) and, with --tta, the largest spread of per-view counts, chosen together to maximise
    correct minus --lam x wrong among proven answers (lam 1 is the policy used before tiers)
  - an object whose proven tier would still be wrong more often than right gets no proven tier
    (`enabled` false: every answer is an estimate)
With --model a.pt+b.pt the views of both models are fused the same way (an ensemble).
With --tta both ways of combining the eight views are tried (`fuse`: boxes fused across views, `median`:
the median of the per-view counts) and the better one by exact accuracy is kept.
Reports 2-fold cross-validated numbers (fit on one half of the images, measured on the other), which
are the honest ones. Writes the route config (models/detector_thresholds.json by default) and a
report next to it. Test-split answers are never read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from bpqa.data import DEFAULT_DATA, split_of  # noqa: E402
from bpqa.query import parse  # noqa: E402
from bpqa.routes import detector as det  # noqa: E402

TS = [round(0.05 * i, 2) for i in range(1, 19)]
BANDS = [0.05, 0.1, 0.15, 0.2, 0.25]
KS = [1, 2, 3, 99]          # estimate when this many boxes score inside the band (99: never)
SPREADS = [0, 1, 2, 99]     # estimate when per-view counts differ by more than this (TTA only)


def gold_counts(root: Path, paths: set[str]):
    out = defaultdict(dict)
    with open(root / "questions.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["path"] not in paths:
                continue
            assert split_of(r["path"]) == "dev", "tuning reads train-split answers only"
            q = parse(r["question"])
            if q.lang == "en" and q.measure == "count" and q.obj in det.OBJECTS:
                out[r["path"]][q.obj] = int(r["answer"])
    return out


class Rows:
    """Per image: the views' boxes of one object, with the fused list precomputed."""

    def __init__(self, obj, items):
        cl = set(det.OBJECTS[obj])
        self.obj = obj
        self.items = []
        for views, gold in items:
            vs = [[d for d in v if d["cls"] in cl] for v in views]
            self.items.append((vs, det.combine(vs) if len(vs) > 1 else vs[0], gold))

    def subset(self, keep):
        r = Rows.__new__(Rows)
        r.obj, r.items = self.obj, [it for it, k in zip(self.items, keep) if k]
        return r


def evaluate(rows: Rows, c: dict):
    """(answered as proven, proven correct, all correct) for policy c."""
    a = cp = call = 0
    for views, fused, gold in rows.items:
        n, hits, unsure, spread = det.count_views(views, fused, rows.obj, c)
        ok = n == gold
        call += ok
        if not det.policy(hits, unsure, c, spread, n):
            a += 1
            cp += ok
    return a, cp, call


def fit(rows: Rows, agg: str, lam: float, mx: int):
    base = {"agg": agg, "max": mx, "enabled": True, "band": 0.0, "max_unsure": 99, "max_spread": 99}
    best_t = max(TS, key=lambda t: (evaluate(rows, {**base, "conf": t})[2], -abs(t - 0.5)))
    multi = len(rows.items[0][0]) > 1 if rows.items else False
    best, best_s = None, None
    for b in BANDS:
        for k in KS:
            for sp in (SPREADS if multi else [99]):
                c = {**base, "conf": best_t, "band": b, "max_unsure": k, "max_spread": sp}
                a, cp, _ = evaluate(rows, c)
                s = (cp - lam * (a - cp), -b, k, sp)
                if best_s is None or s > best_s:
                    best, best_s = c, s
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stoa", default=str(DEFAULT_DATA), help="dataset root with questions.jsonl")
    ap.add_argument("--images", default="", help="file listing dataset-relative image paths (default: all dev CubiCasa images)")
    ap.add_argument("--model", default=str(det.MODEL),
                    help="detector weights inside models/ (default models/detector.pt); a.pt+b.pt fuses both models' views")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--tile", type=int, default=0, help="tile size for tiled inference (0: whole image)")
    ap.add_argument("--tile-scale", type=float, default=2.0)
    ap.add_argument("--tile-overlap", type=int, default=192)
    ap.add_argument("--tile-max-side", type=int, default=4096)
    ap.add_argument("--tta", action="store_true", help="eight rotations and mirrors")
    ap.add_argument("--lam", type=float, default=1.0, help="cost of a wrong proven answer, in correct ones")
    ap.add_argument("--objects", default=",".join(det.OBJECTS))
    ap.add_argument("--out", default=str(det.THRESHOLDS))
    a = ap.parse_args()
    root = Path(a.stoa)
    models = [Path(m).resolve() if Path(m).parent != Path(".") else (REPO / "models" / m).resolve() for m in a.model.split("+")]
    if any(m.parent != (REPO / "models").resolve() for m in models):
        raise SystemExit("put the weights in models/ (the route loads models/<name>)")
    model_name = "+".join(m.name for m in models)
    tile = {"tile": a.tile, "scale": a.tile_scale, "overlap": a.tile_overlap, "max_side": a.tile_max_side} if a.tile else None
    if a.images:
        paths = {l.strip() for l in Path(a.images).read_text(encoding="utf-8").splitlines() if l.strip()}
    else:
        paths = {f"data/cubicasa5k/{p.name}" for p in (root / "data" / "cubicasa5k").glob("*.png")}
    paths = {p for p in paths if split_of(p) == "dev"}
    gold = gold_counts(root, paths)
    print(f"tuning on {len(gold)} train-split images", file=sys.stderr)
    views = {}
    for i, p in enumerate(sorted(gold)):
        views[p] = det.views_for(root / p, {"model": model_name, "imgsz": a.imgsz, "tile": tile, "tta": a.tta})
        if (i + 1) % 100 == 0:
            print(f"  detected {i + 1}/{len(gold)}", file=sys.stderr)
    mode = {"model": model_name, "imgsz": a.imgsz, "tile": tile, "tta": a.tta}
    out = {"_imgsz": a.imgsz, "_tuned_on": f"{len(gold)} Stoa train-split images", "_model": "+".join(str(det.model_tag(m)) for m in models),
           "_lam": a.lam}
    report = {"_mode": mode}
    for obj in a.objects.split(","):
        ps = sorted(p for p in gold if obj in gold[p])
        if not ps:
            continue
        rows = Rows(obj, [(views[p], gold[p][obj]) for p in ps])
        mx = max(g for _, _, g in rows.items)
        half = [int(hashlib.sha1(p.encode()).hexdigest()[:4], 16) % 2 for p in ps]
        best = None
        for agg in (["fuse", "median"] if a.tta else ["fuse"]):
            c = fit(rows, agg, a.lam, mx)
            n_ans, n_ok, n_all = evaluate(rows, c)
            cv = {"proven": 0, "proven_ok": 0, "all_ok": 0, "est": 0, "est_ok": 0}
            for h in (0, 1):
                tr, te = rows.subset([x == h for x in half]), rows.subset([x != h for x in half])
                ch = fit(tr, agg, a.lam, mx)
                ta, tp, tall = evaluate(te, ch)
                tpa, tpp, _ = evaluate(tr, ch)
                if tpp - (tpa - tpp) <= 0:            # no proven tier on that half: all estimates
                    ta, tp = 0, 0
                cv["proven"] += ta
                cv["proven_ok"] += tp
                cv["all_ok"] += tall
            cand = (cv["all_ok"], agg, c, n_ans, n_ok, n_all, cv)
            if best is None or cand[0] > best[0]:
                best = cand
        _, agg, c, n_ans, n_ok, n_all, cv = best
        enabled = n_ok - (n_ans - n_ok) > 0
        n = len(ps)
        n_est, est_ok = (n - n_ans, n_all - n_ok) if enabled else (n, n_all)
        out[obj] = {**mode, "conf": c["conf"], "band": c["band"], "max": mx, "max_unsure": c["max_unsure"],
                    "max_spread": c["max_spread"], "agg": agg,
                    "precision": round(n_ok / max(1, n_ans), 3), "precision_estimate": round(est_ok / max(1, n_est), 3),
                    "enabled": enabled}
        report[obj] = {
            "n": n, "agg": agg, "t": c["conf"], "band": c["band"], "k": c["max_unsure"], "max_spread": c["max_spread"],
            "enabled": enabled,
            "fitted_exact": round(n_all / n, 4),
            "fitted_proven": f"{n_ok}/{n_ans} correct, coverage {n_ans / n:.1%}",
            "cv_exact": round(cv["all_ok"] / n, 4),
            "cv_proven_coverage": round(cv["proven"] / n, 4),
            "cv_proven_precision": round(cv["proven_ok"] / max(1, cv["proven"]), 4),
            "cv_estimate_precision": round((cv["all_ok"] - cv["proven_ok"]) / max(1, n - cv["proven"]), 4),
        }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    Path(a.out).with_suffix(".report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
