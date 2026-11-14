"""Debug helper for text answers: which gold numbers / tokens are missing from our answer.

    python tools/text_miss.py out/<run>/answers.jsonl [--route ifc-props]
"""
import json
import re
import sys

path = sys.argv[1]
route = sys.argv[3] if len(sys.argv) > 3 and sys.argv[2] == "--route" else None
for line in open(path, encoding="utf-8"):
    r = json.loads(line)
    if r["type"] != "text" or r["correct"] is not False or (route and r["route"] != route):
        continue
    gs, ps = str(r["gold"]).lower(), str(r["value"]).lower()
    g = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", gs.replace(",", ""))]
    p = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", ps.replace(",", ""))]
    miss = [x for x in g if not any(abs(y - x) <= 0.02 * max(abs(x), 1e-9) for y in p)]
    print(f"Q: {r['question'][:120]}\n   missing {len(miss)}/{len(g)}: {miss[:15]}  | {r['explanation'][:90]}")
