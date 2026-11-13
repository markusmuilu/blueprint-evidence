"""Naive Nemotron baseline vs our router on a fixed, seeded sample of preview TEST questions.

    python tools/nemo_baseline.py --phase sample|nemo|all

Sample: 150 questions of the preview test split (same split function and seed as eval.py), stratified by
source and type: CubiCasa counts 50, Swiss counts 40, Swiss areas 30, IFC areas via the rendered PDF 30;
inside a stratum the parsed object type is round-robined and at most 3 questions per drawing (IFC: one model, no cap) are taken.
Nemotron gets the drawing as one image (PDF page 1 rendered, long edge 1600 px) and a plain prompt that
asks for just the number. Scoring is eval.score (counts exact, areas within 2%). Responses are cached on
disk (bpqa/nemotron.py), so a rerun is free. Writes out/baseline_nemotron.json.
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pymupdf
from PIL import Image

from bpqa import nemotron as N
from bpqa.data import data_root, load_questions, split_of
from bpqa.query import parse
from bpqa.router import Context, solve
from eval import score

SEED = 2026
PLAN = [("cubicasa5k", "count", 50), ("swiss_dwellings", "count", 40), ("swiss_dwellings", "area", 30),
        ("ifc_models", "area", 30)]
OUT = REPO / "out" / "baseline_nemotron.json"

# Illustrative list price of a paid frontier API, USD per million tokens. An ASSUMPTION, not a quote.
PRICE_IN, PRICE_OUT = 3.0, 15.0


def build_sample(root) -> list:
    rng = random.Random(SEED)
    qs = [q for q in load_questions(root) if split_of(q.path) == "test"]
    out = []
    for src, typ, n in PLAN:
        pool = [q for q in qs if q.source == src and q.type == typ and q.ext in ("png", "pdf")
                and parse(q.question).scope != "model"]
        pool.sort(key=lambda q: q.qid)
        rng.shuffle(pool)
        by_obj = defaultdict(list)
        per_drawing = defaultdict(int)
        for q in pool:
            by_obj[parse(q.question).obj or "?"].append(q)
        pick, objs = [], sorted(by_obj)
        while len(pick) < n and any(by_obj.values()):
            for o in objs:
                while by_obj[o]:
                    q = by_obj[o].pop(0)
                    if per_drawing[q.path] < (n if src == "ifc_models" else 3):
                        per_drawing[q.path] += 1
                        pick.append(q)
                        break
                if len(pick) >= n:
                    break
        out.extend(pick)
    return out


def render(root: Path, path: str):
    f = root / path
    if f.suffix.lower() == ".pdf":
        pg = pymupdf.open(f)[0]
        pix = pg.get_pixmap(dpi=150)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
    else:
        img = Image.open(f)
    return N.prepare_image(img)


def prompt_for(q) -> str:
    unit = " Give the area in square metres." if q.type == "area" else ""
    return ("You are given a construction drawing and a question about it.\n"
            f"Question: {q.question}\n"
            f"Reply with just the number, no units and no explanation.{unit}")


def run_nemo(root, sample, workers=3):
    cache = {}
    for q in sample:        # render sequentially (PyMuPDF is not thread-safe), call the API in parallel
        if q.path not in cache:
            cache[q.path] = render(root, q.path)

    def one(q):
        png, sent, orig = cache[q.path]
        r = N.chat(prompt_for(q), png)
        return {"qid": q.qid, "pred": N.parse_number(r.get("content")), "raw": (r.get("content") or "")[:80],
                "error": r.get("error"), "prompt_tokens": r.get("prompt_tokens"),
                "completion_tokens": r.get("completion_tokens"), "latency_s": r.get("latency_s"),
                "finish": r.get("finish_reason"), "image": list(sent)}
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(one, sample))


class _A:       # minimal Answer-like for eval.score
    def __init__(self, v):
        self.value, self.abstain = v, (None if v is not None else "no number")
        self.answered = v is not None


def run_ours(root, sample, pdf_only):
    res = {}
    for q in sample:
        if pdf_only and q.ext != "pdf":
            continue
        ctx = Context(root=root, q=q, out_dir=REPO / "out" / "evidence", evidence=False, pdf_only=pdf_only)
        query, a = solve(ctx)
        res[q.qid] = {"pred": a.value if a.answered else None, "route": a.route, "ok": bool(score(q, a)),
                      "abstain": a.abstain}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all")
    ap.add_argument("--data")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()
    root = data_root(args.data)
    sample = build_sample(root)
    if args.phase == "sample":
        c = defaultdict(int)
        for q in sample:
            c[(q.source, q.type, parse(q.question).obj)] += 1
        for k, v in sorted(c.items(), key=str):
            print(k, v)
        print(len(sample), "questions", len({q.path for q in sample}), "drawings")
        return
    t0 = time.time()
    nemo = run_nemo(root, sample, args.workers)
    print(f"nemotron done {time.time() - t0:.0f}s", file=sys.stderr)
    ours = run_ours(root, sample, False)
    print(f"ours done {time.time() - t0:.0f}s", file=sys.stderr)
    ours_pdf = run_ours(root, sample, True)
    recs = []
    for q, n in zip(sample, nemo):
        ok = score(q, _A(n["pred"]))
        recs.append({"qid": q.qid, "path": q.path, "source": q.source, "type": q.type, "question": q.question,
                     "gold": q.answer, "obj": parse(q.question).obj, "nemotron": {**n, "correct": bool(ok)},
                     "ours": ours.get(q.qid), "ours_pdf_only": ours_pdf.get(q.qid)})
    summary = summarize(recs)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"seed": SEED, "plan": PLAN, "price_assumption_usd_per_mtok": [PRICE_IN, PRICE_OUT],
                               "summary": summary, "records": recs}, indent=1, ensure_ascii=False), encoding="utf-8")
    print_table(summary)


def summarize(recs):
    groups = defaultdict(list)
    for r in recs:
        for k in [(r["source"], r["type"]), ("ALL", "ALL")]:
            groups[k].append(r)
    rows = []
    for (s, t), rs in groups.items():
        n = len(rs)
        nem = [r["nemotron"] for r in rs]
        pin = [x["prompt_tokens"] or 0 for x in nem]
        pout = [x["completion_tokens"] or 0 for x in nem]
        lat = sorted(x["latency_s"] for x in nem if x["latency_s"])
        full = [r["ours"] for r in rs]
        po = [r["ours_pdf_only"] for r in rs if r["ours_pdf_only"]]
        rows.append({
            "source": s, "type": t, "n": n,
            "nemo_answered": sum(1 for x in nem if x["pred"] is not None),
            "nemo_correct": sum(x["correct"] for x in nem), "nemo_accuracy": sum(x["correct"] for x in nem) / n,
            "ours_answered": sum(1 for o in full if o["pred"] is not None),
            "ours_correct": sum(1 for o in full if o["ok"]), "ours_accuracy": sum(1 for o in full if o["ok"]) / n,
            "ours_pdfonly_n": len(po), "ours_pdfonly_correct": sum(1 for o in po if o["ok"]),
            "ours_pdfonly_accuracy": (sum(1 for o in po if o["ok"]) / len(po)) if po else None,
            "nemo_prompt_tokens_mean": sum(pin) / n, "nemo_completion_tokens_mean": sum(pout) / n,
            "nemo_latency_median_s": lat[len(lat) // 2] if lat else None,
            "nemo_latency_mean_s": sum(lat) / len(lat) if lat else None,
            "frontier_cost_per_q_measured_tokens": (sum(pin) * PRICE_IN + sum(pout) * PRICE_OUT) / 1e6 / n,
            "frontier_cost_per_q_direct_answer": (sum(pin) * PRICE_IN + 10 * n * PRICE_OUT) / 1e6 / n})
    rows.sort(key=lambda r: (r["source"] == "ALL", r["source"], r["type"]))
    return rows


def print_table(rows):
    for r in rows:
        po = "-" if r["ours_pdfonly_accuracy"] is None else f"{r['ours_pdfonly_accuracy']:.1%}"
        print(f"{r['source']:<16}{r['type']:<7}n={r['n']:<4} nemo {r['nemo_accuracy']:.1%} (ans {r['nemo_answered']}) "
              f"ours {r['ours_accuracy']:.1%} pdf-only {po} | tok in/out {r['nemo_prompt_tokens_mean']:.0f}/"
              f"{r['nemo_completion_tokens_mean']:.0f} lat med {r['nemo_latency_median_s']}s "
              f"cost ${r['frontier_cost_per_q_measured_tokens']:.4f}/${r['frontier_cost_per_q_direct_answer']:.4f}")


if __name__ == "__main__":
    main()
