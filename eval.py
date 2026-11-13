"""Evaluate the solver on a split of the questions.

    python eval.py                       # dev split, all sources, no evidence images
    python eval.py --split test --evidence
    python eval.py --sources swiss_dwellings,ifc_models --limit 200

Prints accuracy per source x question type, coverage (answered vs abstained) and the
estimated model cost per question. Writes out/<split>/answers.jsonl and summary files.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

from bpqa.data import data_root, load_questions, split_of
from bpqa.router import Context, solve

REPO = Path(__file__).resolve().parent


def score(q, a) -> bool | None:
    """True/False for scorable answers, None when abstained."""
    if not a.answered:
        return None
    gold, pred = q.answer, a.value
    if q.type == "count":
        try:
            return int(round(float(pred))) == int(gold) and abs(float(pred) - round(float(pred))) < 1e-6
        except (TypeError, ValueError):
            return False
    if q.type in ("area", "length", "volume"):
        try:
            g, p = float(gold), float(pred)
        except (TypeError, ValueError):
            return False
        return abs(p - g) <= 0.02 * abs(g) if g else abs(p) < 1e-9
    # free-form text: approximate check (all numbers in gold present within 2%, else token overlap)
    gs, ps = str(gold).lower(), str(pred).lower()
    gnums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", gs.replace(",", ""))]
    pnums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", ps.replace(",", ""))]
    if gnums:
        return all(any(abs(p - g) <= 0.02 * max(abs(g), 1e-9) for p in pnums) for g in gnums)
    gt = set(re.findall(r"\w+", gs))
    return len(gt & set(re.findall(r"\w+", ps))) >= 0.6 * max(len(gt), 1)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="dataset root (folder with questions.jsonl); default from STOA_DATA")
    ap.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    ap.add_argument("--sources", default="", help="comma-separated source filter")
    ap.add_argument("--objects", default="", help="comma-separated parsed-object filter, e.g. toilet,sink,door")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--evidence", action="store_true", help="render evidence PNGs into out/evidence/")
    ap.add_argument("--prior", action="store_true",
                    help="answer raster questions without a CV detector with the dev-split majority value (labelled baseline)")
    ap.add_argument("--no-checks", action="store_true", help="turn the checking layer off (for before/after numbers)")
    ap.add_argument("--out", default=str(REPO / "out"))
    args = ap.parse_args(argv)

    root = data_root(args.data)
    qs = load_questions(root)
    if args.prior:
        from bpqa.query import parse
        from bpqa.routes.raster import fit_priors
        fit_priors(qs, parse, split_of)   # fitted on dev answers only
    qs = [q for q in qs if args.split == "all" or split_of(q.path) == args.split]
    if args.sources:
        keep = set(args.sources.split(","))
        qs = [q for q in qs if q.source in keep]
    if args.objects:
        from bpqa.query import parse
        keep_o = set(args.objects.split(","))
        qs = [q for q in qs if parse(q.question).obj in keep_o]
    # group by file so per-drawing caches stay warm
    qs.sort(key=lambda q: (q.path, q.qid))
    if args.limit:
        qs = qs[: args.limit]

    out = Path(args.out)
    ev_dir = out / "evidence"
    tag = args.split + ("-prior" if args.prior else "") + ("-nochecks" if args.no_checks else "")
    if args.data:
        tag += "-" + Path(args.data).name
    if args.objects:
        tag += "-" + args.objects.replace(",", "+")
    run_dir = out / tag
    run_dir.mkdir(parents=True, exist_ok=True)

    stats = defaultdict(lambda: {"n": 0, "answered": 0, "correct": 0, "cost": 0.0})
    routes = defaultdict(int)
    by_obj = defaultdict(lambda: {"n": 0, "answered": 0, "correct": 0})
    t0 = time.time()
    with open(run_dir / "answers.jsonl", "w", encoding="utf-8") as f:
        for i, q in enumerate(qs):
            ctx = Context(root=root, q=q, out_dir=ev_dir, evidence=args.evidence, prior=args.prior,
                          checks=not args.no_checks)
            query, a = solve(ctx)
            ok = score(q, a)
            for key in [(q.source, q.type), (q.source, "ALL"), ("ALL", "ALL")]:
                s = stats[key]
                s["n"] += 1
                s["answered"] += a.answered
                s["correct"] += bool(ok)
                s["cost"] += a.cost_usd
            routes[a.route if a.answered else f"abstain:{a.route}"] += 1
            o = by_obj[(q.source, query.obj or "?")]
            o["n"] += 1
            o["answered"] += a.answered
            o["correct"] += bool(ok)
            rec = {"qid": q.qid, "path": q.path, "question": q.question, "gold": q.answer, "type": q.type,
                   "split": args.split, "correct": ok, "query": query.as_dict(), **a.as_dict()}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if (i + 1) % 500 == 0:
                print(f"  {i + 1}/{len(qs)}  {time.time() - t0:.0f}s", file=sys.stderr)

    rows = []
    for (src, typ), s in sorted(stats.items(), key=lambda kv: (kv[0][0] == "ALL", kv[0])):
        rows.append({"source": src, "type": typ, "n": s["n"], "answered": s["answered"],
                     "coverage": s["answered"] / s["n"], "correct": s["correct"],
                     "accuracy": s["correct"] / s["n"],
                     "precision": s["correct"] / s["answered"] if s["answered"] else 0.0,
                     "cost_per_q": s["cost"] / s["n"]})
    hdr = f"{'source':<16}{'type':<8}{'n':>6}{'answered':>10}{'coverage':>10}{'correct':>9}{'accuracy':>10}{'precision':>11}{'$/q':>8}"
    lines = [f"split={args.split}  questions={len(qs)}  time={time.time() - t0:.0f}s", hdr]
    for r in rows:
        lines.append(f"{r['source']:<16}{r['type']:<8}{r['n']:>6}{r['answered']:>10}{r['coverage']:>10.1%}"
                     f"{r['correct']:>9}{r['accuracy']:>10.1%}{r['precision']:>11.1%}{r['cost_per_q']:>8.4f}")
    lines.append("")
    lines.append(f"{'source':<16}{'object':<16}{'n':>6}{'answered':>10}{'correct':>9}{'accuracy':>10}{'precision':>11}")
    for (src, obj), o in sorted(by_obj.items()):
        lines.append(f"{src:<16}{obj:<16}{o['n']:>6}{o['answered']:>10}{o['correct']:>9}{o['correct'] / o['n']:>10.1%}"
                     f"{(o['correct'] / o['answered'] if o['answered'] else 0):>11.1%}")
    lines.append("routes: " + ", ".join(f"{k}={v}" for k, v in sorted(routes.items(), key=lambda kv: -kv[1])))
    lines.append("accuracy = correct / all questions (abstain counts as wrong); precision = correct / answered")
    text = "\n".join(lines)
    print(text)
    (run_dir / "summary.txt").write_text(text + "\n", encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
