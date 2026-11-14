"""'Tiling for the toilets' on a floor with no separate WC room: python tests/test_toilet_fallback.py

Synthetic rooms (shapely boxes) and toilet symbols; no dataset needed. With STOA_DATA set, one Swiss
floor from the preview set is also asked end to end (skipped when the file is not there).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shapely.geometry import Point, box

from bpqa.query import parse
from bpqa.takeoff import describe, fallback_reason, interpretation, summary, toilet_fallback

Q = "How many square meters of tiling should I order for the toilets?"


def room(name, x0, y0, x1, y1, number=None):
    return {"name": name, "number": number, "poly": box(x0, y0, x1, y1)}


def toilet_test(symbols, others=()):
    """has_toilet over symbol boxes, the same rule the routes use: True when a toilet's centre is inside and
    the room is bigger than the symbol, False when only other fixtures are inside, None when nothing is."""
    syms = [(Point((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), (b[2] - b[0]) * (b[3] - b[1])) for b in symbols]
    pts = [Point(xy) for xy in others]

    def test(r):
        if any(r["poly"].area > a and r["poly"].contains(c) for c, a in syms):
            return True
        return False if any(r["poly"].contains(c) for c in pts) else None
    return test


def main():
    bad = 0

    def check(name, cond, detail=""):
        nonlocal bad
        if not cond:
            bad += 1
        print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))

    tk = parse(Q).extra["takeoff"]
    check("question parses as a wc takeoff", tk["types"] == ["wc"], tk["types"])

    bad_room = room("Bad", 0, 0, 2, 2.5)            # bathroom with a toilet
    dusche = room("Dusche", 3, 0, 5, 1.5)           # shower room: a shower tray drawn, no toilet
    bad2 = room("Bad", 12, 4, 14, 6, number="2.09")  # bathroom with nothing drawn inside: unknown
    kitchen = room("Küche", 6, 0, 10, 3)
    bare = room(None, 0, 4, 2, 6, number="2.04")     # numbered room with no name, toilet inside
    hall = room("Korridor", 3, 4, 9, 5)              # corridor with a stray toilet symbol: never used
    sliver = room(None, 11, 0, 11.3, 0.4)            # shaft sliver around a symbol: smaller than the symbol
    toilets = [(0.2, 0.2, 0.6, 0.9), (0.5, 4.5, 0.9, 5.2), (4.0, 4.2, 4.4, 4.9), (11.0, 0.0, 11.4, 0.7)]
    rooms = [bad_room, dusche, kitchen, bare, hall, sliver]
    shower = [(4.0, 0.7)]

    # 1. no WC room, toilet symbols present: bathrooms with a toilet + unlabelled rooms with a toilet
    used, _, tk2 = toilet_fallback(rooms, tk, has_toilet=toilet_test(toilets, shower))
    check("fallback uses Bad and the unlabelled room with a toilet",
          [r["name"] for r in used] == ["Bad", None], [r["name"] for r in used])
    check("Dusche left out (no toilet drawn)", tk2["toilet_rooms"]["left_out"] == ["Dusche"], tk2["toilet_rooms"])
    check("corridor with a stray symbol not used", hall not in used)
    check("sliver smaller than the symbol not used", sliver not in used)
    check("fallback marked confirmed", tk2["toilet_rooms"]["confirmed"] is True)
    check("question interpretation untouched", tk["types"] == ["wc"] and "toilet_rooms" not in tk)
    check("estimate reason given", "no separate WC room" in fallback_reason(tk2))
    rows = [{"label": r["name"] or "unlabelled room", "number": r["number"], "area": r["poly"].area, "source": "test"}
            for r in used]
    total, items, expl = summary(rows, tk2)
    check("total is Bad + unlabelled room", abs(total - 9.0) < 1e-9, total)
    check("explanation opens with the interpretation",
          expl.startswith("No separate WC room on this floor; using the rooms that contain a toilet: Bad (5.00 m2), "
                          "unlabelled room 2.04 (4.00 m2)."), expl[:200])
    check("explanation names what was left out", "Left out, no toilet drawn inside: Dusche." in expl, expl)
    check("old 'separate WC rooms' sentence not used", "read as separate WC rooms" not in expl)

    # 1b. a bathroom with nothing drawn inside is used on its label, named as not confirmed
    used, _, tk2b = toilet_fallback(rooms + [bad2], tk, has_toilet=toilet_test(toilets, shower))
    check("bathroom with nothing drawn: used", any(r is bad2 for r in used) and not any(r is dusche for r in used))
    s1 = interpretation(tk2b, [("Bad", 5.0), ("Bad 2.09", 4.0), ("unlabelled room 2.04", 4.0)])
    check("bathroom with nothing drawn: named as not confirmed", "Bad 2.09: no fixtures drawn inside" in s1, s1)
    check("bathroom with nothing drawn: in the estimate reason", "label alone" in fallback_reason(tk2b))

    # 2. no toilet symbols on the floor (scan, untyped drawing): every bathroom label, not confirmed
    used, _, tk3 = toilet_fallback(rooms, tk, has_toilet=None)
    check("without symbols: all bathroom labels", [r["name"] for r in used] == ["Bad", "Dusche"], [r["name"] for r in used])
    check("without symbols: unlabelled rooms not used", bare not in used)
    check("without symbols: says the toilet is not confirmed",
          "no toilet symbols" in interpretation(tk3, [("Bad", 5.0), ("Dusche", 3.0)])
          and "not confirmed" in fallback_reason(tk3))

    # 3. a separate WC room exists: WC rooms only, unchanged behaviour
    wc = room("WC", 12, 0, 13, 1.5)
    used, _, tk4 = toilet_fallback(rooms + [wc], tk, has_toilet=toilet_test(toilets, shower))
    check("WC room present: only the WC room", [r["name"] for r in used] == ["WC"], [r["name"] for r in used])
    check("WC room present: interpretation unchanged", tk4 is tk and not fallback_reason(tk4))
    check("WC room present: says bathrooms are not included", "bathrooms with a WC seat not included" in describe(tk4))

    # 4. question names bathrooms too, or does not ask for toilets: unchanged
    tkb = parse("How many m2 of tiles for the toilets and bathrooms?").extra["takeoff"]
    used, _, tk5 = toilet_fallback(rooms, tkb, has_toilet=toilet_test(toilets, shower))
    check("toilets + bathrooms asked: plain select", tk5 is tkb and {r["name"] for r in used} == {"Bad", "Dusche"})
    tkk = parse("How many m2 of tiles for the kitchen?").extra["takeoff"]
    used, _, tk6 = toilet_fallback(rooms, tkk, has_toilet=toilet_test(toilets, shower))
    check("kitchen asked: plain select", tk6 is tkk and [r["name"] for r in used] == ["Küche"])

    # 5. toilets + kitchens, no WC room: kitchens kept, toilet rooms added, only toilet rooms named
    tkm = parse("How many m2 of tiles for the toilets and the kitchen?").extra["takeoff"]
    used, _, tk7 = toilet_fallback(rooms, tkm, has_toilet=toilet_test(toilets, shower))
    check("toilets + kitchen: kitchen, Bad, unlabelled", [r["name"] for r in used] == ["Küche", "Bad", None],
          [r["name"] for r in used])
    s = interpretation(tk7, [("Küche", 12.0), ("Bad", 5.0), ("unlabelled room 2.04", 4.0)])
    check("toilets + kitchen: kitchen not listed as a toilet room", "Küche" not in s, s)

    # 6. symbols present but no bathroom holds one, and no unlabelled room does: abstain with a reason
    used, _, tk8 = toilet_fallback([dusche, kitchen, hall], tk, has_toilet=toilet_test(toilets, shower))
    check("no room with a toilet: nothing used, reason given",
          not used and "has a toilet symbol drawn inside" in tk8["toilet_rooms"]["why"], tk8["toilet_rooms"])

    # generic DXF: toilet blocks confirm the bathroom, the shower room without one is left out
    import tempfile
    import ezdxf
    from bpqa.data import Question
    from bpqa.routes import dxf_generic
    doc = ezdxf.new(setup=True)
    doc.header["$INSUNITS"] = 4
    for name in ("A-AREA", "A-AREA-IDEN", "P-FIXT"):
        doc.layers.add(name)
    doc.blocks.new("WC-01").add_circle((0, 0), 200)
    msp = doc.modelspace()
    for (x, y, w, h), lab in [((0, 0, 2000, 2500), "Bathroom"), ((2000, 0, 1500, 2500), "Shower"),
                              ((3500, 0, 4000, 2500), "Kitchen")]:
        msp.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], close=True, dxfattribs={"layer": "A-AREA"})
        msp.add_text(lab, dxfattribs={"layer": "A-AREA-IDEN", "insert": (x + w / 2, y + h / 2)})
    doc.blocks.new("SHOWER-900").add_lwpolyline([(0, 0), (900, 0), (900, 900), (0, 900)], close=True)
    msp.add_blockref("WC-01", (500, 500), dxfattribs={"layer": "P-FIXT"})
    msp.add_blockref("SHOWER-900", (2200, 200), dxfattribs={"layer": "P-FIXT"})
    path = Path(tempfile.gettempdir()) / "stoa_toilet_fallback.dxf"
    doc.saveas(path)

    class Ctx:
        file, evidence, out_dir = path, False, Path(tempfile.gettempdir())
        q = Question(0, "x/" + path.name, "What is the floor area of the toilets in m2?")

    dxf_generic.load.cache_clear()
    a = dxf_generic.answer(Ctx(), parse(Ctx.q.question), path)
    check("generic DXF: bathroom with a WC block only", a.answered and abs(float(a.value) - 5.0) < 0.01, (a.value, a.abstain))
    check("generic DXF: estimate with the interpretation", a.tier == "estimate" and "Left out, no toilet drawn inside: Shower"
          in a.explanation, (a.tier, a.explanation[:200]))

    # end to end on a Swiss floor of the preview set (bathrooms named 'Bad', no WC room)
    root = os.environ.get("STOA_DATA")
    rel = "data/swiss_dwellings/site1015_bldg2113_floor9557.pdf"
    if root and (Path(root) / rel).exists():
        from bpqa.data import Question
        from bpqa.router import Context, solve
        ctx = Context(root=Path(root), q=Question(qid=0, path=rel, question=Q), out_dir=Path("out/test_evidence"),
                      evidence=False, nemotron=False)
        _, a = solve(ctx)
        check("Swiss floor answers the toilets question", a.answered, a.abstain)
        check("Swiss answer is an estimate", a.tier == "estimate", a.tier)
        check("Swiss answer states the interpretation", a.explanation.startswith("No separate WC room"), a.explanation[:120])
    else:
        print("skip end-to-end Swiss floor (STOA_DATA not set or file missing)")

    print(f"{bad} failure(s)")
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
