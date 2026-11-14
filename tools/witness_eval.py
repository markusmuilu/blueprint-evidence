"""How often do the format witnesses agree, and does agreement predict a correct answer?

    python tools/witness_eval.py --split dev --per-drawing 12

For every question on a drawing that exists in more than one format (Swiss Dwellings: DXF, PDF, PNG;
IFC plans: model and PDF), each format answers on its own (bpqa/witnesses.py), and the router's normal
answer is computed too. Both are scored against the dataset answer with eval.py's rule. Writes
out/witness/records.jsonl and out/witness/witness_summary.json (read by the app's Results view).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ["NEMO_FALLBACK"] = "0"

from bpqa import witnesses  # noqa: E402
from bpqa.answer import Answer  # noqa: E402
from bpqa.data import data_root, load_questions, split_of  # noqa: E402
from bpqa.router import Context, solve  # noqa: E402
from eval import score  # noqa: E402

LABELS = {"all": "All formats agree", "majority": "Majority agrees, one differs", "split": "Formats disagree, no majority",
          "single": "Only one format answers", "none": "No format answers"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--per-drawing", type=int, default=12)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", default=str(REPO / "out" / "witness"))
    ap.add_argument("--summarize-only", action="store_true", help="rebuild summary.json from records.jsonl")
    args = ap.parse_args()
    root = data_root(args.data)
    if args.summarize_only:
        out = Path(args.out)
        recs = [json.loads(x) for x in (out / "records.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        old = json.loads((out / "witness_summary.json").read_text(encoding="utf-8")) if (out / "witness_summary.json").exists() else {}
        return write_summary(recs, out, root, args, old.get("elapsed_s", 0))
    qs = [q for q in load_questions(root) if (args.split == "all" or split_of(q.path) == args.split) and q.type != "text"]
    by_path = defaultdict(list)
    for q in qs:
        by_path[q.path].append(q)
    rng = random.Random(args.seed)
    plan = []
    for p in sorted(by_path):
        if len(witnesses.witnesses_for(root, p)) < 2:
            continue
        group = by_path[p][:]
        rng.shuffle(group)
        plan += group[: args.per_drawing]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    recs = []
    t_all = time.perf_counter()
    with open(out / "records.jsonl", "w", encoding="utf-8") as f:
        for k, q in enumerate(plan):
            ctx = Context(root=root, q=q, out_dir=out, evidence=False, nemotron=False)
            try:
                _, ra = solve(ctx)
            except Exception as ex:
                ra = Answer(route="error", abstain=str(ex))
            w = witnesses.run(root, q.path, q.question)
            maj = w["majority_value"]
            maj_ok = score(q, Answer(value=maj, unit=w["unit"])) if maj is not None else None
            rec = {"qid": q.qid, "path": q.path, "source": q.source, "type": q.type, "gold": q.answer,
                   "router": {"route": ra.route, "value": ra.value if ra.answered else None, "correct": score(q, ra)},
                   "level": w["level"], "n_formats": w["n_formats"], "n_answered": w["n_answered"], "n_agree": w["n_agree"],
                   "majority": maj, "majority_correct": maj_ok,
                   "witnesses": [{k2: r[k2] for k2 in ("format", "route", "value", "tier", "agrees", "wall_s")} |
                                 {"correct": score(q, Answer(value=r["value"], unit=r["unit"])) if r["value"] is not None else None}
                                 for r in w["witnesses"]]}
            recs.append(rec)
            f.write(json.dumps(rec, default=str) + "\n")
            vals = " ".join(f"{r['format']}={r['value']}" for r in w["witnesses"])
            print(f"[{k + 1}/{len(plan)}] {w['level']:<8} gold={q.answer} router={rec['router']['value']} {vals}  {q.path.rsplit('/', 1)[-1]}", flush=True)
    write_summary(recs, out, root, args, time.perf_counter() - t_all)


def rate(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs), len(xs)) if xs else (None, 0)


def write_summary(recs, out, root, args, elapsed):
    n = len(recs)
    rows = []
    for level in ("all", "majority", "split", "single", "none"):
        rs = [r for r in recs if r["level"] == level]
        if not rs:
            continue
        rc, rn = rate([bool(r["router"]["correct"]) for r in rs])     # abstention counts as wrong
        mc, mn = rate([r["majority_correct"] for r in rs if r["majority"] is not None])
        rows.append({"level": level, "label": LABELS[level], "n": len(rs), "share": len(rs) / n,
                     "router_correct": rc, "router_correct_n": sum(bool(r["router"]["correct"]) for r in rs),
                     "majority_correct": mc, "majority_correct_n": sum(bool(r["majority_correct"]) for r in rs if r["majority"] is not None)})
    rc = sum(bool(r["router"]["correct"]) for r in recs)
    rows.append({"level": "total", "label": "All questions", "n": n, "share": 1.0, "router_correct": rc / n if n else None,
                 "router_correct_n": rc, "majority_correct": None, "majority_correct_n": None})
    # does the router answer agreeing with at least one other format predict correctness?
    corrob = [r for r in recs if r["router"]["value"] is not None and r["n_answered"] >= 2]
    def agrees_with_other(r):
        same = [w for w in r["witnesses"] if w["value"] is not None and witnesses.same(w["value"], r["router"]["value"], "count" if r["type"] == "count" else "m2")]
        return len(same) >= 2
    a_yes = [r for r in corrob if agrees_with_other(r)]
    a_no = [r for r in corrob if not agrees_with_other(r)]
    by_source = defaultdict(Counter)
    for r in recs:
        by_source[r["source"]][r["level"]] += 1
    per_format = defaultdict(lambda: [0, 0, 0])
    for r in recs:
        for w in r["witnesses"]:
            pf = per_format[w["format"]]
            pf[0] += 1
            pf[1] += w["value"] is not None
            pf[2] += bool(w["correct"])
    # per witness: is a format's answer right more often when another format gives the same value?
    by_agree = defaultdict(lambda: [0, 0])
    for r in recs:
        for w in r["witnesses"]:
            if w["value"] is None or w["agrees"] is None:
                continue
            g = "alone" if r["n_answered"] == 1 else "in" if w["agrees"] else "out"
            k = g + ":" + w["format"]
            by_agree[k][0] += 1
            by_agree[k][1] += bool(w["correct"])
            k = g
            by_agree[k][0] += 1
            by_agree[k][1] += bool(w["correct"])
    witness_rows = []
    for key, label in (("in", "A format's answer when another format matches it"), ("out", "A format's answer when another format answers differently"),
                       ("alone", "A format's answer when no other format answers")):
        n_, c_ = by_agree.get(key, [0, 0])
        per = {f: by_agree.get(f"{key}:{f}", [0, 0]) for f in ("DXF", "IFC", "PDF", "PNG")}
        witness_rows.append({"key": key, "label": label, "n": n_, "correct": c_ / n_ if n_ else None, "correct_n": c_,
                             "by_format": {f: {"n": v[0], "correct": v[1]} for f, v in per.items() if v[0]}})
    summary = {
        "split": args.split, "witness_rows": witness_rows, "data": str(root), "questions": n, "elapsed_s": round(elapsed, 1), "rows": rows,
        "corroborated": {"n": len(a_yes), "router_correct": rate([bool(r["router"]["correct"]) for r in a_yes])[0]},
        "uncorroborated": {"n": len(a_no), "router_correct": rate([bool(r["router"]["correct"]) for r in a_no])[0]},
        "by_source": {k: dict(v) for k, v in by_source.items()},
        "per_format": {k: {"asked": v[0], "answered": v[1], "correct": v[2]} for k, v in per_format.items()},
        "note": "",
    }
    cy, cn = summary["corroborated"], summary["uncorroborated"]
    pct = lambda x: "-" if x is None else f"{100 * x:.1f}%"
    summary["note"] = (f"{n} {args.split} questions of the {'Stoa preview set' if 'preview' in str(root) else str(root)} on drawings with two or more formats "
                       f"(Swiss Dwellings DXF + PDF + PNG, IFC plans model + PDF), up to {args.per_drawing} per drawing; witnesses read each format alone, "
                       f"no model calls. When the router's answer is matched by at least one other format it is right {pct(cy['router_correct'])} of the time "
                       f"({cy['n']} questions); when no other format matches it, {pct(cn['router_correct'])} ({cn['n']}). "
                       f"Router correct counts an abstention as wrong. Source: out/witness/witness_summary.json (tools/witness_eval.py).")
    (out / "witness_summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("questions", "elapsed_s", "corroborated", "uncorroborated", "per_format")}, indent=1))
    for r in rows:
        print(f"{r['label']:<34} n={r['n']:<5} share={pct(r['share'])}  router={pct(r['router_correct'])}  majority={pct(r['majority_correct'])}")


if __name__ == "__main__":
    main()
