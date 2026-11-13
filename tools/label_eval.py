"""Dev-only analysis: label-count vs gold per room type."""
import json, sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.data import data_root, load_questions, split_of
from bpqa.query import parse
from bpqa.routes.labels import find_rooms
from bpqa.routes.ocr import ocr_image
root = data_root()
objs = sys.argv[1].split(",") if len(sys.argv) > 1 else ["bedroom", "kitchen", "sauna", "wc_room", "bathroom", "balcony"]
verbose = "-v" in sys.argv
qs = [q for q in load_questions(root) if q.source == "cubicasa5k" and split_of(q.path) == "dev"]
res = collections.defaultdict(list)
for q in qs:
    p = parse(q.question)
    if p.lang != "en" or p.obj not in objs: continue
    f = root / q.path
    toks = ocr_image(f, allow_run=False, partial=True)
    if not toks: continue
    if toks is None: continue
    hits = find_rooms(toks, p.obj)
    res[p.obj].append((q.path.split("/")[-1], q.answer, len(hits), [h["text"] for h in hits]))
for o, rows in res.items():
    ok = sum(g == n for _, g, n, _ in rows)
    print(f"{o:10s} n={len(rows)} exact={ok} ({ok/len(rows):.0%})  over={sum(n>g for _,g,n,_ in rows)} under={sum(n<g for _,g,n,_ in rows)}")
    if verbose:
        for r in rows:
            if r[1] != r[2]: print("   ", r)
