"""Room-type takeoff sums on vector drawings, compared with Stoa's own area answers where comparable.

Stoa's questions do not ask "area of all bathrooms", so the reference is built from questions it
does ask, and only where the comparison is like for like:

  swiss-label  "total net area of all rooms labelled 'Schlafzimmer'" (also 'Chambre a coucher',
               'Camera da letto') vs. our takeoff "total floor area of the bedrooms" on that floor
  swiss-rooms  per-room answers ("net floor area of room 7.05 ('Bad')") summed over the rooms our
               classifier types as bathroom / kitchen; counted only when Stoa asks about every
               such room on the floor (same room numbers), so a missing question cannot pass as an error
  ifc-rooms    per-room answers on IFC models ("net floor area of the room 'Bathroom 1 (A104)'"),
               summed per model and type, same completeness rule (matched by space label)

    python tools/takeoff_eval.py --data <root> --split dev [--max-floors 300] [--pdf-only]

The takeoff answer is produced by the normal router from a takeoff question, so the parser, the
room-type classifier and the route are all exercised. Within 2% counts as correct (Stoa's rule).
"""
import argparse
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.data import Question, data_root, load_questions, split_of
from bpqa.router import Context, solve
from bpqa.takeoff import room_types

TYPE_Q = {"bedroom": "What is the total floor area of the bedrooms in m2?",
          "bathroom": "How many m2 of tiling do I need for the bathrooms?",
          "kitchen": "How many square metres of flooring for the kitchen?",
          "wc": "How many square meters of tiling should I order for the toilets?"}
BEDROOM_LABELS = {"Schlafzimmer", "Chambre à coucher", "Camera da letto"}


def ask(root, path, text, pdf_only):
    q = Question(qid=-1, path=path, question=text)
    ctx = Context(root=root, q=q, out_dir=Path("out/evidence"), evidence=False, checks=False, pdf_only=pdf_only)
    _, a = solve(ctx)
    return a


def ok(v, g):
    return v is not None and g and abs(v - g) <= 0.02 * abs(g)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--max-floors", type=int, default=300)
    ap.add_argument("--pdf-only", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()
    root = data_root(args.data)
    qs = [q for q in load_questions(root) if q.type == "area" and split_of(q.path) == args.split]
    rows = []

    # swiss-label
    sw_label = [q for q in qs if q.source == "swiss_dwellings" and q.path.endswith(".pdf")
                and (m := re.search(r"labelled '(.+?)'", q.question)) and m.group(1) in BEDROOM_LABELS]
    random.Random(0).shuffle(sw_label)
    for q in sw_label[:args.max_floors]:
        a = ask(root, q.path, TYPE_Q["bedroom"], args.pdf_only)
        rows.append({"set": "swiss-label", "type": "bedroom", "path": q.path, "gold": q.answer, "value": a.value,
                     "route": a.route, "abstain": a.abstain})

    # per-room references
    per = defaultdict(dict)       # (path or model, type) -> {room key: gold}
    for q in qs:
        if q.source == "swiss_dwellings" and q.path.endswith(".pdf"):
            m = re.search(r"of room (\S+) \('(.+?)'\)", q.question)
            if m and q.question.startswith("What is"):
                for t in room_types(m.group(2)) & {"bathroom", "kitchen"}:
                    per[(q.path, t)][m.group(1)] = q.answer
        elif q.source == "ifc_models" and not re.search(
                r"total|samlede|totale|gesamte|wall|wand|v.gge|vaegge|sidea|zijopp|seiten", q.question, re.I):
            m = re.search(r"'(.+?)'|«(.+?)»|“(.+?)”", q.question)
            if m:
                m = type("M", (), {"group": lambda self, i, g=next(x for x in m.groups() if x): g})()
                for t in room_types(re.sub(r"\s*\(.*\)$", "", m.group(1))) & {"bathroom", "wc", "kitchen", "bedroom"}:
                    per[(q.path, t)][m.group(1)] = q.answer
    keys = sorted(per)
    random.Random(1).shuffle(keys)
    n_sw = 0
    for path, t in keys:
        is_sw = "swiss" in path
        if is_sw:
            n_sw += 1
            if n_sw > args.max_floors:
                continue
        a = ask(root, path, TYPE_Q[t], args.pdf_only)
        gold_rooms = per[(path, t)]
        got = {}
        for it in a.items or []:
            k = it.get("number") if is_sw else (f"{it['room']}" if "(" in it["room"] else f"{it['room']} ({it.get('number')})")
            got[k] = it.get("area_m2")
        complete = a.answered and set(got) == set(gold_rooms)
        room_hits = sum(1 for k, g in gold_rooms.items() if k in got and ok(got[k], g))
        room_n = sum(1 for k in gold_rooms if k in got)
        rows.append({"set": "swiss-rooms" if is_sw else "ifc-rooms", "type": t, "path": path,
                     "gold": round(sum(gold_rooms.values()), 2), "value": a.value, "route": a.route,
                     "abstain": a.abstain, "complete": complete, "n_gold": len(gold_rooms), "n_ours": len(got),
                     "room_hits": room_hits, "room_n": room_n, "missing": sorted(set(gold_rooms) - set(got))[:5], "extra": sorted(set(got) - set(gold_rooms))[:5]})

    print(f"split {args.split}{' pdf-only' if args.pdf_only else ''}")
    for s in ("swiss-label", "swiss-rooms", "ifc-rooms"):
        for t in sorted({r["type"] for r in rows if r["set"] == s}):
            rs = [r for r in rows if r["set"] == s and r["type"] == t]
            if s != "swiss-label":
                comp = [r for r in rs if r["complete"]]
                print(f"  {s:12s} {t:9s} n={len(rs):4d} room sets match: {len(comp):4d}  within 2% of those: "
                      f"{sum(ok(r['value'], r['gold']) for r in comp):4d}  answered: {sum(r['value'] is not None for r in rs)}"
                      f"  per-room within 2%: {sum(r['room_hits'] for r in rs)}/{sum(r['room_n'] for r in rs)}"
                      f" (of {sum(r['n_gold'] for r in rs)} asked)")
            else:
                ans = [r for r in rs if r["value"] is not None]
                print(f"  {s:12s} {t:9s} n={len(rs):4d} answered: {len(ans):4d}  within 2%: "
                      f"{sum(ok(r['value'], r['gold']) for r in rs):4d}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")


if __name__ == "__main__":
    main()
