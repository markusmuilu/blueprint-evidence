"""Measure the precision of "proven" scan answers on dev and write the calibration table.

    python eval.py --data ../data/stoa-full/junction-dataset --split dev --sources cubicasa5k --no-calibration
    python tools/calibrate_tiers.py out/dev-nocalib-junction-dataset/answers.jsonl

Reads an answers.jsonl written with calibration off (so "proven" is each route's own tuned tier) and
measures, per scan route and asked object, and per route, object and answered count, how often a proven
answer was right. A cell keeps the proven tier only when its dev precision is at least MIN_PRECISION over at
least MIN_N distinct drawings (an English and a Finnish question on the same image are one drawing, since
they get the same answer). Everything else is written with its measured precision, which the answer then
quotes as the reason it is an estimate (bpqa/calibration.py).

A route may also name the condition it met (Answer.tier_cell, e.g. "clear" for a detector count whose views
all agree with no box near the threshold); those answers are measured again as their own cell
("raster-detector|toilet|clear"), so the proven tier can be kept for that condition alone.

Detector answers on images the detector trained on (data/detector/ds_cubi/train.txt and val.txt) are left
out: they would overstate its precision. Test-split answers are refused.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from bpqa.calibration import MIN_N, MIN_PRECISION, TABLE  # noqa: E402
from bpqa.data import split_of  # noqa: E402

TRAIN_LISTS = [REPO.parent / "data" / "detector" / "ds_cubi" / f for f in ("train.txt", "val.txt")]


def wilson_low(k: int, n: int, z: float = 1.96) -> float:
    if not n:
        return 0.0
    p = k / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - m) / (1 + z * z / n)


def load(path: Path, train: set[str]):
    """Distinct (route, object, drawing) proven answers: (route, obj, value, correct)."""
    seen = {}
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        if split_of(r["path"]) != "dev":
            raise SystemExit(f"{r['path']} is not a dev drawing: calibration reads dev answers only")
        route = r.get("route") or ""
        if r.get("tier") != "proven" or not route.startswith("raster") or r.get("value") is None:
            continue
        obj = (r.get("query") or {}).get("obj") or r.get("obj") or "?"
        measure = (r.get("query") or {}).get("measure") or r.get("measure") or "?"
        if route == "raster-detector" and Path(r["path"]).stem in train:
            continue
        o = obj if measure == "count" else f"{obj}:{measure}"
        seen.setdefault((route, o, r["path"]), (r["value"], bool(r["correct"])))
        if r.get("tier_cell"):           # also measured on its own: the condition the route met
            seen.setdefault((route, f"{o}|{r['tier_cell']}", r["path"]), (r["value"], bool(r["correct"])))
    return seen


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("answers", nargs="+", help="answers.jsonl files from dev runs with calibration off")
    ap.add_argument("--out", default=str(TABLE))
    ap.add_argument("--note", default="", help="how the answers were produced, kept in the table")
    args = ap.parse_args(argv)
    train = {Path(l.strip()).stem for f in TRAIN_LISTS if f.exists() for l in open(f) if l.strip()}
    seen = {}
    for p in args.answers:
        seen.update(load(Path(p), train))
    cells = defaultdict(lambda: [0, 0])
    counts = defaultdict(lambda: [0, 0])
    for (route, obj, _), (value, ok) in seen.items():
        cells[(route, obj)][0] += 1
        cells[(route, obj)][1] += ok
        v = str(round(value)) if isinstance(value, (int, float)) and float(value).is_integer() else None
        if v is not None:
            counts[(route, obj, v)][0] += 1
            counts[(route, obj, v)][1] += ok

    def row(n, ok):
        return {"n": n, "correct": ok, "precision": round(ok / n, 4), "wilson_low": round(wilson_low(ok, n), 4),
                "proven": n >= MIN_N and ok / n >= MIN_PRECISION}

    table = {"_about": "Proven precision of scan routes on dev drawings (tools/calibrate_tiers.py). A cell keeps "
                       f"the proven tier only at >= {MIN_PRECISION:.0%} precision over >= {MIN_N} drawings.",
             "_sources": [Path(p).name for p in args.answers] + ([args.note] if args.note else []),
             "_drawings": len({k[2] for k in seen}),
             "cells": {}}
    for (route, obj), (n, ok) in sorted(cells.items()):
        c = row(n, ok)
        c["counts"] = {v: row(*counts[(route, obj, v)]) for v in sorted({k[2] for k in counts if k[:2] == (route, obj)},
                                                                        key=lambda s: int(s))}
        table["cells"][f"{route}|{obj}"] = c
    Path(args.out).write_text(json.dumps(table, indent=1) + "\n", encoding="utf-8")
    for k, c in table["cells"].items():
        keep = [v for v, cc in c["counts"].items() if cc["proven"]]
        print(f"{k:<40} n={c['n']:>5} prec={c['precision']:6.1%} lo={c['wilson_low']:6.1%} "
              f"{'PROVEN' if c['proven'] else 'estimate'}" + (f"  proven counts: {keep}" if keep and not c["proven"] else ""))


if __name__ == "__main__":
    main()
