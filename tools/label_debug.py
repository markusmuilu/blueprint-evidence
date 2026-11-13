"""Dev-only: for mismatching images, print gold, hits and all short OCR tokens."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.data import data_root, load_questions, split_of
from bpqa.query import parse
from bpqa.routes.labels import find_rooms, dedupe
from bpqa.routes.ocr import ocr_image
root = data_root()
obj = sys.argv[1]
for q in load_questions(root):
    if q.source != "cubicasa5k" or split_of(q.path) != "dev": continue
    p = parse(q.question)
    if p.lang != "en" or p.obj != obj: continue
    toks = ocr_image(root / q.path, allow_run=False, partial=True)
    if toks is None: continue
    hits = find_rooms(toks, obj)
    if len(hits) != q.answer:
        short = sorted({t["text"] for t in dedupe(toks) if len(t["text"]) <= 14 and t["conf"] > 0.4})
        print(q.path.split("/")[-1], "gold", q.answer, "got", [h["text"] for h in hits])
        print("    ", short)
