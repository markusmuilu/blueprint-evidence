"""Write DEV-split CubiCasa gold counts per image (fixture and opening types) for MIL training.
Test-split answers are never written."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.data import data_root, load_questions, split_of
from bpqa.query import parse
OBJS = ["toilet", "sink", "shower_or_bath", "door", "window"]
d = {}
for q in load_questions(data_root()):
    if q.source != "cubicasa5k" or split_of(q.path) != "dev": continue
    p = parse(q.question)
    if p.obj in OBJS:
        d.setdefault(Path(q.path).stem, {})[p.obj] = int(q.answer)
out = Path(__file__).resolve().parents[1] / "out" / "fixtures" / "dev_counts.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(d, indent=0))
print(len(d), "dev images")
