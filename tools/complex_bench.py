"""Judge-style complex questions: is the question understood, and is it answered?

    python tools/complex_bench.py                 # understood share, per kind (no drawings read, no model calls)
    python tools/complex_bench.py --e2e           # also solve each on its drawing: answered / cannot, per kind
    python tools/complex_bench.py --show-misses

tests/data/complex_questions.jsonl holds 60 questions over IFC models and sheets, Swiss PDF / DXF, CubiCasa scans and
the Stoa sample: superlatives, conditions, units, shares, storeys named in words, lists, per apartment, several parts.
60 are 'tune' (written while building bpqa/compose.py); 32 are 'heldout' (written by a separate model run that saw no
parser code, expected shapes fixed before the first run, never tuned on).
Each has the expected shape:

  kind simple         one measurable question: measure, object, room types, and what must be carried with it
                      (storey: the storey word read, e.g. top / ground; unit_out: sqft)
  kind list, extreme, condition, share, per_apartment, parts, which_storey
                      the composite shape bpqa/compose.py plans, with its key fields (types, max, which, threshold,
                      count, the object of each part)

'Understood' = the reading has that shape. A build without bpqa/compose.py (before 14 Nov) has no composite readings,
so only the simple cases can count there. Rules only: the language model is not asked (use_llm=False).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
DATA = REPO / "tests" / "data" / "complex_questions.jsonl"
DATASET = Path(r"C:\Users\Markus\Coding\junction-2026\data\stoa-full\junction-dataset")
SAMPLE_ROOT = Path(r"C:\Users\Markus\Coding\junction-2026")


def _types(q) -> list:
    tk = q.extra.get("takeoff") or {}
    return sorted(tk.get("types") or [])


def understood(case: dict) -> tuple[bool, str]:
    from bpqa import phrasing
    try:
        from bpqa import compose
    except ImportError:
        compose = None
    exp, text = case["expected"], case["text"]
    p = compose.plan(text) if compose else None
    if exp["kind"] == "simple":
        if p is not None:
            return False, f"planned as {p['kind']}"
        q = phrasing.read(text, use_llm=False)
        got = {"measure": q.measure, "obj": q.obj}
        if "variant" in exp:
            got["variant"] = q.variant
        if "types" in exp:
            got["types"] = _types(q)
        if "unit_out" in exp:
            got["unit_out"] = q.extra.get("unit_out")
        if "storey" in exp:
            got["storey"] = (q.extra.get("storey_ref") or {}).get("kind")
        want = {k: (sorted(v) if k == "types" else v) for k, v in exp.items() if k != "kind"}
        return got == want, json.dumps(got)
    if p is None:
        return False, "no composite reading"
    if p["kind"] != exp["kind"]:
        return False, f"planned as {p['kind']}"
    for k, v in exp.items():
        if k in ("kind", "parts"):
            continue
        g = p.get(k)
        if k == "types":
            g, v = sorted(g or []), sorted(v)
        if k == "threshold":
            if g is None or abs(float(g) - float(v)) > 0.01 * max(1.0, float(v)):
                return False, f"threshold {g}"
            continue
        if g != v:
            return False, f"{k}={g!r}"
    if exp["kind"] == "parts":
        from bpqa.query import RuleParser
        parts = p["parts"]
        if len(parts) != len(exp["parts"]):
            return False, f"{len(parts)} parts: {parts}"
        for sub, want in zip(parts, exp["parts"]):
            q = phrasing.read(sub, use_llm=False)
            got = [q.measure, q.obj] + ([_types(q)] if len(want) > 2 else [])
            if got != want:
                return False, f"part {sub!r} read as {got}"
    return True, p["kind"]


def solve(case: dict):
    from bpqa.data import Question
    from bpqa.router import Context, solve as s
    path = case["path"]
    root = DATASET
    if path.startswith("sample:"):
        root, path = SAMPLE_ROOT, path[len("sample:"):]
    ctx = Context(root=root, q=Question(qid=-1, path=path, question=case["text"]), out_dir=REPO / "out" / "evidence",
                  evidence=False, nemotron=False)
    t = time.time()
    q, a = s(ctx)
    return a, time.time() - t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--e2e", action="store_true")
    ap.add_argument("--show-misses", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--split", default="both", choices=["tune", "heldout", "both"])
    a = ap.parse_args()
    cases = [json.loads(l) for l in DATA.read_text(encoding="utf-8").splitlines() if l.strip()]
    cases = [c for c in cases if a.split in ("both", c.get("split", "tune"))]
    by = defaultdict(lambda: [0, 0, 0])
    rows = []
    for c in cases:
        ok, why = understood(c)
        r = {"id": c["id"], "kind": c["expected"]["kind"], "text": c["text"], "understood": ok, "why": why}
        if a.e2e:
            try:
                ans, dt = solve(c)
                r.update(answered=ans.answered, value=str(ans.value)[:160], route=ans.route, tier=ans.tier,
                         abstain=(ans.abstain or "")[:200], t=round(dt, 2))
            except Exception as ex:
                r.update(answered=False, abstain=f"error {type(ex).__name__}: {ex}")
        rows.append(r)
        b = by[r["kind"]]
        b[0] += 1
        b[1] += ok
        b[2] += bool(r.get("answered"))
        if a.show_misses and not ok:
            print(f"  MISS {c['id']} {c['text']!r}: {why}")
    n = len(rows)
    print(f"== complex questions n={n}  understood {sum(r['understood'] for r in rows) / n:.1%}"
          + (f"  answered {sum(bool(r.get('answered')) for r in rows) / n:.1%}" if a.e2e else ""))
    for k, (m, u, ans) in sorted(by.items()):
        print(f"  {k:14s} n={m:2d}  understood {u / m:6.1%}" + (f"  answered {ans / m:6.1%}" if a.e2e else ""))
    if a.out:
        Path(a.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
