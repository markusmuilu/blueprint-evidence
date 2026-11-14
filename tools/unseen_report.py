"""Score out/unseen/results.jsonl and print the per-drawing markdown tables used in UNSEEN.md.

    python tools/unseen_report.py out/unseen/results.jsonl > out/unseen/tables.md
"""
from __future__ import annotations

import json
import sys
from collections import OrderedDict, defaultdict


def tier(r):
    if r["ours"] is None or r["abstain"]:
        return "cannot prove"
    if r.get("tier"):                     # the answer's own tier (runs after tiers were added)
        return r["tier"]
    if str(r["route"]).startswith("raster"):
        return "estimate"
    return "proven" if (r["confidence"] or 0) >= 0.8 else "estimate"


def outcome(r):
    """correct | wrong | abstain (no answer, answer expected) | abstain-ok (no answer, none derivable)."""
    ref, ours = r["answer"], r["ours"]
    if r["expected"] == "abstain":
        return "abstain-ok" if ours is None else "wrong"
    if ours is None:
        return "abstain"
    try:
        o = float(ours)
    except (TypeError, ValueError):
        return "wrong"
    if r["type"] == "count":
        return "correct" if o == float(ref) else "wrong"
    return "correct" if abs(o - float(ref)) <= 0.02 * abs(float(ref)) else "wrong"


def short(q, n=88):
    q = q.replace("|", "/")
    return q if len(q) <= n else q[: n - 1] + "..."


def main():
    rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
    by = OrderedDict()
    for r in rows:
        by.setdefault(r["drawing"], []).append(r)
    tot = defaultdict(int)
    kinds = defaultdict(lambda: defaultdict(int))
    for d, rs in by.items():
        print(f"\n#### {d}\n")
        print("| # | question | reference | ours | result | route | tier (conf) | s | why not answered / note |")
        print("|---|---|---|---|---|---|---|---|---|")
        for k, r in enumerate(rs, 1):
            o = outcome(r)
            ref = "no answer derivable" if r["expected"] == "abstain" else f"{r['answer']} {r['unit']}".replace("count", "").strip()
            ours = "abstain" if r["ours"] is None else f"{r['ours']}"
            mode = " (pdf-only)" if r["pdf_only"] else ""
            note = (r["abstain"] or r["error"] or "")[:110].replace("|", "/")
            if r["ref_method"].startswith("manual"):
                ref += " (manual, check)"
            print(f"| {k} | {short(r['question'])}{mode} | {ref} | {ours} | {o} | {r['route']} | {tier(r)} ({r['confidence']}) | {r['seconds']} | {note} |")
            tot[o] += 1
            cls = ("ifc" if r["path"].endswith(".ifc") else "dxf" if r["path"].endswith(".dxf") else
                   "pdf" if r["path"].endswith(".pdf") else "image")
            if r["pdf_only"]:
                cls = "pdf-only"
            kinds[cls][o] += 1
    print("\n#### Totals\n")
    print("| class | questions | correct | wrong | abstain | abstain-ok |")
    print("|---|---|---|---|---|---|")
    for cls, c in kinds.items():
        print(f"| {cls} | {sum(c.values())} | {c['correct']} | {c['wrong']} | {c['abstain']} | {c['abstain-ok']} |")
    print(f"| **all** | {sum(tot.values())} | {tot['correct']} | {tot['wrong']} | {tot['abstain']} | {tot['abstain-ok']} |")
    answered = tot["correct"] + tot["wrong"]
    print(f"\nanswered {answered}, correct {tot['correct']}, precision {tot['correct']}/{answered}")


if __name__ == "__main__":
    main()
