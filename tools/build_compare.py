"""Build the data of the Compare tab: app/static/compare.json and the thumbnails in app/static/compare/.

    STOA_DATA=<dataset or demo set holding the example drawings> python tools/build_compare.py [--data DIR]
        --cost <out/cost/cost_by_route.json> --test-answers <out/test/answers.jsonl> [--bakeoff out/model_bakeoff.json]

Nothing here runs at page load and no model is called. Two inputs are read:

- the recorded Nemotron baseline (out/baseline_nemotron.json): the 150 preview TEST questions, Nemotron 3.5 Super VL given
  the drawing and the question, with its answer, correctness and latency. The left side of each example is a record from it.
- a fresh local run of our own router on the same question (through the app's /api/ask, recomputed, after one throwaway
  question to warm the process up). Its answer, status, route, time and evidence image are the right side. The time is the
  app's own "time_ms" for the first question on that drawing, on this machine (CPU, no GPU), and the file says so.

The totals are computed, with their sample sizes, from the baseline, the router's re-scored test run (out/test/answers.jsonl,
the same re-scoring as the README table), the bake-off file and the measured cost per route (out/cost/cost_by_route.json).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "app" / "static"
FULL = REPO.parent / "data" / "stoa-full" / "junction-dataset"

# The prepared examples, by baseline question id. The mix is on purpose: Nemotron wrong on a Swiss area, wrong on an IFC area,
# wrong on a scan count, no answer at all on a Swiss count, and one where Nemotron was right too.
EXAMPLES = [
    {"qid": 5924, "kind": "Swiss area"},
    {"qid": 4943, "kind": "IFC model area"},
    {"qid": 1866, "kind": "Scan count"},
    {"qid": 6404, "kind": "Swiss count"},
    {"qid": 1044, "kind": "Scan count, Nemotron right too"},
]
WARMUP = ("data/cubicasa5k/high_quality_architectural-10372.png", "How many windows are shown on this floor plan?")
USD_PER_EUR = 1.1052        # the rate app.js uses (fmtUsd); costs are computed in USD and shown in euros


def wilson(k: int, n: int) -> list[float]:
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [round(c - h, 4), round(c + h, 4)]


def totals(base: dict, test_answers: Path, bakeoff: Path, cost: Path) -> dict:
    qids = [r["qid"] for r in base["records"]]
    n = len(qids)
    nemo_ok = sum(1 for r in base["records"] if (r["nemotron"] or {}).get("correct"))
    ours = {}
    for line in open(test_answers, encoding="utf-8"):
        r = json.loads(line)
        ours[r["qid"]] = bool(r.get("correct"))
    ours_ok = sum(1 for q in qids if ours.get(q))
    t = {"questions": n, "nemotron_correct": nemo_ok, "nemotron_accuracy": round(nemo_ok / n, 4),
         "nemotron_ci95": wilson(nemo_ok, n), "ours_correct": ours_ok, "ours_accuracy": round(ours_ok / n, 4),
         "ours_ci95": wilson(ours_ok, n),
         "ours_source": "current router, re-scored on the same 150 questions from out/test/answers.jsonl",
         "nemotron_source": "out/baseline_nemotron.json"}
    b = json.loads(bakeoff.read_text(encoding="utf-8"))
    best = None
    vision = {k: m for k, m in b["models"].items() if m.get("mode") == "image"}     # the six hosted vision models
    for name, m in vision.items():
        w = m["sets"].get("weak110")
        if w and w["n"] >= 20 and (best is None or w["accuracy"] > best[1]["accuracy"]):
            best = (name, w)
    name, w = best
    t["bakeoff"] = {"models": len(vision), "best_model": name.split("__")[0], "set": "weak110",
                    "n": w["n"], "best_correct": w["correct"], "best_accuracy": round(w["accuracy"], 4),
                    "ours_correct": w["ours_correct"], "ours_accuracy": round(w["ours_accuracy"], 4),
                    "source": "out/model_bakeoff.json (docs/model_bakeoff.md)"}
    # cost per 1,000 questions of the 150 mix: Nemotron at the assumed list price and its measured tokens;
    # ours = measured CPU seconds per route at the assumed vCPU price (cost_by_route.json), by the router's route per question
    pin, pout = base.get("price_assumption_usd_per_mtok", [3.0, 15.0])
    recs = [r["nemotron"] for r in base["records"] if r.get("nemotron") and r["nemotron"].get("prompt_tokens") is not None]
    nemo_usd = sum((x["prompt_tokens"] * pin + (x["completion_tokens"] or 0) * pout) / 1e6 for x in recs) / len(recs)
    per_route = {r["route"]: r["usd_per_1000_cpu"] for r in json.loads(cost.read_text(encoding="utf-8"))["routes"]}
    routes = [(r["ours"] or {}).get("route") for r in base["records"]]
    ours_usd1000 = sum(per_route[x] for x in routes) / n
    t["cost"] = {"nemotron_usd_per_1000": round(nemo_usd * 1000, 2), "nemotron_eur_per_1000": round(nemo_usd * 1000 / USD_PER_EUR, 2),
                 "ours_usd_per_1000": round(ours_usd1000, 4), "ours_eur_per_1000": round(ours_usd1000 / USD_PER_EUR, 4),
                 "usd_per_eur": USD_PER_EUR, "price_in_out_usd_per_mtok": [pin, pout],
                 "note": "Nemotron: measured tokens per question at an assumed list price. Ours: measured CPU time per route at an assumed "
                         "vCPU price, weighted by the routes the 150 questions took. Neither is a quote; NVIDIA Build itself was free."}
    return t


def _correct(rec: dict, a: dict) -> bool:
    """Our answer against the dataset answer, scored like eval.score (counts exact, areas within 2%)."""
    try:
        g, p = float(rec["gold"]), float(a.get("value"))
    except (TypeError, ValueError):
        return False
    if rec["type"] == "count":
        return abs(p - round(p)) < 1e-6 and int(round(p)) == int(g)
    return abs(p - g) <= 0.02 * abs(g) if g else abs(p) < 1e-9


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="dataset root holding the example drawings (default: $STOA_DATA, else the full dataset)")
    ap.add_argument("--baseline", default=str(REPO / "out" / "baseline_nemotron.json"))
    ap.add_argument("--bakeoff", default=str(REPO / "out" / "model_bakeoff.json"))
    ap.add_argument("--cost", default=str(REPO / "out" / "cost" / "cost_by_route.json"))
    ap.add_argument("--test-answers", default=str(REPO / "out" / "test" / "answers.jsonl"))
    ap.add_argument("--no-run", action="store_true", help="skip the local run; keep the examples of the existing compare.json")
    args = ap.parse_args()
    data = args.data or os.environ.get("STOA_DATA") or str(FULL)
    os.environ["STOA_DATA"] = data
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")      # CPU only; "" breaks Ultralytics device selection with a CUDA build of torch
    os.environ.setdefault("NEMO_IMAGE_ESTIMATE", "0")      # as in the live app (deploy/web.py)
    os.environ.setdefault("NEMO_DEADLINE_S", "15")
    sys.path.insert(0, str(REPO))

    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    by_qid = {r["qid"]: r for r in base["records"]}
    out_json = STATIC / "compare.json"

    examples = []
    if args.no_run:
        old = {e["qid"]: e for e in json.loads(out_json.read_text(encoding="utf-8"))["examples"]}
        examples = [old[e["qid"]] for e in EXAMPLES]
    else:
        from fastapi.testclient import TestClient
        from PIL import Image
        from app import server
        client = TestClient(server.app)

        def ask(path: str, question: str) -> dict:
            r = client.post("/api/ask", json={"path": path, "question": question, "refresh": True})
            r.raise_for_status()
            return r.json()

        print("warm-up ...", flush=True)
        ask(*WARMUP)
        (STATIC / "compare").mkdir(exist_ok=True)
        for spec in EXAMPLES:
            r = by_qid[spec["qid"]]
            nem = r["nemotron"]
            print(r["qid"], r["path"], flush=True)
            a = ask(r["path"], r["question"])
            thumb = None
            if a.get("evidence_id"):
                png = client.get(f"/api/evidence/{a['evidence_id']}.png", params={"body": 1})
                png.raise_for_status()
                im = Image.open(io.BytesIO(png.content)).convert("RGB")
                im.thumbnail((720, 720))
                thumb = f"compare/{spec['qid']}.jpg"
                im.save(STATIC / thumb, "JPEG", quality=84, optimize=True)
            examples.append({
                "qid": r["qid"], "kind": spec["kind"], "path": r["path"], "drawing": a.get("drawing"), "source": r["source"],
                "type": r["type"], "question": r["question"], "gold": r["gold"], "unit": a.get("unit") or "",
                "nemotron": {"answer": nem["pred"], "correct": bool(nem["correct"]), "latency_s": nem["latency_s"],
                             "finish": nem.get("finish"), "completion_tokens": nem.get("completion_tokens")},
                "ours": {"answer": a.get("value"), "status": a.get("status"), "route": a.get("route"),
                         "correct": _correct(r, a), "time_s": round((a.get("time_ms") or 0) / 1000, 2),
                         "usd_per_1000": (a.get("compute") or {}).get("usd_per_1000"),
                         "evidence": thumb, "n_items": len(a.get("items") or [])},
            })

    doc = {"generated": time.strftime("%Y-%m-%d"),
           "note": "Left: the recorded Nemotron 3.5 Super VL baseline, no live calls. Right: our router on the first question for that "
                   "drawing, recomputed locally on a CPU machine; the time is the app's own measurement.",
           "examples": examples,
           "totals": totals(base, Path(args.test_answers), Path(args.bakeoff), Path(args.cost))}
    out_json.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", out_json)


if __name__ == "__main__":
    main()
