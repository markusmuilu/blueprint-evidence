"""Print DEV gold answers (English questions) for an image stem. Refuses test-split images."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.data import data_root, load_questions, split_of
from bpqa.query import parse
qs = load_questions(data_root())
for stem in sys.argv[1:]:
    path = f"data/cubicasa5k/{stem}.png"
    if split_of(path) != "dev":
        print(stem, "TEST SPLIT, refused"); continue
    print(stem, {parse(q.question).obj: q.answer for q in qs if q.path == path and parse(q.question).lang == "en"})
