"""IFC route: toilets fallback and ifc-props leaving takeoff questions alone: python tests/test_ifc_toilets.py

No dataset needed: ifc-props must abstain on a takeoff question before it opens the model, and the
fallback helper must pick bathrooms with a WC pan from stand-in spaces.
"""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.query import parse
from bpqa.routes import ifc_props, ifc_route

Q = "How many square meters of tiling should I order for the toilets?"


def main():
    bad = 0

    def check(name, cond):
        nonlocal bad
        bad += not cond
        print(("ok   " if cond else "FAIL ") + name)

    q = parse(Q)
    ctx = NS(model_ifc=lambda: Path("missing.ifc"), q=NS(question=Q, ext="ifc"))
    a = ifc_props.answer(ctx, q)
    check("ifc-props abstains on a takeoff question", not a.answered and "takeoff" in a.abstain)

    sp = lambda i, n: NS(id=lambda: i, Name=n, LongName=None)
    floor = [sp(1, "Bathroom"), sp(2, "Bathroom 2"), sp(3, "Kitchen")]
    real = (ifc_route._space_fixtures, ifc_route.fixture_kind)
    try:
        ifc_route._space_fixtures = lambda M, f: {1: [("pan", "x")], 2: [("tub", "x")], 3: []}
        ifc_route.fixture_kind = lambda e: {"pan": "wc", "tub": "bath"}[e]
        used, tk2, why = ifc_route.toilet_rooms_fallback(None, floor, q.extra["takeoff"])
    finally:
        ifc_route._space_fixtures, ifc_route.fixture_kind = real
    check("bathroom with a pan used, tub-only and kitchen not", [s.Name for s in used] == ["Bathroom"])
    check("interpretation recorded", bool(tk2.get("toilet_rooms")) and not why)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
