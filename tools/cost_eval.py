"""Measure wall time and CPU seconds per question by route, and cost per 1,000 questions at ASSUMED prices.

    python tools/cost_eval.py --split dev --drawings 10 --per 2

Samples up to --drawings drawings per (source, file type, question type) from the split and asks --per of
their questions in a row with evidence rendering on, as the app does. The first question on a drawing is
"cold" (OCR and detector caches empty for that drawing; models may already be loaded), later ones "warm".
Run it before other runs fill out/det and out/ocr, or pass --fresh-cache to move those caches aside.
Nemotron is off: its cost is measured separately in out/baseline_nemotron.json.

Writes out/cost/cost_by_route.json and out/cost/questions.jsonl.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ["NEMO_FALLBACK"] = "0"

from app import costs  # noqa: E402
from bpqa.data import data_root, load_questions, split_of  # noqa: E402
from bpqa.router import Context, solve  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--drawings", type=int, default=10)
    ap.add_argument("--per", type=int, default=2)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--fresh-cache", action="store_true")
    ap.add_argument("--out", default=str(REPO / "out" / "cost"))
    ap.add_argument("--summarize-only", action="store_true", help="rebuild cost_by_route.json from questions.jsonl")
    args = ap.parse_args()
    if args.summarize_only:
        out = Path(args.out)
        rows = [json.loads(x) for x in (out / "questions.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        old = json.loads((out / "cost_by_route.json").read_text(encoding="utf-8"))
        return summarize(rows, args, old["data"], old["elapsed_s"], out)

    root = data_root(args.data)
    qs = [q for q in load_questions(root) if args.split == "all" or split_of(q.path) == args.split]
    if args.fresh_cache:
        for sub in ("det", "ocr"):
            d = REPO / "out" / sub
            if d.exists():
                shutil.move(str(d), str(d) + f".aside-{int(time.time())}")
    by_stratum = defaultdict(lambda: defaultdict(list))
    for q in qs:
        if q.type == "text":
            continue
        by_stratum[(q.source, q.ext, q.type)][q.path].append(q)
    rng = random.Random(args.seed)
    plan = []
    for key in sorted(by_stratum):
        paths = sorted(by_stratum[key])
        rng.shuffle(paths)
        for p in paths[: args.drawings]:
            group = by_stratum[key][p][:]
            rng.shuffle(group)
            plan += [(key, q, i == 0) for i, q in enumerate(group[: args.per])]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    render = out / "render"
    rows = []
    t_all = time.perf_counter()
    with open(out / "questions.jsonl", "w", encoding="utf-8") as f:
        for k, (key, q, cold) in enumerate(plan):
            ctx = Context(root=root, q=q, out_dir=render, evidence=True, nemotron=False)
            t0, c0 = time.perf_counter(), time.process_time()
            try:
                _, ans = solve(ctx)
                route, answered = ans.route, ans.answered
            except Exception as ex:
                route, answered = f"error:{type(ex).__name__}", False
            wall, cpu = time.perf_counter() - t0, time.process_time() - c0
            r = {"qid": q.qid, "path": q.path, "source": q.source, "ext": q.ext, "type": q.type, "route": route,
                 "answered": answered, "cold": cold, "wall_s": round(wall, 3), "cpu_s": round(cpu, 3)}
            rows.append(r)
            f.write(json.dumps(r) + "\n")
            print(f"[{k + 1}/{len(plan)}] {route:<20} {wall:6.2f}s wall {cpu:6.2f}s cpu  {'cold' if cold else 'warm'}  {q.path}", flush=True)
    shutil.rmtree(render, ignore_errors=True)
    summarize(rows, args, str(root), time.perf_counter() - t_all, out)


def summarize(rows, args, root, elapsed, out):
    def agg(rs):
        walls = [r["wall_s"] for r in rs]
        cpus = [r["cpu_s"] for r in rs]
        cold = [r["wall_s"] for r in rs if r["cold"]]
        warm = [r["wall_s"] for r in rs if not r["cold"]]
        cpu_mean = statistics.mean(cpus)
        rec = costs.compute_record(statistics.mean(walls), cpu_mean, None, gpu_s=0.0)
        # the same mean time booked on a GPU, for the detector route if it ran there
        gpu_alt = costs.compute_record(statistics.mean(walls), 0.0, None, gpu_s=statistics.mean(walls))
        return {"n": len(rs), "answered": sum(r["answered"] for r in rs),
                "wall_median_s": round(statistics.median(walls), 3), "wall_mean_s": round(statistics.mean(walls), 3),
                "wall_p90_s": round(sorted(walls)[max(0, math.ceil(0.9 * len(walls)) - 1)], 3),
                "cold_median_s": round(statistics.median(cold), 3) if cold else None,
                "warm_median_s": round(statistics.median(warm), 3) if warm else None,
                "cpu_mean_s": round(cpu_mean, 3), "usd_per_1000_cpu": rec["usd_per_1000"],
                "usd_per_1000_if_gpu": gpu_alt["usd_per_1000"]}

    by_route = defaultdict(list)
    for r in rows:
        by_route[r["route"]].append(r)
    table = [{"route": k, **agg(v)} for k, v in sorted(by_route.items(), key=lambda kv: -len(kv[1]))]
    summary = {"split": args.split, "data": root, "questions": len(rows), "elapsed_s": round(elapsed, 1),
               "machine": {"cpu_count": os.cpu_count(), "device": "CPU only (CUDA_VISIBLE_DEVICES empty)"},
               "assumed": costs.assumptions(), "routes": table, "all": agg(rows) if rows else None,
               "method": "a question that every route abstained on is booked under the first route tried; wall and CPU seconds (process time, all threads) per question, evidence rendering on, Nemotron off; "
                         "cost = CPU seconds x assumed vCPU price; usd_per_1000_if_gpu books the mean wall time on the assumed GPU price instead."}
    (out / "cost_by_route.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("questions", "elapsed_s")}))
    for t in table:
        print(f"{t['route']:<22} n={t['n']:<4} median {t['wall_median_s']:.2f}s  cpu {t['cpu_mean_s']:.2f}s  ${t['usd_per_1000_cpu']:.4f}/1000")


if __name__ == "__main__":
    main()
