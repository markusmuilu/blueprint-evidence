"""Dev-only: precision/coverage of label counting under abstention policies."""
import sys, collections, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.data import data_root, load_questions, split_of
from bpqa.query import parse
from bpqa.routes.labels import find_rooms, dedupe, is_word
from bpqa.routes.ocr import ocr_image
root = data_root()
split = sys.argv[1] if len(sys.argv) > 1 else "dev"
assert split == "dev", "policy tuning is dev-only"
rows = []
for q in load_questions(root):
    if q.source != "cubicasa5k" or split_of(q.path) != split: continue
    p = parse(q.question)
    if p.lang != "en" or p.obj not in ("bedroom", "kitchen", "sauna", "wc_room", "bathroom", "balcony"): continue
    toks = ocr_image(root / q.path, allow_run=False, partial=True)
    if not toks: continue
    hits = find_rooms(toks, p.obj)
    words = [t for t in dedupe(toks) if is_word(t["text"])]
    rows.append(dict(obj=p.obj, gold=q.answer, n=len(hits), words=len(words),
                     weak=sum(len(h["part"]) == 1 for h in hits), name=q.path.split("/")[-1]))
def report(name, keep):
    print("==", name)
    for o in sorted({r["obj"] for r in rows}):
        rs = [r for r in rows if r["obj"] == o]
        ans = [r for r in rs if keep(r)]
        ok = sum(r["gold"] == r["n"] for r in ans)
        print(f"  {o:9s} n={len(rs):3d} answered={len(ans):3d} ({len(ans)/len(rs):4.0%}) correct={ok:3d} prec={ok/max(1,len(ans)):4.0%} acc={ok/len(rs):4.0%}")
report("all", lambda r: True)
for w in (3, 5, 8):
    report(f"zero needs >= {w} words", lambda r, w=w: r["n"] > 0 or r["words"] >= w)
report("zero needs >=5 words, no single-letter hits", lambda r: (r["n"] > 0 or r["words"] >= 5) and r["weak"] == 0)
report("nonzero only", lambda r: r["n"] > 0)
print("== precision by predicted value (pred: correct/answered)")
for o in sorted({r["obj"] for r in rows}):
    rs = [r for r in rows if r["obj"] == o]
    d = collections.defaultdict(lambda: [0, 0])
    for r in rs:
        k = (r["n"], "w" if r["weak"] else "", "rich" if r["words"] >= 5 else "poor")
        d[k][1] += 1; d[k][0] += r["gold"] == r["n"]
    print(f"  {o:9s}", "  ".join(f"{k[0]}{k[1]}{'' if k[0] else '/' + k[2]}:{v[0]}/{v[1]}" for k, v in sorted(d.items())))
