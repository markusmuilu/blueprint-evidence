"""Measure the Nemotron question-parse fallback (bpqa/routes/nemotron.py NemotronParser).

    python tools/nemo_parse_eval.py

Three checks, all on the preview data, results in out/nemo_parse_eval.json:
  A. agreement with the rule parser on 80 seeded dev questions the rules DO parse (all languages): the
     rule parser is the reference, so this measures how faithfully the fallback maps a question;
  B. 14 hand-written phrasings and languages the rules do not cover, with the typed query a person would
     write; the rule parser is run on them too;
  C. the preview questions the rule parser leaves unparsed (130, free-form IFC-Bench text): how many the
     fallback maps to a valid query at all.
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bpqa.data import data_root, load_questions, split_of
from bpqa.query import parse
from bpqa.routes.nemotron import NemotronParser

HAND = [  # (question, measure, obj, extras that must match)
    ("What is the total number of glazed openings in this plan?", "count", "window", {}),
    ("Tell me how many loos are drawn on this sheet.", "count", "toilet", {}),
    ("Cuantas puertas hay en este plano?", "count", "door", {}),
    ("Hur manga fonster finns det pa ritningen?", "count", "window", {}),
    ("Hoeveel ramen staan er op deze plattegrond?", "count", "window", {}),
    ("How big is the room 'Kueche' on this floor, in square metres?", "area", "room", {"label": "Kueche"}),
    ("What is the surface of apartment B in m2?", "area", "apartment", {"apartment": "B"}),
    ("Add up the floor area of every room on level 'EG'.", "area", "room", {"level": "EG", "scope": "level"}),
    ("Quantos quartos tem este apartamento?", "count", "bedroom", {}),
    ("Wie oft ist eine Badewanne eingezeichnet?", "count", "bathtub", {}),
    ("Count the staircases shown.", "count", "stair", {}),
    ("Combien de colonnes dans le modele 'Haus A'?", "count", "column", {"scope": "model"}),
    ("How long are all the walls together, in metres?", "length", "wall", {}),
    ("Montako parveketta kuvassa nakyy?", "count", "balcony", {}),
]


def same(q, d, extras) -> bool:
    if q.measure != d[0] or q.obj != d[1]:
        return False
    return all(getattr(q, k) == v for k, v in extras.items())


def main():
    root = data_root()
    qs = load_questions(root)
    rng = random.Random(7)
    dev_ok = [q for q in qs if split_of(q.path) == "dev" and parse(q.question).ok()]
    dev_ok.sort(key=lambda q: q.qid)
    a_sample = rng.sample(dev_ok, 80)
    unparsed = [q for q in qs if not parse(q.question).ok()]
    P = NemotronParser()
    out = {}
    with ThreadPoolExecutor(3) as ex:
        a_res = list(ex.map(lambda q: P.parse(q.question), a_sample))
        b_res = list(ex.map(lambda h: P.parse(h[0]), HAND))
        c_res = list(ex.map(lambda q: P.parse(q.question), unparsed))

    agree = 0
    parsed = 0
    rows = []
    for q, r in zip(a_sample, a_res):
        ref = parse(q.question)
        ok = r.ok()
        parsed += ok
        match = ok and (r.measure, r.obj, r.scope, r.label, r.room_no, r.apartment) == \
            (ref.measure, ref.obj, ref.scope, ref.label, ref.room_no, ref.apartment)
        agree += match
        rows.append({"question": q.question, "rules": ref.as_dict(), "nemotron": r.as_dict() if ok else None, "match": match})
    out["A"] = {"n": len(a_sample), "mapped": parsed, "agree_with_rules": agree, "rows": rows}
    hb = []
    for h, r in zip(HAND, b_res):
        rules = parse(h[0])
        hb.append({"question": h[0], "expected": [h[1], h[2], h[3]], "rules_ok": rules.ok(),
                   "rules_correct": rules.ok() and same(rules, h[1:3], h[3]),
                   "nemotron": r.as_dict() if r.ok() else None, "nemotron_correct": r.ok() and same(r, h[1:3], h[3])})
    out["B"] = {"n": len(HAND), "rules_parsed": sum(x["rules_ok"] for x in hb), "rules_correct": sum(x["rules_correct"] for x in hb),
                "nemotron_mapped": sum(x["nemotron"] is not None for x in hb),
                "nemotron_correct": sum(x["nemotron_correct"] for x in hb), "rows": hb}
    cm = Counter((r.measure, r.obj) for r in c_res if r.ok())
    out["C"] = {"n": len(unparsed), "mapped": sum(r.ok() for r in c_res), "top": cm.most_common(8)}
    (REPO / "out").mkdir(exist_ok=True)
    (REPO / "out" / "nemo_parse_eval.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print("A", {k: v for k, v in out["A"].items() if k != "rows"})
    print("B", {k: v for k, v in out["B"].items() if k != "rows"})
    for x in hb:
        print("  ", x["nemotron_correct"], x["rules_ok"], x["question"], "->", x["nemotron"] and (x["nemotron"].get("measure"), x["nemotron"].get("obj")))
    print("C", out["C"])


if __name__ == "__main__":
    main()
