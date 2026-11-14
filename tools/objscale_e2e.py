"""End to end: room-type area takeoffs on scans whose printed areas are hidden, before and after the object scale.

Stoa asks no area question on a scan, so the questions are made from the plans themselves: on every plan of the
chosen half (tools/objscale_eval.py) that prints room areas, one takeoff question per room type (bedrooms,
bathrooms, WC rooms, saunas, kitchens) whose rooms ALL print an area; the gold answer is the sum of those printed
areas. The printed areas are then hidden from the route (their OCR tokens are erased from the linework but not
read), so the plan looks like one that prints no areas. Each question is answered by raster_area.takeoff_answer
with the object scale off (before) and on (after), and scored with eval.py's rule (within 2% of the gold).

    python tools/objscale_e2e.py --data <root> --regions out/objscale/regions --half val [--out rows.jsonl]
"""
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np

from bpqa import scale_objects as OS
from bpqa import takeoff
from bpqa.data import data_root
from bpqa.query import parse
from bpqa.routes import raster_area as RA

sys.path.insert(0, str(Path(__file__).resolve().parent))
from objscale_eval import half  # noqa: E402

QUESTIONS = {
    "bedroom": "How many square meters of flooring do I need for the bedrooms?",
    "bathroom": "How many square meters of flooring do I need for the bathrooms?",
    "wc": "How many square meters of flooring do I need for the toilets?",
    "sauna": "How many square meters of flooring do I need for the sauna?",
    "kitchen": "How many square meters of flooring do I need for the kitchen?",
}


def hidden_plan_tokens(f, allow_run=True, min_conf=0.5):
    """The upright pass with every printed area made unreadable (kept as an empty token, so its ink is still erased
    from the linework); a token "MH1 15,3 m2" keeps its label."""
    t = RA.tokens_r0(Path(f), allow_run=False) or []
    out = []
    for x in t:
        if x["conf"] < min_conf:
            continue
        pa = RA.parse_area(x["text"])
        if pa is not None:
            x = {**x, "text": pa[1] if RA.room_word(pa[1]) else ""}
        out.append(x)
    return RA.UPRIGHT, out, []


def questions(f: Path):
    toks = [t for t in (RA.tokens_r0(f, allow_run=False) or []) if t["conf"] >= 0.5]
    rooms, free = RA.read_rooms(toks)
    labelled = [(r.label, r.printed) for r in rooms if r.label] + [(l["text"], None) for l in free]
    out = []
    for t, qtext in QUESTIONS.items():
        rs = [(lab, v) for lab, v in labelled if t in takeoff.room_types(lab)]
        if rs and all(v is not None for _, v in rs):
            out.append({"type": t, "question": qtext, "gold": round(sum(v for _, v in rs), 2), "rooms": rs})
    return out


def ask(f: Path, qtext: str):
    q = parse(qtext)
    ctx = SimpleNamespace(file=f, q=SimpleNamespace(path=f"data/cubicasa5k/{f.name}", question=qtext), evidence=False,
                          out_dir=Path("out/objscale/ev"))
    return RA.takeoff_answer(ctx, q, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    ap.add_argument("--regions", default="out/objscale/regions")
    ap.add_argument("--half", default="val", choices=["all", "tune", "val"])
    ap.add_argument("--out")
    a = ap.parse_args()
    root = data_root(a.data)
    files = [root / "data" / "cubicasa5k" / json.loads(p.read_text(encoding="utf-8"))["file"]
             for p in sorted(Path(a.regions).glob("*.json"))]
    files = [f for f in files if a.half == "all" or half(f.name) == a.half]
    RA.plan_tokens = hidden_plan_tokens
    rows = []
    for f in files:
        qs = questions(f)
        if not qs:
            continue
        for on in (False, True):
            OS.ENABLED = on
            RA.analyse.cache_clear()
            for q in qs:
                ans = ask(f, q["question"])
                key = "after" if on else "before"
                q[key] = {"value": ans.value if ans.answered else None, "tier": ans.tier if ans.answered else None,
                          "why": None if ans.answered else ans.abstain}
                if ans.answered:
                    q[key]["err"] = round((ans.value - q["gold"]) / q["gold"], 4)
                    q[key]["object_scale"] = "OBJECT scale" in (ans.explanation or "")
        for q in qs:
            rows.append({"file": f.name, **q})
    OS.ENABLED = False

    def tally(key):
        ans = [r for r in rows if r[key]["value"] is not None]
        right = [r for r in ans if abs(r[key]["err"]) <= 0.02]
        return {"answered": len(ans), "right": len(right), "wrong": len(ans) - len(right),
                "abstain": len(rows) - len(ans), "estimates": sum(r[key]["tier"] == "estimate" for r in ans)}
    print(f"plans {len({r['file'] for r in rows})}, questions {len(rows)}")
    print("before", tally("before"))
    print("after ", tally("after"))
    new = [r for r in rows if r["before"]["value"] is None and r["after"]["value"] is not None]
    if new:
        e = np.abs([r["after"]["err"] for r in new])
        print(f"newly answered {len(new)}: right (2%) {int((e <= .02).sum())}, wrong {int((e > .02).sum())}; "
              f"within 5% {int((e <= .05).sum())}, within 10% {int((e <= .10).sum())}, within 20% {int((e <= .20).sum())}; "
              f"median error {np.median(e):.1%}; all tier estimate: {all(r['after']['tier'] == 'estimate' for r in new)}")
        for t in QUESTIONS:
            et = np.abs([r["after"]["err"] for r in new if r["type"] == t])
            if len(et):
                print(f"  {t:9s} n={len(et)} right={int((et <= .02).sum())} median={np.median(et):.1%}")
    changed = [r for r in rows if r["before"]["value"] is not None and r["before"]["value"] != r["after"]["value"]]
    print(f"answers that existed before and changed: {len(changed)}")
    if a.out:
        Path(a.out).write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


if __name__ == "__main__":
    main()
