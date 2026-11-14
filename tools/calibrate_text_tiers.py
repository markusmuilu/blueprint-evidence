"""Measure how often free-text / property lookups answer what was asked, on dev, and write the table.

    python eval.py --split dev --sources ifc_bench --no-calibration --out out/textcal
    python eval.py --split dev --sources ifc_models --no-calibration --out out/textcal
    python tools/calibrate_text_tiers.py out/textcal/dev-nocalib/answers.jsonl [more answers.jsonl ...]

Reads answers.jsonl files written with calibration off. A lookup answer the lookup itself did not lower carries
its kind in tier_cell ("text:ifc_props|extreme:width:door", set by router._try_text). Per kind it counts answers,
distinct drawings and correct answers. A kind keeps the proven tier only at MIN_PRECISION over MIN_N distinct
drawings, the same standard as the scan routes (bpqa/calibration.py). Test-split answers are refused.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from bpqa.calibration import MIN_N, MIN_PRECISION, TEXT_TABLE  # noqa: E402
from bpqa.data import split_of  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("answers", nargs="+")
    ap.add_argument("--out", default=str(TEXT_TABLE))
    args = ap.parse_args(argv)
    n, ok, drawings = defaultdict(int), defaultdict(int), defaultdict(set)
    seen = set()
    for f in args.answers:
        for line in open(f, encoding="utf-8"):
            r = json.loads(line)
            if split_of(r["path"]) != "dev":
                raise SystemExit(f"{r['path']} is not a dev drawing: calibration reads dev answers only")
            cell = r.get("tier_cell") or ""
            if not cell.startswith("text:") or r.get("value") is None or r["qid"] in seen:
                continue
            seen.add(r["qid"])
            n[cell] += 1
            ok[cell] += bool(r.get("correct"))
            drawings[cell].add(r["path"])
    cells = {}
    for c in sorted(n):
        p = ok[c] / n[c]
        cells[c] = {"n": n[c], "correct": ok[c], "drawings": len(drawings[c]), "precision": round(p, 4),
                    "proven": p >= MIN_PRECISION and len(drawings[c]) >= MIN_N}
    table = {"_about": "How often free-text / property lookups answered what was asked, on dev questions "
                       "(tools/calibrate_text_tiers.py). A kind keeps the proven tier only at >= 98% over >= 100 drawings.",
             "_sources": [f"{Path(f).parent.parent.name}/{Path(f).parent.name}/answers.jsonl" for f in args.answers], "cells": cells}
    Path(args.out).write_text(json.dumps(table, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for c, v in cells.items():
        print(f"{c:<60} n={v['n']:>4} drawings={v['drawings']:>3} precision={v['precision']:.1%} proven={v['proven']}")


if __name__ == "__main__":
    main()
