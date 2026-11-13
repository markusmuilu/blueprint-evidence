"""Effect of the Nemotron fallback route on the preview DEV questions that currently abstain.

    python tools/nemo_fallback_eval.py --phase abstain     # router without Nemotron over dev, list abstentions
    python tools/nemo_fallback_eval.py --phase sample --n 240

`abstain` runs every dev question through the router with the Nemotron fallbacks off and stores the ones
with no answer in out/nemo_fallback/abstained_dev.json. `sample` draws N of the abstained questions that the
route may answer at all (numeric, not about a whole model), stratified by source and parsed object
with a fixed seed, runs them through the full router (fallback on) and scores them with eval.score.
Writes out/nemo_fallback/result.json. Dev only: test was not used.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bpqa.data import data_root, load_questions, split_of
from bpqa.query import parse
from bpqa.router import Context, solve
from eval import score

OUT = REPO / "out" / "nemo_fallback"
SEED = 11


def abstain_phase(root):
    OUT.mkdir(parents=True, exist_ok=True)
    qs = [q for q in load_questions(root) if split_of(q.path) == "dev"]
    qs.sort(key=lambda q: (q.path, q.qid))
    res, t0 = [], time.time()
    for i, q in enumerate(qs):
        ctx = Context(root=root, q=q, out_dir=OUT / "ev0", evidence=False, nemotron=False)
        query, a = solve(ctx)
        res.append({"qid": q.qid, "answered": a.answered, "ok": bool(score(q, a)) if a.answered else None,
                    "route": a.route, "obj": query.obj, "measure": query.measure, "scope": query.scope,
                    "compound": bool(query.extra.get("compound") or query.extra.get("unresolved_scope"))})
        if (i + 1) % 1000 == 0:
            print(i + 1, len(qs), f"{time.time() - t0:.0f}s", file=sys.stderr)
    (OUT / "abstained_dev.json").write_text(json.dumps(res), encoding="utf-8")
    ab = [r for r in res if not r["answered"]]
    print(f"dev {len(res)} questions, router without Nemotron answers {len(res) - len(ab)}, abstains {len(ab)}")


def eligible(r) -> bool:
    return (not r["answered"] and r["measure"] in ("count", "area", "length", "volume")
            and r["scope"] != "model" and not r["compound"])


def sample_phase(root, n, workers):
    res = json.loads((OUT / "abstained_dev.json").read_text(encoding="utf-8"))
    by_q = {q.qid: q for q in load_questions(root)}
    ab = [r for r in res if not r["answered"]]
    el = [r for r in ab if eligible(r) and by_q[r["qid"]].ext in ("png", "jpg", "jpeg", "pdf")]
    strata = defaultdict(list)
    for r in sorted(el, key=lambda r: r["qid"]):
        strata[(by_q[r["qid"]].source, r["obj"])].append(r)
    rng = random.Random(SEED)
    for v in strata.values():
        rng.shuffle(v)
    pick, keys = [], sorted(strata)
    while len(pick) < n and any(strata.values()):
        for k in keys:
            if strata[k] and len(pick) < n:
                pick.append(strata[k].pop())
    print(f"abstained {len(ab)}, eligible {len(el)}, sampled {len(pick)}", file=sys.stderr)

    def one(r):
        q = by_q[r["qid"]]
        ctx = Context(root=root, q=q, out_dir=OUT / "evidence", evidence=True, nemotron=True)
        query, a = solve(ctx)
        return {"qid": q.qid, "source": q.source, "type": q.type, "obj": query.obj, "question": q.question,
                "gold": q.answer, "answered": a.answered, "route": a.route, "pred": a.value if a.answered else None,
                "ok": bool(score(q, a)) if a.answered else None, "tier": a.tier, "abstain": a.abstain,
                "cost_usd": a.cost_usd, "evidence": a.evidence}
    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(one, pick))
    ans = [r for r in rows if r["answered"]]
    summ = {"dev_questions": len(res), "abstained_without_nemotron": len(ab), "eligible_for_route": len(el),
            "sampled": len(rows), "answered": len(ans), "correct": sum(r["ok"] for r in ans),
            "invalid_or_abstained": len(rows) - len(ans)}
    per = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        k = f"{r['source']}/{r['obj']}"
        per[k][0] += 1
        per[k][1] += r["answered"]
        per[k][2] += bool(r["ok"])
    summ["per_stratum"] = {k: {"n": v[0], "answered": v[1], "correct": v[2]} for k, v in sorted(per.items())}
    reasons = defaultdict(int)
    for r in rows:
        if not r["answered"]:
            reasons[(r["abstain"] or "")[-150:].split("invalid model output:")[-1].strip()[:60]] += 1
    summ["abstain_reasons"] = dict(sorted(reasons.items(), key=lambda kv: -kv[1])[:10])
    (OUT / "result.json").write_text(json.dumps({"summary": summ, "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summ, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True)
    ap.add_argument("--n", type=int, default=240)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--data")
    args = ap.parse_args()
    root = data_root(args.data)
    (abstain_phase if args.phase == "abstain" else None) and abstain_phase(root)
    if args.phase == "sample":
        sample_phase(root, args.n, args.workers)


if __name__ == "__main__":
    main()
