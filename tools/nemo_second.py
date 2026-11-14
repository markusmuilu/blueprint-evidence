"""Nemotron as a SECOND OPINION on CubiCasa scan counts where our answer is an estimate.

    python tools/nemo_second.py ours   --split dev  --drawings 160 --shard 0/4   # our answers (router, no Nemotron)
    python tools/nemo_second.py pick   --split dev  --per-object 43               # seeded sample of estimate answers
    python tools/nemo_second.py nemo   --split dev  --variant A|B                 # ask Nemotron (cached)
    python tools/nemo_second.py report --split dev                                # per-object table and policies

Splits: dev = tuning data (policies are chosen here); test = run once at the end.
Variant A: image and the dataset question, reply {"count": n}.
Variant B: our detected boxes drawn on the image as numbered red boxes; Nemotron verifies or corrects the
count and returns {"count": n, "boxes": [[x0,y0,x1,y1], ...]} (boxes in the image it was shown).
All answers cached under out/nemo_cache by bpqa/nemotron.py. Files are written under out/second/<split>/.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

OBJECTS = ["door", "window", "kitchen", "sink", "shower_or_bath", "sauna", "bedroom"]
SEED = 20261114
ROOT = Path(os.environ.get("STOA_DATA") or REPO.parent / "data" / "stoa-full" / "junction-dataset")


def out_dir(split):
    d = REPO / "out" / "second" / split
    d.mkdir(parents=True, exist_ok=True)
    return d


def english(q):
    return q.question.startswith("How many")


def candidate_questions(split, n_drawings, offset=0):
    """Seeded drawings of the split; for each, the English count question of every object of interest."""
    from bpqa.data import load_questions, split_of
    from bpqa.query import parse
    qs = [q for q in load_questions(ROOT) if q.source == "cubicasa5k" and split_of(q.path) == split and english(q)]
    by = defaultdict(dict)
    for q in sorted(qs, key=lambda q: q.qid):
        p = parse(q.question)
        if p.measure == "count" and p.obj in OBJECTS and p.obj not in by[q.path]:
            by[q.path][p.obj] = q
    paths = sorted(by)
    random.Random(SEED).shuffle(paths)
    out = []
    for p in paths[offset:offset + n_drawings]:
        out.extend(by[p][o] for o in OBJECTS if o in by[p])
    return out


# ------------------------------------------------------------------ phase 1: our answers
def phase_ours(a):
    os.environ["NEMO_FALLBACK"] = "0"
    from bpqa.router import Context, solve
    qs = candidate_questions(a.split, a.drawings, a.offset)
    k, n = (int(x) for x in a.shard.split("/"))
    paths = sorted({q.path for q in qs})
    mine = {p for i, p in enumerate(paths) if i % n == k}
    qs = [q for q in qs if q.path in mine]
    f = out_dir(a.split) / (f"ours.{k}of{n}.jsonl" if not a.offset else f"ours.o{a.offset}.{k}of{n}.jsonl")
    done = set()
    if f.exists():
        done = {json.loads(l)["qid"] for l in f.read_text(encoding="utf-8").splitlines() if l.strip()}
    t0 = time.time()
    with open(f, "a", encoding="utf-8") as w:
        for i, q in enumerate(qs):
            if q.qid in done:
                continue
            ctx = Context(root=ROOT, q=q, out_dir=REPO / "out" / "second" / "ev", evidence=False, nemotron=False)
            t1 = time.time()
            query, ans = solve(ctx)
            d = ans.as_dict()
            rec = {"qid": q.qid, "path": q.path, "question": q.question, "obj": query.obj, "gold": q.answer,
                   "value": ans.value, "tier": ans.tier, "route": ans.route, "abstain": ans.abstain,
                   "confidence": ans.confidence, "tier_reason": ans.tier_reason,
                   "boxes": [it.get("xyxy") for it in d["items"] if it.get("xyxy")],
                   "uncertain": [u.get("xyxy") for u in d["uncertain"] if u.get("xyxy")],
                   "ours_s": round(time.time() - t1, 1)}
            w.write(json.dumps(rec) + "\n")
            w.flush()
            if i % 20 == 0:
                print(f"shard {k}: {i}/{len(qs)} {time.time() - t0:.0f}s", file=sys.stderr, flush=True)


def load_ours(split):
    rows = []
    for f in sorted(out_dir(split).glob("ours.*.jsonl")):
        rows += [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows


# ------------------------------------------------------------------ phase 2: the sample
def stable_key(qid):
    import hashlib
    return hashlib.sha1(f"{SEED}-{qid}".encode()).hexdigest()


def cap_sample(rows, per_object, per_drawing=3):
    """Deterministic subsample: per object, hash order, at most `per_drawing` per drawing, `per_object` in all.
    The result does not change when more estimate answers are added later, except by filling up."""
    out = []
    for o in OBJECTS:
        seen = Counter()
        n = 0
        for r in sorted((r for r in rows if r["obj"] == o), key=lambda r: stable_key(r["qid"])):
            if seen[r["path"]] < per_drawing and n < per_object:
                seen[r["path"]] += 1
                n += 1
                out.append(r)
    return out


def phase_pick(a):
    """sample.json: every estimate answer (hash order, so Nemotron is asked in a stable order)."""
    rows = [r for r in load_ours(a.split) if r["tier"] == "estimate" and r["value"] is not None]
    rows.sort(key=lambda r: stable_key(r["qid"]))
    (out_dir(a.split) / "sample.json").write_text(json.dumps(rows), encoding="utf-8")
    cap = cap_sample(rows, a.per_object)
    for o in OBJECTS:
        print(f"{o:<16}estimates {sum(r['obj'] == o for r in rows):>4}  in the capped sample {sum(r['obj'] == o for r in cap):>3}")
    print("total estimates", len(rows), " capped sample", len(cap))


# ------------------------------------------------------------------ phase 3: Nemotron
# prompts, parsing and the request live in bpqa/second_opinion.py (shared with the opt-in product step)


def phase_nemo(a):
    from PIL import Image
    from bpqa import second_opinion as S
    sample = json.loads((out_dir(a.split) / "sample.json").read_text(encoding="utf-8"))
    if not a.all:
        sample = cap_sample(sample, a.per_object)
    if a.limit:
        sample = sample[: a.limit]
    f = out_dir(a.split) / f"nemo_{a.variant}.jsonl"
    done = set()
    if f.exists():
        done = {json.loads(l)["qid"] for l in f.read_text(encoding="utf-8").splitlines() if l.strip()}
    todo = [r for r in sample if r["qid"] not in done]
    imgs = {}
    for r in todo:
        if r["path"] not in imgs:
            imgs[r["path"]] = Image.open(ROOT / r["path"]).convert("RGB")

    def one(r):
        res = S.ask(a.variant, imgs[r["path"]], r["question"], r["boxes"])
        return {"qid": r["qid"], "variant": a.variant, "count": res["count"], "boxes": res["boxes"],
                "raw": (res.get("content") or "")[:300], "error": res.get("error"), "cached": res.get("cached"),
                "latency_s": res.get("latency_s"), "prompt_tokens": res.get("prompt_tokens"),
                "completion_tokens": res.get("completion_tokens"), "finish": res.get("finish_reason"),
                "retried": res["retried"], "sent": res["sent"]}

    t0 = time.time()
    n = 0
    with ThreadPoolExecutor(a.workers) as ex, open(f, "a", encoding="utf-8") as w:
        for rec in ex.map(one, todo):
            if rec["error"] is None:          # a failed request (429 after retries) is not a result: asked again next run
                w.write(json.dumps(rec) + "\n")
                w.flush()
            n += 1
            if n % 10 == 0:
                print(f"{a.variant}: {n}/{len(todo)} {time.time() - t0:.0f}s", file=sys.stderr, flush=True)


# ------------------------------------------------------------------ phase 4: the report
def load_nemo(split, v):
    f = out_dir(split) / f"nemo_{v}.jsonl"
    if not f.exists():
        return {}
    return {r["qid"]: r for r in (json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip())}


def policies():
    """name -> f(ours, A, B) -> prediction or None (abstain). A, B may be None (no usable reply)."""
    P = {}
    P["ours"] = lambda o, a, b: o
    P["nemoA"] = lambda o, a, b: a
    P["nemoB"] = lambda o, a, b: b
    P["A else ours"] = lambda o, a, b: a if a is not None else o
    P["B else ours"] = lambda o, a, b: b if b is not None else o
    P["agree(ours,A) else ours"] = lambda o, a, b: o
    P["agree(ours,A) else abstain"] = lambda o, a, b: o if a == o else None
    P["agree(ours,B) else abstain"] = lambda o, a, b: o if b == o else None
    P["A==B overrides ours"] = lambda o, a, b: a if (a is not None and a == b) else o
    P["A==B else abstain"] = lambda o, a, b: a if (a is not None and a == b) else None
    P["ours unless A,B,ours all differ->B"] = lambda o, a, b: o if (o in (a, b)) else (b if b is not None else o)
    P["majority(ours,A,B) else ours"] = lambda o, a, b: next((v for v, c in Counter([o, a, b]).items() if v is not None and c >= 2), o)
    return P


def phase_report(a):
    allrows = json.loads((out_dir(a.split) / "sample.json").read_text(encoding="utf-8"))
    ours = {r["qid"]: r for r in (allrows if a.all else cap_sample(allrows, a.per_object))}
    A, B = load_nemo(a.split, "A"), load_nemo(a.split, "B")
    rows = []
    for q, r in ours.items():
        rows.append({"obj": r["obj"], "gold": int(r["gold"]), "o": r["value"], "a": (A.get(q) or {}).get("count"),
                     "b": (B.get(q) or {}).get("count"), "has_a": q in A, "has_b": q in B, "qid": q})
    if not B:                                   # test split: only variant A was asked (the policy chosen on dev)
        for r in rows:
            r["has_b"] = True
    usable = [r for r in rows if r["has_a"] and r["has_b"]]
    print(f"split={a.split}  sample={len(rows)}  with A={sum(r['has_a'] for r in rows)}  with B={sum(r['has_b'] for r in rows)}")
    P = policies()
    hdr = f"{'object':<16}{'n':>4}" + "".join(f"{k:>10}" for k in ("ours", "nemoA", "nemoB", "A=B"))
    print("\nAccuracy (exact count; a missing Nemotron reply counts as wrong)")
    print(hdr + f"{'ours=A':>9}{'ours=B':>9}{'agreeA acc':>11}{'agreeB acc':>11}")
    acc = lambda rs, key: (sum(r[key] == r["gold"] for r in rs) / len(rs)) if rs else float("nan")
    for o in OBJECTS + ["ALL"]:
        rs = [r for r in usable if o == "ALL" or r["obj"] == o]
        if not rs:
            continue
        ab = [r for r in rs if r["a"] is not None and r["a"] == r["b"]]
        oa = [r for r in rs if r["o"] == r["a"]]
        ob = [r for r in rs if r["o"] == r["b"]]
        print(f"{o:<16}{len(rs):>4}{acc(rs, 'o'):>10.1%}{acc(rs, 'a'):>10.1%}{acc(rs, 'b'):>10.1%}"
              f"{(sum(r['a'] == r['gold'] for r in ab) / len(ab) if ab else float('nan')):>10.1%}"
              f"{len(oa) / len(rs):>9.1%}{len(ob) / len(rs):>9.1%}"
              f"{(sum(r['o'] == r['gold'] for r in oa) / len(oa) if oa else float('nan')):>11.1%}"
              f"{(sum(r['o'] == r['gold'] for r in ob) / len(ob) if ob else float('nan')):>11.1%}")
    print("\nPolicy accuracy per object (abstain = wrong); in brackets: answered share, precision when answered")
    names = list(P)
    objs = [o for o in OBJECTS if any(r["obj"] == o for r in usable)] + ["ALL"]
    print(f"{'policy':<38}" + "".join(f"{o[:12]:>20}" for o in objs))
    for nm in names:
        line = f"{nm:<38}"
        for o in objs:
            rs = [r for r in usable if o == "ALL" or r["obj"] == o]
            preds = [P[nm](r["o"], r["a"], r["b"]) for r in rs]
            ok = sum(p == r["gold"] for p, r in zip(preds, rs))
            ans = sum(p is not None for p in preds)
            cell = f"{ok / len(rs):.0%} ({ans / len(rs):.0%},{(ok / ans if ans else 0):.0%})"
            line += f"{cell:>20}"
        print(line)
    # the policy as shipped (bpqa/second_opinion.py POLICY): listed objects take Nemotron's A count when it gave one
    from bpqa import second_opinion as SO
    print("\nChosen policy (POLICY in bpqa/second_opinion.py): objects " + ", ".join(SO.POLICY) + " take A, all others keep ours")
    tot = {"ours": [0, 0], "policy": [0, 0]}
    for o in objs:
        rs = [r for r in usable if o == "ALL" or r["obj"] == o]
        fin = [SO.decide(SO.POLICY[r["obj"]]["rule"], r["o"], r["a"], r["b"]) if r["obj"] in SO.POLICY else r["o"] for r in rs]
        ok0 = sum(r["o"] == r["gold"] for r in rs)
        ok1 = sum(f == r["gold"] for f, r in zip(fin, rs))
        w = sum(f == r["gold"] and r["o"] != r["gold"] for f, r in zip(fin, rs))
        l = sum(f != r["gold"] and r["o"] == r["gold"] for f, r in zip(fin, rs))
        print(f"  {o:<16}n={len(rs):>3}  ours {ok0 / len(rs):>6.1%} ({ok0}/{len(rs)})   with policy {ok1 / len(rs):>6.1%} ({ok1}/{len(rs)})   wins/losses {w}/{l}")

    # paired comparison with ours: wins = policy right and ours wrong, losses = the reverse; two-sided sign test
    from math import comb
    def sign_p(w, l):
        n = w + l
        if n == 0:
            return 1.0
        k = min(w, l)
        return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)
    print("\nPaired against ours: wins/losses (policy right & ours wrong / ours right & policy wrong), sign-test p")
    print(f"{'policy':<38}" + "".join(f"{o[:12]:>20}" for o in objs))
    for nm in names[1:]:
        line = f"{nm:<38}"
        for o in objs:
            rs = [r for r in usable if o == "ALL" or r["obj"] == o]
            w = sum(P[nm](r["o"], r["a"], r["b"]) == r["gold"] and r["o"] != r["gold"] for r in rs)
            l = sum(P[nm](r["o"], r["a"], r["b"]) != r["gold"] and r["o"] == r["gold"] for r in rs)
            line += f"{w}/{l} p={sign_p(w, l):.2f}".rjust(20)
        print(line)
    # latency and requests
    for v, d in (("A", A), ("B", B)):
        L = [r["latency_s"] for r in d.values() if r.get("latency_s") and not r.get("cached", False) or r.get("latency_s")]
        L = sorted(x for x in L if x)
        if L:
            pt = sum(r.get("prompt_tokens") or 0 for r in d.values())
            ct = sum(r.get("completion_tokens") or 0 for r in d.values())
            print(f"\nvariant {v}: {len(d)} questions, latency median {L[len(L) // 2]:.0f}s, p90 {L[int(len(L) * .9)]:.0f}s, "
                  f"mean {sum(L) / len(L):.0f}s; tokens in {pt}, out {ct}; retried {sum(bool(r.get('retried')) for r in d.values())}; "
                  f"errors {sum(bool(r.get('error')) for r in d.values())}; no count {sum(r.get('count') is None for r in d.values())}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["ours", "pick", "nemo", "report"])
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--drawings", type=int, default=160)
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--offset", type=int, default=0, help="skip this many seeded drawings (extend the pool)")
    ap.add_argument("--per-object", type=int, default=43)
    ap.add_argument("--variant", default="A", choices=["A", "B"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--all", action="store_true", help="report: every estimate answer, not the capped sample")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    {"ours": phase_ours, "pick": phase_pick, "nemo": phase_nemo, "report": phase_report}[a.phase](a)


if __name__ == "__main__":
    main()
