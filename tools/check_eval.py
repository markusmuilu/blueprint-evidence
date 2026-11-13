"""Dev-only: what each consistency check would do to the raster answers.

Reads an existing answers.jsonl (default out/dev/answers.jsonl, produced without the checking layer
via eval.py --no-checks), runs every check on each answered raster question and reports, per check:
how often it fires, and the precision of the answers it passes and fails. Also reports, for each
check, how often the gold answer itself satisfies it (a check the gold often fails is a bad check).

    python tools/check_eval.py [out/dev/answers.jsonl]
"""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa import checks
from bpqa.data import data_root

root = data_root()
src = Path(sys.argv[1] if len(sys.argv) > 1 else "out/dev/answers.jsonl")
rows = [json.loads(l) for l in open(src, encoding="utf-8")]
assert all(r["split"] == "dev" for r in rows), "check tuning is dev-only"
rows = [r for r in rows if r["path"].startswith("data/cubicasa5k/") and r["query"].get("lang") == "en"]

stat = collections.defaultdict(lambda: {"pass": [0, 0], "fail": [0, 0], "gold_ok": [0, 0]})
base = collections.defaultdict(lambda: [0, 0, 0])   # obj -> n, answered, correct
for r in rows:
    obj = r["query"].get("obj")
    b = base[(r["route"] if r["value"] is not None else "-", obj)]
    b[0] += 1
    f = root / r["path"]
    for c in checks.run(f, obj, r["gold"]):           # does the gold answer satisfy the check?
        s = stat[(c.name, obj)]
        s["gold_ok"][0] += c.status == "pass"
        s["gold_ok"][1] += 1
    if r["value"] is None:
        continue
    b[1] += 1
    b[2] += bool(r["correct"])
    for c in checks.run(f, obj, r["value"]):
        s = stat[(c.name, obj)][c.status]
        s[0] += bool(r["correct"])
        s[1] += 1

print("answered raster questions (EN), route x object: n answered correct")
for k, (n, a, c) in sorted(base.items(), key=str):
    if a:
        print(f"  {k[0]:20s} {k[1]:15s} {a:4d} {c:4d}  prec {c / a:.0%}")
print("\ncheck x object: answers passing (correct/n), failing (correct/n), gold satisfies check")
for (name, obj), s in sorted(stat.items()):
    p, fl, g = s["pass"], s["fail"], s["gold_ok"]
    print(f"  {name:26s} {obj:15s} pass {p[0]:3d}/{p[1]:<3d}  fail {fl[0]:3d}/{fl[1]:<3d}  gold ok {g[0]:3d}/{g[1]:<3d}"
          f" ({g[0] / max(1, g[1]):.0%})")
