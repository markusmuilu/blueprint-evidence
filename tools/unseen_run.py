"""Run ask.py on every question of the unseen set and record what came back.

    python tools/unseen_run.py --data ../data/unseen --out out/unseen [--only-ext png,jpg]

One ask.py subprocess per question (exactly what a user would run). Results go to
<out>/results.jsonl, one line per question and mode (default, or --pdf-only for PDFs that have a DXF twin).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default=str(REPO / "out" / "unseen"))
    ap.add_argument("--only-ext", default="")
    ap.add_argument("--skip-ext", default="")
    ap.add_argument("--results", default="results.jsonl")
    ap.add_argument("--questions", default="questions.jsonl")
    args = ap.parse_args()
    data = Path(args.data)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    only = set(filter(None, args.only_ext.split(",")))
    skip = set(filter(None, args.skip_ext.split(",")))
    qs = [json.loads(line) for line in open(data / args.questions, encoding="utf-8")]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    done = set()
    if (out / args.results).exists():
        for line in open(out / args.results, encoding="utf-8"):
            r = json.loads(line)
            done.add((r["path"], r["question"], r["pdf_only"]))
    with open(out / args.results, "a", encoding="utf-8") as fh:
        for i, q in enumerate(qs):
            ext = q["path"].rsplit(".", 1)[-1].lower()
            if (only and ext not in only) or ext in skip:
                continue
            modes = [False]
            if ext == "pdf" and (data / q["path"]).with_suffix(".dxf").exists():
                modes.append(True)
            for pdf_only in modes:
                if (q["path"], q["question"], pdf_only) in done:
                    continue
                cmd = [sys.executable, str(REPO / "ask.py"), q["path"], q["question"], "--data", str(data),
                       "--out", str(out / "evidence")]
                if pdf_only:
                    cmd.append("--pdf-only")
                t0 = time.time()
                try:
                    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=600, env=env, cwd=REPO)
                    dt = time.time() - t0
                    try:
                        res = json.loads(p.stdout)
                        err = None
                    except Exception:
                        res, err = {}, (p.stderr or p.stdout)[-600:]
                except subprocess.TimeoutExpired:
                    dt, res, err = time.time() - t0, {}, "timeout 600 s"
                rec = {"i": i, "pdf_only": pdf_only, "seconds": round(dt, 1), **{k: q[k] for k in
                       ("path", "drawing", "question", "answer", "unit", "type", "ref_method", "manual_check", "expected")},
                       "ours": res.get("value"), "route": res.get("route"), "confidence": res.get("confidence"),
                       "tier": res.get("tier"), "tier_reason": res.get("tier_reason"),
                       "abstain": res.get("abstain"), "evidence": res.get("evidence"), "explanation": res.get("explanation"),
                       "checks": res.get("checks"), "query": res.get("query"), "error": err}
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                print(i, pdf_only, q["drawing"], "->", rec["ours"], rec["route"], (rec["abstain"] or err or "")[:70], f"{dt:.1f}s", flush=True)


if __name__ == "__main__":
    main()
