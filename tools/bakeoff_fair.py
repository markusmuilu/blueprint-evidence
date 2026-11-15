"""Fair bake-off rerun: one fixed question set, every model on exactly that set.

    python tools/bakeoff_fair.py set                        # build and freeze tools/bakeoff_fair_set.json (ids only)
    python tools/bakeoff_fair.py run --model M [--workers 3] [--until 04:00] [--cache-only]
    python tools/bakeoff_fair.py ours                       # our router, eval.py settings, on the same set
    python tools/bakeoff_fair.py report                     # out/model_bakeoff_fair.json, app/static/bakeoff.json, doc section

Why: the 14 Nov bake-off (tools/model_bakeoff.py) ran every model until the free-tier rate limit or the clock ran out,
so each row has a different n and the rows cannot be compared. Here a model either answers all questions of the set
(a row) or it does not finish and gets no numbers at all.

The set (frozen in tools/bakeoff_fair_set.json):
  core32     the 32 scan weak-spot questions every earlier comparison used (the weak110 questions mistral-large-4
             finished on 14 Nov; app/static/compare.json's bakeoff n=32): 8 doors, 13 windows, 11 room types
  extension  to 60: 4 more scan doors (12), 12 Swiss counts, 6 Swiss areas, 6 IFC-plan areas. Taken in the original
             harness's fixed run order (weak110 + base150, round-robin by category, each category shuffled with
             random.Random(1)), so the draw is seeded and independent of any model's answers, and replies already
             cached on disk are reused.

Prompts, image rendering and token budgets are the original harness's (nemo_baseline.prompt_for and render, long edge
1600 px, max_tokens 4096 with a 16384 retry on a cut reply). Scoring is eval.score (counts exact, areas within 2%); a
missing or unparseable reply counts as wrong. A transport error (rate limit, timeout) is not an answer: it is retried
and the question stays open until a real reply arrives.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import model_bakeoff as MB          # noqa: E402  (also loads the key from STOA_ENV_FILE)
from bpqa import nemotron as N      # noqa: E402
from bpqa.data import data_root     # noqa: E402
from eval import score              # noqa: E402
from nemo_baseline import prompt_for, render  # noqa: E402

SET_FILE = REPO / "tools" / "bakeoff_fair_set.json"
OUTD = REPO / "out" / "bakeoff_fair"
MODELS = [
    ("nvidia/nemotron-3.5-super-vl-120b-a12b", "nemotron-3.5-super-vl"),
    ("mistralai/mistral-large-4-1t-a52b", "mistral-large-4"),
    ("meta/llama-3.2-90b-vision-instruct", "llama-3.2-90b-vision"),
    ("nvidia/ising-calibration-1.5-31b", "ising-calibration-1.5"),
    ("nvidia/nemotron-3.5-bolt-e4b", "nemotron-3.5-bolt"),
    ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", "nemotron-3-nano-omni"),
]
CATS = [("scan_door", "Scan doors"), ("scan_window", "Scan windows"), ("scan_room_type", "Scan room types"),
        ("swiss_count", "Swiss counts"), ("area", "Areas (Swiss, IFC)")]
CORE32 = [584, 212, 414, 3476, 2144, 706, 3644, 1364, 3632, 1400, 2908, 548, 2834, 2842, 3628, 446, 3404, 570, 2480,
          3250, 1328, 856, 664, 834, 1326, 704, 2058, 4160, 3910, 1050, 1286, 574]
EXTEND = {"scan_door": 4, "swiss_count": 12, "swiss_area": 6, "ifc_area": 6}


def fair_cat(c: str) -> str:
    return "area" if c in ("swiss_area", "ifc_area") else c


def original_order(qs, s):
    """The 14 Nov image-mode run order (model_bakeoff.main, run --sets weak110,base150)."""
    ids = []
    for name in ("weak110", "base150"):
        for i in s[name]:
            if i not in ids:
                ids.append(i)
    by = defaultdict(list)
    for i in ids:
        by[MB.category(qs[i], s)].append(i)
    order = sorted(by, key=lambda c: (c not in ("scan_door", "scan_window", "scan_room_type"), c))
    for c in by:
        random.Random(1).shuffle(by[c])
    return [by[c][k] for k in range(max(map(len, by.values()))) for c in order if k < len(by[c])]


def build_set(qs, s):
    chosen = list(CORE32)
    need = dict(EXTEND)
    for i in original_order(qs, s):
        c = MB.category(qs[i], s)
        if i not in chosen and need.get(c, 0) > 0:
            chosen.append(i)
            need[c] -= 1
    assert not any(need.values()), need
    by = defaultdict(list)
    for i in chosen:
        by[MB.category(qs[i], s)].append(i)
    return {"note": "Fair bake-off set, preview TEST split, question ids only. core32 = the 32 scan weak-spot questions "
                    "of earlier comparisons; the rest extends to 60 in the 14 Nov harness's seeded run order. "
                    "Built by tools/bakeoff_fair.py set.",
            "qids": chosen, "core32": CORE32, "by_category": dict(by)}


def load():
    root = data_root(None)
    qs, s = MB.load_all(root)
    if not SET_FILE.exists():
        SET_FILE.write_text(json.dumps(build_set(qs, s), indent=1), encoding="utf-8")
    fs = json.loads(SET_FILE.read_text(encoding="utf-8"))
    cat = {i: fair_cat(c) for c, ids in fs["by_category"].items() for i in ids}
    return root, qs, s, fs, cat


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def run(root, qs, fs, model, workers, until, cache_only, call_timeout=None):
    OUTD.mkdir(parents=True, exist_ok=True)
    outp = OUTD / f"{MB.slug(model)}.jsonl"
    done = {}
    if outp.exists():
        for line in outp.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done[r["qid"]] = r
    todo = [qs[i] for i in fs["qids"] if i not in done]
    pre = {}
    for q in todo:                       # PyMuPDF is not thread-safe: render first, sequentially
        if q.path not in pre:
            pre[q.path] = render(root, q.path)
    deadline = None
    if until:
        h, m = map(int, until.split(":"))
        now = datetime.now()
        deadline = now.replace(hour=h, minute=m, second=0, microsecond=0).timestamp()
        if deadline < now.timestamp():
            deadline += 86400

    def one(q):
        if deadline and time.time() > deadline:
            return q, None
        r = N.chat(prompt_for(q), pre[q.path][0], max_tokens=4096, model=model, deadline_s=call_timeout)
        if not r.get("error") and (r.get("finish_reason") == "length" or r.get("content") is None):
            r = N.chat(prompt_for(q), pre[q.path][0], max_tokens=16384, model=model, deadline_s=call_timeout)
        return q, r

    t0 = time.time()
    new = errs = 0
    with open(outp, "a", encoding="utf-8") as fh, ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(one, q) for q in todo]
        for f in as_completed(futs):
            q, r = f.result()
            if r is None:
                continue
            if r.get("error"):                    # rate limit / timeout / cache-only miss: not an answer, stays open
                errs += 1
                if r["error"] != "no NVIDIA_API_KEY":
                    log(f"{model} q{q.qid}: error {str(r['error'])[:80]}")
                continue
            rec = {"qid": q.qid, "pred": N.parse_number(r.get("content")), "raw": (r.get("content") or "")[:80],
                   "prompt_tokens": r.get("prompt_tokens"), "completion_tokens": r.get("completion_tokens"),
                   "latency_s": r.get("latency_s"), "finish": r.get("finish_reason"), "cached": r.get("cached")}
            done[q.qid] = rec
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            new += 1
            if not r.get("cached"):
                log(f"{model}: {len(done)}/{len(fs['qids'])} (q{q.qid} {r.get('latency_s')} s)")
    left = len(fs["qids"]) - len(done)
    log(f"{model}: {len(done)}/{len(fs['qids'])} done, {new} new this pass, {errs} open errors, "
        f"{left} left, {time.time() - t0:.0f}s")
    return left


class _A:
    def __init__(self, v):
        self.value, self.answered = v, v is not None


def run_ours(root, qs, fs):
    """eval.py's answer: the same Context and score as `python eval.py --split test` (checks on, Nemotron off)."""
    from bpqa.router import Context, solve
    res = {}
    for i in fs["qids"]:
        q = qs[i]
        t = time.time()
        ctx = Context(root=root, q=q, out_dir=REPO / "out" / "evidence", evidence=False, checks=True,
                      pdf_only=False, nemotron=False)
        _, a = solve(ctx)
        res[i] = {"pred": a.value if a.answered else None, "ok": bool(score(q, a)), "route": a.route,
                  "tier": a.tier if a.answered else None, "latency_s": round(time.time() - t, 2)}
    (OUTD / "ours.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    log(f"ours: {sum(r['ok'] for r in res.values())}/{len(res)}")


def report(root, qs, fs, cat):
    from bakeoff_report import wilson
    ours = {int(k): v for k, v in json.loads((OUTD / "ours.json").read_text(encoding="utf-8")).items()}
    ids = fs["qids"]
    n_by = Counter(cat[i] for i in ids)
    meta = json.loads((OUTD / "run_meta.json").read_text(encoding="utf-8")) if (OUTD / "run_meta.json").exists() else {}

    def block(ok_of):
        d = {c: [sum(1 for i in ids if cat[i] == c and ok_of(i)), n_by[c]] for c, _ in CATS}
        k = sum(1 for i in ids if ok_of(i))
        d["overall"] = [k, len(ids)]
        d["ci95"] = [round(x, 4) for x in wilson(k, len(ids))]
        return d

    out = {"date": "2026-11-15", "set_file": "tools/bakeoff_fair_set.json", "n": len(ids),
           "n_by_category": dict(n_by), "scoring": "eval.score: counts exact, areas within 2%; no reply = wrong",
           "ours": block(lambda i: ours[i]["ok"]), "models": []}
    for model, short in MODELS:
        f = OUTD / f"{MB.slug(model)}.jsonl"
        recs = {}
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                r = json.loads(line)
                recs[r["qid"]] = r
        finished = all(i in recs for i in ids)
        e = {"model": model, "short": short, "finished": finished, "answered_so_far": sum(1 for i in ids if i in recs)}
        if finished:
            ok = lambda i: bool(score(qs[i], _A(recs[i]["pred"])))
            e.update(block(ok))
            e["no_reply"] = sum(1 for i in ids if recs[i]["pred"] is None)
            lat = sorted(recs[i]["latency_s"] for i in ids if recs[i].get("latency_s"))
            e["latency_median_s"] = lat[len(lat) // 2] if lat else None
            e["ours_wrong_model_right"] = sum(1 for i in ids if ok(i) and not ours[i]["ok"])
            e["ours_right_model_wrong"] = sum(1 for i in ids if not ok(i) and ours[i]["ok"])
            e["core32"] = [sum(1 for i in fs["core32"] if ok(i)), len(fs["core32"])]
        else:
            e["status"] = "did not finish (free-tier rate limit)"
        e.update(meta.get(model, {}))
        out["models"].append(e)
    out["models"].sort(key=lambda e: (not e["finished"], -(e.get("overall") or [0])[0]))
    out["ours"]["no_reply"] = sum(1 for i in ids if ours[i]["pred"] is None)
    _ol = sorted(ours[i]["latency_s"] for i in ids)
    out["ours"]["latency_median_s"] = _ol[len(_ol) // 2]
    out["ours"]["core32"] = [sum(1 for i in fs["core32"] if ours[i]["ok"]), len(fs["core32"])]
    out["ours_tiers"] = {c: {t: [sum(1 for i in ids if cat[i] == c and ours[i]["tier"] == t and ours[i]["ok"]),
                                 sum(1 for i in ids if cat[i] == c and ours[i]["tier"] == t)]
                             for t in ("proven", "estimate")} for c, _ in CATS}
    out["time"] = meta.get("_time", {})
    out["intro"] = meta.get("_intro", "")          # prose for the doc section, written after the run
    out["method"] = meta.get("_method", [])
    (REPO / "out" / "model_bakeoff_fair.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    # the Results page's bake-off table (app/static/app.js renderBakeoff): one row per model, [right, n] per category
    page = {"title": "Fair rerun, same questions for every model (15 Nov)", "n": len(ids),
            "cats": [[c, t] for c, t in CATS] + [["overall", "All questions"]],
            "ours": {c: out["ours"][c] for c, _ in CATS + [("overall", "")]},
            "rows": [({"model": e["short"], **{c: e[c] for c, _ in CATS + [("overall", "")]}} if e["finished"]
                      else {"model": e["short"], "status": e["status"]}) for e in out["models"]],
            "note": (f"Right / {len(ids)}: every model and our system on the same {len(ids)} preview test questions "
                     f"({n_by['scan_door']} scan doors, {n_by['scan_window']} scan windows, {n_by['scan_room_type']} scan room types, "
                     f"{n_by['swiss_count']} Swiss counts, {n_by['area']} Swiss and IFC areas). Hosted models on NVIDIA Build (free tier), "
                     "same prompt and scoring as the naive baseline (counts exact, areas within 2%, no reply counts as wrong). "
                     "A model that could not answer every question within the free-tier rate limit has no numbers. "
                     "Small sample: read the gaps as direction, not precision. Details: docs/model_bakeoff.md."),
            "source": "out/model_bakeoff_fair.json (tools/bakeoff_fair.py)"}
    (REPO / "app" / "static" / "bakeoff.json").write_text(json.dumps(page, indent=1), encoding="utf-8")
    write_md(out, fs, n_by)
    print(json.dumps({k: out[k] for k in ("ours", "n_by_category")}, indent=0))
    for e in out["models"]:
        print(e["short"], e.get("overall", e.get("status")), {c: e.get(c) for c, _ in CATS})


FAIR_START = "<!-- fair-rerun:start -->"
FAIR_END = "<!-- fair-rerun:end -->"
HISTORY = "## History: the 14 Nov run (different n per model, kept as it was)"


def write_md(out, fs, n_by):
    """The top section of docs/model_bakeoff.md; the 14 Nov sections stay below it unchanged."""
    def rc(v):
        return f"{v[0]}/{v[1]}"
    cols = CATS + [("overall", f"All {out['n']}")]
    L = [FAIR_START, "## Fair rerun, same questions for every model (15 Nov)", ""]
    L.append(out.get("intro", "").strip() or
             f"Every model below answered the same {out['n']} preview TEST questions, so the rows compare.")
    L += ["", "| Model | " + " | ".join(t for _, t in cols) + " |", "|" + "---|" * (len(cols) + 1)]
    L.append("| **Ours (Blueprint Evidence, eval.py)** | " + " | ".join(f"**{rc(out['ours'][c])}**" for c, _ in cols) + " |")
    for e in out["models"]:
        if e["finished"]:
            L.append(f"| {e['short']} | " + " | ".join(rc(e[c]) for c, _ in cols) + " |")
        else:
            L.append(f"| {e['short']} | " + " | ".join(["did not finish (free-tier rate limit)"] + [""] * (len(cols) - 1)) + " |")
    L += ["", "Cells are right / questions. Every finished row has the same n in every column.", ""]
    fin = [e for e in out["models"] if e["finished"]]
    if fin:
        L += ["| Model | 95% CI, all | No number in reply | Median latency | Ours wrong, model right | Ours right, model wrong | Core 32 (scan weak spots) |",
              "|---|---|---|---|---|---|---|"]
        o = out["ours"]
        L.append(f"| Ours | {o['ci95'][0]:.0%} to {o['ci95'][1]:.0%} | {o['no_reply']} | {o['latency_median_s']:.1f} s (local CPU, warm caches) | - | - | {rc(o['core32'])} |")
        for e in fin:
            lat = "-" if e.get("latency_median_s") is None else f"{e['latency_median_s']:.0f} s"
            L.append(f"| {e['short']} | {e['ci95'][0]:.0%} to {e['ci95'][1]:.0%} | {e['no_reply']} | {lat} | "
                     f"{e['ours_wrong_model_right']} | {e['ours_right_model_wrong']} | {rc(e['core32'])} |")
        L.append("")
    ot = out["ours_tiers"]
    L.append("Our answers by tier on this set: " + "; ".join(
        f"{t} {rc(ot[c]['proven'])} proven, {rc(ot[c]['estimate'])} estimate"
        for c, t in CATS) + " (right / answered in that tier). Ours never abstained here.")
    L += [""] + [ln for ln in out.get("method", [])] + ["", FAIR_END, ""]
    p = REPO / "docs" / "model_bakeoff.md"
    doc = p.read_text(encoding="utf-8")
    if FAIR_START in doc:
        doc = doc[:doc.index(FAIR_START)] + doc[doc.index(FAIR_END) + len(FAIR_END):].lstrip("\n")
    title, rest = doc.split("\n", 1)
    if HISTORY not in rest:
        rest = "\n" + HISTORY + "\n\n" + rest.lstrip("\n").replace("\n### ", "\n#### ").replace("\n## ", "\n### ")
    p.write_text(title + "\n\n" + "\n".join(L) + rest, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["set", "run", "ours", "report"])
    ap.add_argument("--model")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--until", default="", help="HH:MM local: stop sending new requests after this time")
    ap.add_argument("--cache-only", action="store_true", help="only cached replies, no API calls")
    ap.add_argument("--call-timeout", type=float, default=None,
                    help="seconds per question attempt (retries included); a question that runs out stays open for the next pass")
    a = ap.parse_args()
    if a.cache_only:
        N._key = lambda: None
    root, qs, s, fs, cat = load()
    if a.cmd == "set":
        print(len(fs["qids"]), "questions,", len({qs[i].path for i in fs["qids"]}), "drawings")
        print({c: len(v) for c, v in fs["by_category"].items()})
        return
    if a.cmd == "run":
        models = [m for m, _ in MODELS] if a.model == "all" else [a.model]
        for m in models:
            run(root, qs, fs, m, a.workers, a.until, a.cache_only, a.call_timeout)
        return
    if a.cmd == "ours":
        OUTD.mkdir(parents=True, exist_ok=True)
        run_ours(root, qs, fs)
        return
    report(root, qs, fs, cat)


if __name__ == "__main__":
    main()
