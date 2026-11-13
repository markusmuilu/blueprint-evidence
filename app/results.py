"""Read eval.py outputs (out/<run>/summary.json, summary.txt, answers.jsonl) for the Results view.

Looks in this repo's out/ first, then in BPE_RESULTS, then in the sibling main checkout
(../stoa/out), because eval runs usually happen there. Read only.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


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


def _order(name: str) -> tuple:
    base = name.split("-")[0]
    return ({"test": 0, "dev": 1}.get(base, 2), len(name), name)


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
                             "mtime": (sub / "summary.json").stat().st_mtime})
    runs.sort(key=lambda r: _order(r["id"]))
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
            "routes": dict(sorted(routes.items(), key=lambda kv: -kv[1]))}
    _CACHE[str(sub)] = (mtime, data)
    return data
