"""Re-score the published per-question results against the Stoa dataset's own answers.

The CSVs in results/ carry our answer per question but not the question text or the dataset's answer (those belong
to the challenge dataset). With the dataset at hand this joins the two on question_id (the line number in
questions.jsonl, counted from 0), scores our_answer with eval.py's own score() function, and prints accuracy and
Proven precision per source and overall.

  python tools/score_results.py <dataset folder with questions.jsonl> [results CSVs or folder, default results/]

Accuracy = right / all questions (a declined question counts as wrong). Proven precision = right / Proven answers.
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from bpqa.answer import Answer  # noqa: E402
from bpqa.data import load_questions  # noqa: E402
from eval import score  # noqa: E402

COLS = ["drawing", "question_id", "question_type", "our_answer", "unit", "status", "correct"]


def csv_files(args):
    paths = [Path(a) for a in args] or [REPO / "results"]
    out = []
    for p in paths:
        out += sorted(p.glob("*.csv")) if p.is_dir() else [p]
    return out


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    root = Path(argv[0])
    if not (root / "questions.jsonl").exists():
        raise SystemExit(f"questions.jsonl not found under {root}")
    qs = {q.qid: q for q in load_questions(root)}
    stats = defaultdict(lambda: {"n": 0, "right": 0, "proven": 0, "proven_right": 0, "differs": 0})
    problems = 0
    for path in csv_files(argv[1:]):
        with open(path, encoding="utf-8", newline="") as f:
            rd = csv.DictReader(f)
            if rd.fieldnames != COLS:
                raise SystemExit(f"{path}: unexpected columns {rd.fieldnames}")
            for row in rd:
                q = qs.get(int(row["question_id"]))
                if q is None or q.path.rsplit("/", 1)[-1] != row["drawing"]:
                    problems += 1      # a different dataset version: the qid does not point at this drawing
                    continue
                answered = row["status"] != "cannot"
                a = Answer(value=row["our_answer"] if answered else None, abstain=None if answered else "cannot")
                ok = bool(score(q, a))
                for key in (q.source, "ALL"):
                    s = stats[key]
                    s["n"] += 1
                    s["right"] += ok
                    s["proven"] += row["status"] == "proven"
                    s["proven_right"] += row["status"] == "proven" and ok
                    s["differs"] += ok != (row["correct"] == "yes")
    print(f"{'source':<18}{'questions':>10}{'right':>8}{'accuracy':>10}{'proven':>8}{'proven right':>14}{'precision':>11}")
    for key in sorted(stats, key=lambda k: (k == "ALL", k)):
        s = stats[key]
        prec = f"{s['proven_right'] / s['proven']:.1%}" if s["proven"] else "-"
        print(f"{key:<18}{s['n']:>10}{s['right']:>8}{s['right'] / s['n']:>10.1%}{s['proven']:>8}{s['proven_right']:>14}"
              f"{prec:>11}")
    total = stats["ALL"]
    print(f"rows whose re-score differs from the CSV's correct column: {total['differs']}")
    if problems:
        print(f"rows not matched to the dataset (qid missing or a different drawing): {problems}")
    return 1 if problems or total["differs"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
