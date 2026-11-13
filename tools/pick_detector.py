"""Compare tuned detector policies on the same tune images and keep the better one per object.

    python tools/pick_detector.py models/th_cubi2.json models/th_tiles.json --out models/detector_thresholds.json

Each input is a thresholds file written by tools/tune_detector.py (with its .report.json next to it),
all tuned on the same images. Per object the policy with the higher 2-fold cross-validated exact-count
accuracy wins (every image answered, in either tier); a tie goes to the higher cross-validated precision
of the proven tier. Prints a markdown table of the comparison. --dry-run only prints it.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

OBJECTS = ["toilet", "sink", "shower_or_bath", "door", "window"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    runs = []
    for f in a.files:
        th = json.loads(Path(f).read_text(encoding="utf-8"))
        rep = json.loads(Path(f).with_suffix(".report.json").read_text(encoding="utf-8"))
        runs.append((Path(f).stem, th, rep))
    tuned_on = {r[1].get("_tuned_on") for r in runs}
    if len(tuned_on) > 1:
        raise SystemExit(f"not tuned on the same images: {tuned_on}")
    hdr = "| object | " + " | ".join(f"{n}: exact (cv) | {n}: proven cov / prec | {n}: estimate prec" for n, _, _ in runs) \
          + " | kept |"
    lines = [hdr, "|" + "---|" * (2 + 3 * len(runs))]
    merged = {"_tuned_on": runs[0][1].get("_tuned_on"), "_picked_from": [n for n, _, _ in runs]}
    for obj in OBJECTS:
        cand = [(r[obj]["cv_exact"], r[obj]["cv_proven_precision"], i) for i, (_, _, r) in enumerate(runs) if obj in r]
        if not cand:
            continue
        best = max(cand)[2]
        merged[obj] = runs[best][1][obj]
        cells = []
        for n, _, r in runs:
            x = r.get(obj)
            cells.append("-" if not x else f"{x['cv_exact']:.1%}")
            cells.append("-" if not x else f"{x['cv_proven_coverage']:.1%} / {x['cv_proven_precision']:.1%}")
            cells.append("-" if not x else f"{x['cv_estimate_precision']:.1%}")
        lines.append(f"| {obj} | " + " | ".join(cells) + f" | {runs[best][0]} |")
    print("\n".join(lines))
    if a.out and not a.dry_run:
        Path(a.out).write_text(json.dumps(merged, indent=1), encoding="utf-8")
        print("wrote", a.out)


if __name__ == "__main__":
    main()
