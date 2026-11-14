"""Model bake-off: other frontier models on the same Stoa preview TEST questions, next to our router.

    python tools/model_bakeoff.py samples                       # build and freeze the three question sets
    python tools/model_bakeoff.py run --model M [--mode image|text] [--workers 3]
    python tools/model_bakeoff.py ours                          # our router (Nemotron off) on the same questions
    python tools/model_bakeoff.py report                        # out/model_bakeoff.json + docs/model_bakeoff.md

Question sets (all from the preview TEST split, English wording, fixed seed, frozen in out/bakeoff/samples.json):
  base150  the Nemotron-baseline sample (tools/nemo_baseline.py build_sample), same questions, same prompt
  weak110  scan weak spots: 40 CubiCasa door, 40 window, 30 room-type counts (kitchen, bedroom, bathroom,
           sauna, wc_room, balcony), one question per drawing inside each stratum
  text60   Swiss counts 20, Swiss areas 20, IFC-plan-PDF areas 20 (for the text-only comparison)

Image mode sends the drawing as one image (long edge 1600 px) plus the baseline prompt. Text mode sends the
PDF text layer (PyMuPDF get_text) instead. Replies are cached on disk by bpqa/nemotron.py (keyed by model),
so a rerun is free. The API key is read from the environment or STOA_ENV_FILE and is never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

ENV_FILE = os.environ.get("STOA_ENV_FILE")
if ENV_FILE and Path(ENV_FILE).exists() and not os.environ.get("NVIDIA_API_KEY"):
    for _l in Path(ENV_FILE).read_text(encoding="utf-8").splitlines():
        if _l.startswith("NVIDIA_API_KEY="):
            os.environ["NVIDIA_API_KEY"] = _l.split("=", 1)[1].strip()
os.environ.setdefault("NEMO_MIN_INTERVAL", "0.4")

import pymupdf

from bpqa import nemotron as N
from bpqa.data import data_root, load_questions, split_of
from bpqa.query import parse
from bpqa.router import Context, solve
from eval import score
from nemo_baseline import build_sample, prompt_for, render

OUTD = REPO / "out" / "bakeoff"
SAMPLES = OUTD / "samples.json"
SEED = 2026
ROOM_OBJS = ["kitchen", "bedroom", "bathroom", "sauna", "wc_room", "balcony"]


def slug(m: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "_", m.lower())


def english(q) -> bool:
    return q.question.startswith(("How many", "What is"))


def pick_distinct(pool, n, rng, key=lambda q: q.path, cap=1):
    pool = sorted(pool, key=lambda q: q.qid)
    rng.shuffle(pool)
    used, out = defaultdict(int), []
    for q in pool:
        if used[key(q)] < cap:
            used[key(q)] += 1
            out.append(q)
        if len(out) >= n:
            break
    return out


def build_sets(root):
    rng = random.Random(SEED)
    qs = [q for q in load_questions(root) if split_of(q.path) == "test" and english(q)]
    cubi = [q for q in qs if q.source == "cubicasa5k" and q.type == "count" and q.ext == "png"
            and parse(q.question).scope != "model"]
    weak = []
    for obj, n in [("door", 40), ("window", 40)]:
        weak += pick_distinct([q for q in cubi if parse(q.question).obj == obj], n, rng)
    per = 5
    for obj in ROOM_OBJS:
        weak += pick_distinct([q for q in cubi if parse(q.question).obj == obj], per, rng)
    weak_cat = {}
    for q in weak:
        o = parse(q.question).obj
        weak_cat[q.qid] = "scan_door" if o == "door" else "scan_window" if o == "window" else "scan_room_type"
    sw = [q for q in qs if q.source == "swiss_dwellings" and q.ext == "pdf"]
    ifc = [q for q in qs if q.source == "ifc_models" and q.ext == "pdf" and q.type == "area"
           and parse(q.question).scope != "model"]
    text = (pick_distinct([q for q in sw if q.type == "count"], 20, rng, cap=2)
            + pick_distinct([q for q in sw if q.type == "area"], 20, rng, cap=2)
            + pick_distinct(ifc, 20, rng, cap=20))
    base = build_sample(root)
    return {"base150": [q.qid for q in base], "weak110": [q.qid for q in weak], "text60": [q.qid for q in text],
            "weak_category": weak_cat}


def load_all(root):
    qs = {q.qid: q for q in load_questions(root)}
    if not SAMPLES.exists():
        OUTD.mkdir(parents=True, exist_ok=True)
        SAMPLES.write_text(json.dumps(build_sets(root)), encoding="utf-8")
    s = json.loads(SAMPLES.read_text(encoding="utf-8"))
    return qs, s


def category(q, s) -> str:
    """Scoring category of a question."""
    if str(q.qid) in s["weak_category"] or q.qid in s["weak_category"]:
        return s["weak_category"].get(str(q.qid)) or s["weak_category"].get(q.qid)
    o = parse(q.question).obj
    if q.source == "cubicasa5k":
        return "scan_door" if o == "door" else "scan_window" if o == "window" else \
            "scan_room_type" if o in ROOM_OBJS else "scan_other"
    if q.source == "swiss_dwellings":
        return "swiss_area" if q.type == "area" else "swiss_count"
    return "ifc_area"


def text_prompt(q, text: str) -> str:
    unit = " Give the area in square metres." if q.type == "area" else ""
    return ("You are given the text layer extracted from a construction drawing PDF (labels and numbers in "
            "reading order, no geometry) and a question about the drawing.\n"
            f"--- drawing text ---\n{text[:12000]}\n--- end ---\n"
            f"Question: {q.question}\n"
            f"Reply with just the number, no units and no explanation.{unit}")


def run_model(root, model, mode, qids, workers, qs, max_tokens=4096, retry_tokens=16384):
    OUTD.mkdir(parents=True, exist_ok=True)
    outp = OUTD / f"{slug(model)}__{mode}.jsonl"
    done = {}
    if outp.exists():
        for line in outp.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done[r["qid"]] = r
    todo = [qs[i] for i in qids if i not in done]
    pre = {}
    for q in todo:          # render sequentially (PyMuPDF is not thread-safe)
        if q.path not in pre:
            pre[q.path] = render(root, q.path) if mode == "image" else \
                pymupdf.open(root / q.path)[0].get_text()

    def one(q):
        if mode == "image":
            r = N.chat(prompt_for(q), pre[q.path][0], max_tokens=max_tokens, model=model)
            if r.get("finish_reason") == "length" or (r.get("content") is None and not r.get("error")):
                r = N.chat(prompt_for(q), pre[q.path][0], max_tokens=retry_tokens, model=model)
        else:
            p = text_prompt(q, pre[q.path])
            r = N.chat(p, None, max_tokens=max_tokens, model=model)
            if r.get("finish_reason") == "length" or (r.get("content") is None and not r.get("error")):
                r = N.chat(p, None, max_tokens=retry_tokens, model=model)
        return {"qid": q.qid, "pred": N.parse_number(r.get("content")), "raw": (r.get("content") or "")[:80],
                "error": r.get("error"), "prompt_tokens": r.get("prompt_tokens"),
                "completion_tokens": r.get("completion_tokens"), "latency_s": r.get("latency_s"),
                "finish": r.get("finish_reason")}

    # pilot: first 30 questions, cut a hopeless model (under 12% right, or mostly errors)
    class A:
        def __init__(s, v):
            s.value, s.answered = v, v is not None
    t0 = time.time()
    with open(outp, "a", encoding="utf-8") as fh, ThreadPoolExecutor(workers) as ex:
        for k, rec in enumerate(ex.map(one, todo)):
            if rec["error"] == "no NVIDIA_API_KEY":      # --cache-only miss
                continue
            done[rec["qid"]] = rec
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            if k + 1 == 30 and all(q.qid in done for q in todo[:30]):
                pilot = [done[q.qid] for q in todo[:30]]
                ok = sum(1 for r, q in zip(pilot, todo[:30]) if score(q, A(r["pred"])))
                err = sum(1 for r in pilot if r["error"])
                print(f"{model} pilot: {ok}/30 right, {err} errors, {time.time() - t0:.0f}s", flush=True)
                if ok / 30 < 0.12 or err > 15:
                    print(f"{model}: CUT after pilot", flush=True)
                    ex.shutdown(wait=False, cancel_futures=True)
                    (OUTD / f"{slug(model)}__{mode}.cut").write_text(f"{ok}/30 right, {err} errors")
                    break
            if (k + 1) % 25 == 0:
                print(f"{model} {mode}: {k + 1}/{len(todo)} {time.time() - t0:.0f}s", flush=True)
    return done


class _A:
    def __init__(self, v):
        self.value, self.abstain = v, (None if v is not None else "no number")
        self.answered = v is not None


def run_ours(root, qs, s, pdf_only):
    ids = sorted(set(s["base150"]) | set(s["weak110"]) | set(s["text60"]))
    res = {}
    for i in ids:
        q = qs[i]
        if pdf_only and q.ext != "pdf":
            continue
        ctx = Context(root=root, q=q, out_dir=REPO / "out" / "evidence", evidence=False, pdf_only=pdf_only,
                      nemotron=False)
        t = time.time()
        query, a = solve(ctx)
        res[i] = {"pred": a.value if a.answered else None, "route": a.route, "ok": bool(score(q, a)),
                  "tier": getattr(a, "tier", None), "abstain": a.abstain, "latency_s": round(time.time() - t, 2)}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["samples", "run", "ours", "report"])
    ap.add_argument("--model")
    ap.add_argument("--mode", default="image")
    ap.add_argument("--sets", default="weak110,base150")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--data")
    ap.add_argument("--cache-only", action="store_true", help="write records for cached replies only, no API calls")
    a = ap.parse_args()
    if a.cache_only:
        N._key = lambda: None
    root = data_root(a.data)
    qs, s = load_all(root)
    if a.cmd == "samples":
        for k in ("base150", "weak110", "text60"):
            print(k, len(s[k]), "questions", len({qs[i].path for i in s[k]}), "drawings")
        from collections import Counter
        print(Counter(s["weak_category"].values()))
        return
    if a.cmd == "run":
        ids = []
        for name in a.sets.split(","):
            for i in s[name]:
                if i not in ids:
                    ids.append(i)
        if a.mode == "image":      # round-robin over categories, so any prefix of a slow run stays balanced
            by = defaultdict(list)
            for i in ids:
                by[category(qs[i], s)].append(i)
            order = sorted(by, key=lambda c: (c not in ("scan_door", "scan_window", "scan_room_type"), c))
            for c in by:
                random.Random(1).shuffle(by[c])
            ids = [by[c][k] for k in range(max(map(len, by.values()))) for c in order if k < len(by[c])]
        run_model(root, a.model, a.mode, ids, a.workers, qs)
        return
    if a.cmd == "ours":
        out = {"full": run_ours(root, qs, s, False), "pdf_only": run_ours(root, qs, s, True)}
        (OUTD / "ours.json").write_text(json.dumps(out), encoding="utf-8")
        print("ours done", len(out["full"]), len(out["pdf_only"]))
        return
    from bakeoff_report import report
    report(root, qs, s)


if __name__ == "__main__":
    main()
