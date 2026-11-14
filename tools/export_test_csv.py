"""Trim an eval run's answers.jsonl to one small CSV per source, plus results/README.md's table numbers.

Columns: drawing, question, gold, our answer, unit, status (proven / estimate / cannot), correct.
No absolute paths: the drawing is the file name under data/<source>/. Long free-text values are cut to 300
characters. A source over about 25 MB would be split by object (none is: CubiCasa is about 4 MB).

  python tools/export_test_csv.py <answers.jsonl> <out_dir>      # e.g. results/, then copy into deploy/demo_results/results/
"""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath

CUT = 300
COLS = ["drawing", "question", "gold", "our answer", "unit", "status", "correct"]


def cell(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.4g}" if abs(v) < 1e6 else f"{v:.0f}"
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= CUT else s[:CUT - 3] + "..."


def status(r):
    if r.get("abstain") or r.get("value") is None:
        return "cannot"
    return "proven" if r.get("tier") == "proven" else "estimate"


def main(src, out_dir):
    rows = defaultdict(list)
    stats = defaultdict(lambda: {"n": 0, "correct": 0, "proven": 0, "proven_ok": 0})
    with open(src, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            p = PurePosixPath(str(r.get("path", "")).replace("\\", "/"))
            parts = p.parts
            source = parts[1] if len(parts) > 2 and parts[0] == "data" else "other"
            st = status(r)
            ok = bool(r.get("correct"))
            rows[source].append([p.name, cell(r.get("question")), cell(r.get("gold")),
                                 "" if st == "cannot" else cell(r.get("value")), cell(r.get("unit")), st,
                                 "yes" if ok else "no"])
            s = stats[source]
            s["n"] += 1
            s["correct"] += ok
            s["proven"] += st == "proven"
            s["proven_ok"] += st == "proven" and ok
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for source, rs in sorted(rows.items()):
        dst = out / f"{source}.csv"
        with open(dst, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(COLS)
            w.writerows(rs)
        s = stats[source]
        print(f"{source}: {s['n']} rows, right {s['correct'] / s['n']:.1%}, proven {s['proven']} "
              f"right {s['proven_ok'] / s['proven']:.1%}" if s["proven"] else f"{source}: {s['n']} rows",
              f"({dst.stat().st_size / 1e6:.1f} MB)")
    print("total", sum(s["n"] for s in stats.values()))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
