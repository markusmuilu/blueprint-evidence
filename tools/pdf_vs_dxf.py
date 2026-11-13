"""Diagnostic: compare what the pdf-vector route reads from a Swiss PDF with the DXF twin of the
same drawing (dev split only). Per object: share of floors where the PDF count equals the DXF count,
room-area agreement within 2%, and how the scale was found.

    python tools/pdf_vs_dxf.py --data <dataset root> --limit 150
"""
from __future__ import annotations

import argparse
import collections
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.data import data_root, split_of  # noqa: E402
from bpqa.routes.dxf_swiss import SwissFloor  # noqa: E402
from bpqa.routes.pdf_vector import PdfFloor  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--split", default="dev")
    ap.add_argument("--show", default="", help="object whose mismatches to list")
    a = ap.parse_args()
    root = data_root(a.data)
    files = sorted((root / "data" / "swiss_dwellings").glob("*.pdf"))
    files = [f for f in files if split_of(f"data/swiss_dwellings/{f.name}") == a.split]
    random.Random(0).shuffle(files)
    files = files[: a.limit]
    eq = collections.Counter()
    n = 0
    scale_m = collections.Counter()
    area_ok = area_n = 0
    for f in files:
        d = SwissFloor(f.with_suffix(".dxf"))
        p = PdfFloor(str(f))
        n += 1
        scale_m[p.scale.method] += 1
        for k in ["doors", "windows", "toilets", "washbasins", "bathtubs", "showers", "units"]:
            ok = len(getattr(d, k)) == len(getattr(p, k))
            eq[k] += ok
            if not ok and a.show == k:
                print(f.name, k, "dxf", len(getattr(d, k)), "pdf", len(getattr(p, k)))
        dn = {r["number"]: r for r in d.rooms if r["number"]}
        pn = {r["number"]: r for r in p.rooms if r["number"]}
        eq["room numbers"] += set(dn) == set(pn)
        dnames = collections.Counter(r["name"] for r in d.rooms if r["name"])
        pnames = collections.Counter(r["name"] for r in p.rooms if r["name"])
        eq["room name counts"] += sum(dnames.values()) == sum(pnames.values())
        for no, r in dn.items():
            if no in pn and p.scale.known:
                area_n += 1
                ga = r["poly"].area
                pa = p.room_area(pn[no])
                ok = abs(pa - ga) <= 0.02 * ga if ga else abs(pa) < 1e-6
                area_ok += ok
                if not ok and a.show == "area":
                    print(f.name, no, r["name"], round(ga, 2), pa)
            elif a.show == "area":
                print(f.name, no, "missing in pdf" if no not in pn else "no scale")
    print(f"floors {n}")
    for k, v in eq.items():
        print(f"  {k:<18} {v}/{n} = {v / n:.1%}")
    print(f"  room areas within 2%: {area_ok}/{area_n}")
    print("  scale methods:", dict(scale_m))


if __name__ == "__main__":
    main()
