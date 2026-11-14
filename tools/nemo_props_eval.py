"""Nemotron as a question -> lookup mapper for ifc-props, measured on the questions the rules leave unmapped.

For every IFC-Bench question of the split where `plan()` finds no lookup, Nemotron picks one lookup from a
closed schema built from the model's own class and storey names (`map_with_nemotron`). The lookup then runs
on the file like a rule-mapped one; Nemotron never supplies a value. Scored with eval.py's text scorer.

    python tools/nemo_props_eval.py --data ../data/stoa-full/junction-dataset --split dev [--limit 80]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.data import load_questions, split_of  # noqa: E402
from bpqa.answer import Answer  # noqa: E402
from bpqa.routes import ifc_props as P  # noqa: E402
from bpqa.routes.ifc_route import load  # noqa: E402
from eval import score  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="../data/stoa-full/junction-dataset")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="out/nemo_props_eval.jsonl")
    args = ap.parse_args(argv)
    root = Path(args.data)
    qs = [q for q in load_questions(root) if q.source == "ifc_bench" and split_of(q.path) == args.split]
    qs.sort(key=lambda q: (q.path, q.qid))
    n = mapped = ok = 0
    cost = 0.0
    with open(args.out, "w", encoding="utf-8") as f:
        for q in qs:
            M = load(str(root / q.path))
            if P.plan(M, q.question) is not None:
                continue
            n += 1
            if args.limit and n > args.limit:
                break
            L = P.map_with_nemotron(M, q.question)
            rec = {"qid": q.qid, "question": q.question, "gold": q.answer, "lookup": None, "value": None, "correct": None}
            if L is not None:
                cost += L.cost
                rec["lookup"] = L.describe()
                try:
                    text, *_ = P.run(M, L)
                    rec["value"] = text
                    mapped += 1
                    rec["correct"] = bool(score(q, Answer(value=text)))
                    ok += rec["correct"]
                except LookupError as ex:
                    rec["abstain"] = str(ex)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"{n}: {rec['lookup']} -> {rec['correct']}", file=sys.stderr)
    n = min(n, args.limit) if args.limit else n
    print(f"unmapped by rules: {n}; Nemotron mapped: {mapped}; correct (lenient scorer): {ok}; "
          f"est. cost ${cost:.4f} (assumed list price)")


if __name__ == "__main__":
    main()
