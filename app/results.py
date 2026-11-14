"""Read eval.py outputs (out/<run>/summary.json, summary.txt, answers.jsonl) for the Results view.

Looks in this repo's out/ first, then in BPE_RESULTS, then in the sibling main checkout
(../stoa/out), because eval runs usually happen there. Read only.

Every run is labelled with the dataset it was measured on: the `data=` field eval.py writes into the
summary header, else the dataset suffix eval.py adds to the run name (`test-junction-dataset`). A run
with neither is labelled "dataset not recorded". The overview also reads the Nemotron baseline
(out/baseline_nemotron.json) and the unseen-drawing run (out/unseen/results.jsonl) when they exist.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATASET_NAMES = {"junction-dataset": "Stoa full set", "junction-dataset-preview": "Stoa preview set"}
FLAGS = {"prior": "with --prior baseline", "notiers": "without tiers", "nochecks": "checks off", "pdfonly": "PDF only",
         "nemo": "Nemotron fallback on"}


def result_dirs() -> list[Path]:
    dirs = [REPO / "out"]
    if os.environ.get("BPE_RESULTS"):
        dirs.append(Path(os.environ["BPE_RESULTS"]))
    dirs.append(REPO.parent / "stoa" / "out")
    seen, out = set(), []
    for d in dirs:
        r = d.resolve()
        if r not in seen and r.is_dir():
            seen.add(r)
            out.append(r)
    return out


def describe(run_id: str, header: str) -> dict:
    """Dataset, split and options of a run, from its summary header and its name."""
    m = re.search(r"\bdata=(\S+)", header or "")
    parts = run_id.split("-")
    split = parts[0] if parts[0] in ("dev", "test", "all") else ""
    data = m.group(1) if m else None
    if data is None:
        for key in sorted(DATASET_NAMES, key=len, reverse=True):
            if run_id.endswith("-" + key) or ("-" + key + "-") in run_id:
                data = key
                break
    flags = [FLAGS[f] for f in parts[1:] if f in FLAGS]
    objs = re.search(r"-([a-z_]+(?:\+[a-z_]+)+)$", run_id)
    if objs:
        flags.append("objects: " + objs.group(1).replace("+", ", "))
    qn = re.search(r"questions=(\d+)", header or "")
    name = DATASET_NAMES.get(data, data) if data else "dataset not recorded"
    label = f"{name}, {split} split" if split else name
    return {"dataset": data, "dataset_name": name, "split": split, "flags": flags,
            "questions": int(qn.group(1)) if qn else None,
            "label": label + (f" ({'; '.join(flags)})" if flags else "")}


def _order(run: dict) -> tuple:
    """This checkout's runs first, plain runs before flagged ones, test before dev, full set before preview."""
    return (run["where"] != "out", bool(run["flags"]), run["dataset"] is None, {"test": 0, "dev": 1}.get(run["split"], 2),
            run["dataset"] != "junction-dataset", run["id"])


def _where(sub: Path) -> str:
    try:
        return str(sub.parent.relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return os.path.relpath(sub.parent, REPO).replace("\\", "/")


def list_runs() -> list[dict]:
    runs, names = [], set()
    for d in result_dirs():
        for sub in sorted(d.iterdir()):
            if (sub / "summary.json").exists() and sub.name not in names:
                names.add(sub.name)
                header = ""
                txt = sub / "summary.txt"
                if txt.exists():
                    header = txt.read_text(encoding="utf-8", errors="replace").splitlines()[0][:200]
                runs.append({"id": sub.name, "dir": str(sub), "header": header,
                             "mtime": (sub / "summary.json").stat().st_mtime, "where": _where(sub),
                             **describe(sub.name, header)})
    runs.sort(key=_order)
    return runs


_CACHE: dict[str, tuple[float, dict]] = {}


def load_run(run_id: str) -> dict | None:
    run = next((r for r in list_runs() if r["id"] == run_id), None)
    if run is None:
        return None
    sub = Path(run["dir"])
    mtime = run["mtime"]
    hit = _CACHE.get(str(sub))
    if hit and hit[0] == mtime:
        return hit[1]
    rows = json.loads((sub / "summary.json").read_text(encoding="utf-8"))
    objects = []
    routes: dict[str, int] = defaultdict(int)
    ans = sub / "answers.jsonl"
    if ans.exists():
        agg: dict[tuple, list[int]] = defaultdict(lambda: [0, 0, 0])
        with open(ans, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                src = r["path"].split("/")[1]
                obj = (r.get("query") or {}).get("obj") or "other"
                a = agg[(src, obj)]
                a[0] += 1
                answered = r.get("abstain") is None and r.get("value") is not None
                a[1] += answered
                a[2] += bool(r.get("correct"))
                routes[(r.get("route") or "?") if answered else "abstained"] += 1
        for (src, obj), (n, answered, correct) in sorted(agg.items()):
            objects.append({"source": src, "object": obj, "n": n, "answered": answered, "correct": correct,
                            "accuracy": correct / n if n else 0.0,
                            "coverage": answered / n if n else 0.0,
                            "precision": correct / answered if answered else None})
    data = {"id": run_id, "header": run["header"], "dir": run["dir"], "rows": rows, "objects": objects,
            **{k: run[k] for k in ("dataset", "dataset_name", "split", "flags", "label", "questions", "where")},
            "routes": dict(sorted(routes.items(), key=lambda kv: -kv[1]))}
    _CACHE[str(sub)] = (mtime, data)
    return data


# ---------------------------------------------------------------- overview: one line per named test set

def _all_row(run: dict) -> dict | None:
    rows = json.loads((Path(run["dir"]) / "summary.json").read_text(encoding="utf-8"))
    return next((r for r in rows if r["source"] == "ALL" and r["type"] == "ALL"), None)


def _find_run(dataset: str, split: str) -> dict | None:
    """The plain run (no flags, every source) of a dataset and split; the newest if there are several."""
    cands = [r for r in list_runs() if r["dataset"] == dataset and r["split"] == split and not r["flags"]
             and r["where"] == "out"]
    return max(cands, key=lambda r: r["mtime"]) if cands else None


def _unseen_outcomes(path: Path) -> list[tuple[dict, str]]:
    spec = importlib.util.spec_from_file_location("unseen_report", REPO / "tools" / "unseen_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    return [(r, mod.outcome(r)) for r in rows]


def overview() -> dict:
    items = []
    for dataset, split in (("junction-dataset", "test"), ("junction-dataset-preview", "test")):
        run = _find_run(dataset, split)
        name = f"{DATASET_NAMES[dataset]}, {split} split"
        if run is None:
            items.append({"set": name, "missing": f"no plain eval run of {dataset} --split {split} found in out/"})
            continue
        a = _all_row(run)
        items.append({"set": name, "n": a["n"], "accuracy": a["accuracy"], "answered": a["answered"],
                      "precision": a["precision"], "proven": a.get("proven"), "proven_precision": a.get("proven_precision"),
                      "estimate": a.get("estimate"), "estimate_precision": a.get("estimate_precision"),
                      "source": f"out/{run['id']}/summary.json", "run": run["id"]})
    base = baseline()
    if base:
        items.append({"set": "Nemotron baseline sample (Stoa preview set, test split)", "n": base["n"],
                      "accuracy": base["ours_accuracy"], "baseline_accuracy": base["nemo_accuracy"],
                      "source": "out/" + Path(base["source"]).name, "note": base["ours_source"]})
    up = next((d / "unseen" / "results.jsonl" for d in result_dirs() if (d / "unseen" / "results.jsonl").exists()), None)
    if up:
        allrows = _unseen_outcomes(up)
        # distinct questions: a PDF that has a DXF twin repeats the DXF's questions (asked again with --pdf-only)
        twins = {r["path"] for r, _ in allrows if r.get("pdf_only")}
        rows = [(r, o) for r, o in allrows if not r.get("pdf_only") and r["path"] not in twins]
        c = defaultdict(int)
        for _, o in rows:
            c[o] += 1
        n = len(rows)
        answered = c["correct"] + c["wrong"]
        items.append({"set": "Unseen public drawings (not in Stoa's data)", "n": n,
                      "accuracy": c["correct"] / n if n else 0.0, "answered": answered, "correct": c["correct"],
                      "wrong": c["wrong"], "abstain": c["abstain"], "abstain_ok": c["abstain-ok"],
                      "proven_wrong": sum(1 for r, o in rows if o == "wrong" and r.get("tier") == "proven"),
                      "precision": c["correct"] / answered if answered else None,
                      "source": "out/unseen/results.jsonl"})
    return {"items": items}


def baseline() -> dict | None:
    """Nemotron with a plain prompt vs our router on the same questions. Our side is re-scored from the newest
    plain preview test run when that run holds every one of the baseline's questions (the router changed after
    the baseline was recorded); otherwise the router numbers stored in the baseline file are shown."""
    f = next((d / "baseline_nemotron.json" for d in result_dirs() if (d / "baseline_nemotron.json").exists()), None)
    if f is None:
        return None
    b = json.loads(f.read_text(encoding="utf-8"))
    tot = next(s for s in b["summary"] if s["source"] == "ALL")
    recs = b["records"]
    out = {"source": str(f), "n": tot["n"], "nemo_correct": tot["nemo_correct"], "nemo_accuracy": tot["nemo_accuracy"],
           "nemo_answered": tot["nemo_answered"], "ours_correct": tot["ours_correct"], "ours_accuracy": tot["ours_accuracy"],
           "ours_source": f"router as recorded in {f.name}",
           "cost_per_q": tot.get("frontier_cost_per_q_measured_tokens"),
           "cost_per_q_direct": tot.get("frontier_cost_per_q_direct_answer"),
           "latency_median_s": tot.get("nemo_latency_median_s"),
           "price_assumption": b.get("price_assumption_usd_per_mtok"),
           "sample": ", ".join(f"{src} {typ} {k}" for src, typ, k in b.get("plan", [])),
           "by_source": [{k: s[k] for k in ("source", "type", "n", "nemo_correct", "ours_correct")} for s in b["summary"]
                         if s["source"] != "ALL"]}
    run = _find_run("junction-dataset-preview", "test")
    if run and (Path(run["dir"]) / "answers.jsonl").exists():
        want = {r["qid"] for r in recs}
        got = {}
        with open(Path(run["dir"]) / "answers.jsonl", encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                if r["qid"] in want:
                    got[r["qid"]] = bool(r.get("correct"))
        if len(got) == len(want):
            ok = sum(got.values())
            out.update(ours_correct=ok, ours_accuracy=ok / len(want),
                       ours_source=f"current router, re-scored from out/{run['id']}/answers.jsonl")
            by = defaultdict(int)
            for r in recs:
                by[(r["source"], r["type"])] += got[r["qid"]]
            for s in out["by_source"]:
                s["ours_correct"] = by[(s["source"], s["type"])]
    return out
