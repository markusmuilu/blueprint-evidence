"""Trim an eval run's answers.jsonl to one small CSV per source, plus results/README.md's table numbers.

Columns: drawing, question_id, question_type, our_answer, unit, status (proven / estimate / cannot), correct.
No question text and no dataset answers: those belong to the Stoa challenge dataset. question_id is the dataset's
qid (the line number in questions.jsonl, counted from 0), so anyone with the dataset can join the two and re-score
with tools/score_results.py. our_answer is written at full precision so that re-scoring gives the same result.
The drawing is the file name only, under data/<source>/.

  python tools/export_test_csv.py <answers.jsonl> <out_dir>      # e.g. results/
"""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath

COLS = ["drawing", "question_id", "question_type", "our_answer", "unit", "status", "correct"]
MEASURE = {"count": "count", "area": "area", "length": "length", "volume": "volume"}


def answer_cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return repr(v)
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return " ".join(s.split())


def question_type(q, dataset_type=""):
    """A short readable kind from the parsed query: 'door count', 'room area', 'text lookup (wall)'."""
    measure, obj = q.get("measure"), (q.get("obj") or "").replace("_", " ")
    if measure == "text":
        return f"text lookup ({obj})" if obj else "text lookup"
    if measure in MEASURE:
        return f"{obj} {MEASURE[measure]}" if obj else MEASURE[measure]
    return f"not parsed ({dataset_type} question)" if dataset_type else "not parsed"


def status(r):
    if r.get("abstain") is not None or r.get("value") is None:
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
            rows[source].append([p.name, r["qid"], question_type(r.get("query") or {}, r.get("type", "")),
                                 "" if st == "cannot" else answer_cell(r.get("value")), r.get("unit") or "", st,
                                 "yes" if ok else "no"])
            s = stats[source]
            s["n"] += 1
            s["correct"] += ok
            s["proven"] += st == "proven"
            s["proven_ok"] += st == "proven" and ok
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for source, rs in sorted(rows.items()):
        rs.sort(key=lambda row: row[1])
        dst = out / f"{source}.csv"
        with open(dst, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(COLS)
            w.writerows(rs)
        s = stats[source]
        print(f"{source}: {s['n']} rows, right {s['correct'] / s['n']:.1%}, proven {s['proven']} "
              f"right {s['proven_ok'] / s['proven']:.1%}" if s["proven"] else f"{source}: {s['n']} rows",
              f"({dst.stat().st_size / 1e6:.1f} MB)")
    t = {k: sum(s[k] for s in stats.values()) for k in ("n", "correct", "proven", "proven_ok")}
    print(f"total {t['n']} rows, right {t['correct'] / t['n']:.1%}, proven {t['proven']} "
          f"right {t['proven_ok'] / t['proven']:.1%}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
