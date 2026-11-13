"""Tune the raster-detector count thresholds on Stoa train-split (dev) images the detector did not train on.

    python tools/tune_detector.py --stoa ../data/stoa-full/junction-dataset --images ../data/detector/ds/tune_images.txt
    python tools/tune_detector.py                     # preview dev images (synthetic-only smoke model)

Per object (toilet, sink, shower_or_bath, door, window): the score threshold t that maximises exact
count accuracy, then an uncertainty band around t (abstain when any box scores within it) chosen to
maximise correct minus wrong answers. Also reports a 2-fold cross-validated estimate (fit on one half
of the images, measured on the other), which is the honest number. Writes models/detector_thresholds.json.
Test-split answers are never read.
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

TS = [round(0.05 * i, 2) for i in range(2, 19)]
BANDS = [0.0, 0.05, 0.1, 0.15, 0.2, 0.25]


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


def evaluate(rows, t, band, mx=99):
    a = c = 0
    for dets, gold in rows:
        hits, unsure = det.count(dets, rows_obj[0], t, band)
        if unsure or len(hits) > mx:
            continue
        a += 1
        c += len(hits) == gold
    return a, c


rows_obj = [None]


def fit(rows):
    best_t = max(TS, key=lambda t: (evaluate(rows, t, 0.0)[1], -abs(t - 0.5)))
    def score(b):
        a, c = evaluate(rows, best_t, b)
        return c - (a - c)
    best_b = max(BANDS, key=lambda b: (score(b), -b))
    return best_t, best_b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stoa", default=str(DEFAULT_DATA), help="dataset root with questions.jsonl")
    ap.add_argument("--images", default="", help="file listing dataset-relative image paths (default: all dev CubiCasa images)")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--out", default=str(det.THRESHOLDS))
    a = ap.parse_args()
    root = Path(a.stoa)
    if a.images:
        paths = {l.strip() for l in Path(a.images).read_text(encoding="utf-8").splitlines() if l.strip()}
    else:
        paths = {f"data/cubicasa5k/{p.name}" for p in (root / "data" / "cubicasa5k").glob("*.png")}
    paths = {p for p in paths if split_of(p) == "dev"}
    gold = gold_counts(root, paths)
    print(f"tuning on {len(gold)} train-split images", file=sys.stderr)
    dets = {}
    for i, p in enumerate(sorted(gold)):
        dets[p] = det.detect(root / p, imgsz=a.imgsz)
        if (i + 1) % 200 == 0:
            print(f"  detected {i + 1}/{len(gold)}", file=sys.stderr)
    out = {"_imgsz": a.imgsz, "_tuned_on": f"{len(gold)} Stoa train-split images", "_model": det.model_tag()}
    report = {}
    for obj in det.OBJECTS:
        rows_obj[0] = obj
        rows = [(dets[p], g[obj]) for p, g in sorted(gold.items()) if obj in g]
        if not rows:
            continue
        t, b = fit(rows)
        mx = max(g for _, g in rows)
        n_ans, n_ok = evaluate(rows, t, b, mx)
        # 2-fold cross-validation by image
        half = [[r for r, p in zip(rows, sorted(p for p in gold if obj in gold[p]))
                 if (int(hashlib.sha1(p.encode()).hexdigest()[:4], 16) % 2) == k] for k in (0, 1)]
        cv_a = cv_c = cv_c0 = 0
        for k in (0, 1):
            tt, bb = fit(half[k])
            aa, cc = evaluate(half[1 - k], tt, bb, mx)
            cv_a, cv_c = cv_a + aa, cv_c + cc
            cv_c0 += evaluate(half[1 - k], tt, 0.0, mx)[1]
        out[obj] = {"conf": t, "band": b, "max": mx, "precision": round(n_ok / max(1, n_ans), 3)}
        report[obj] = {"n": len(rows), "t": t, "band": b, "fitted": f"{n_ok}/{n_ans} answered correct, coverage {n_ans / len(rows):.0%}",
                       "cv_no_band_accuracy": f"{cv_c0}/{len(rows)} = {cv_c0 / len(rows):.1%}",
                       "cv_with_band": f"accuracy {cv_c / len(rows):.1%}, coverage {cv_a / len(rows):.1%}, "
                                       f"precision {cv_c / max(1, cv_a):.1%}"}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
