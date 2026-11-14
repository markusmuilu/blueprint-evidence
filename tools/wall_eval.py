"""Validate the wall-surface takeoff (bpqa/walls.py) against the quantities authoring tools wrote into IFC models.

For every IfcSpace of every model that carries them, compares what the takeoff computes from geometry with:

  perimeter        Qto GrossPerimeter / NetPerimeter, Revit 'Perimeter', ArchiCAD 'Umfang' / 'Nettoumfang'
  gross wall area  Qto GrossWallArea           (ours: outline length x space height)
  net wall area    Qto NetWallArea             (ours: gross - doors and windows on the room's walls)

None of these quantities is read by the takeoff itself (it reports NetWallArea only as a check), so the
comparison is independent. Reports, per quantity: n, share within 2% and 5%, median relative error.

    python tools/wall_eval.py --data ../data/stoa-full/junction-dataset [--split dev|test|all]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa import walls as W  # noqa: E402
from bpqa.data import split_of  # noqa: E402
from bpqa.routes.ifc_route import Model, space_display, space_height, space_openings, wall_faces  # noqa: E402
import ifcopenshell.util.element as E  # noqa: E402

PERIM_KEYS = [("qto", "GrossPerimeter"), ("pset", "Perimeter"), ("pset", "Umfang"), ("pset", "Nettoumfang")]


def model_perimeter(M, sp):
    v, src = M.qto(sp, ["GrossPerimeter"])
    if v is not None:
        return v * M.len_scale, src
    for ps, vals in E.get_psets(sp, psets_only=True).items():
        for k in ("Perimeter", "Umfang", "Nettoumfang"):
            if isinstance(vals.get(k), (int, float)) and vals[k] > 0:
                return float(vals[k]) * M.len_scale, f"{ps}.{k}"
    return None, None


def rel(a, b):
    return abs(a - b) / b if b else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="../data/stoa-full/junction-dataset")
    ap.add_argument("--split", default="all", choices=["dev", "test", "all"])
    ap.add_argument("--out", default="out/wall_eval.json")
    args = ap.parse_args(argv)
    root = Path(args.data) / "data"
    files = sorted(set(root.glob("ifc_models/*.ifc")) | set(root.glob("ifc_bench/*_arc.ifc")))
    rows = []
    seen_models = set()
    for f in files:
        rp = f"data/{f.parent.name}/{f.name}"
        if args.split != "all" and split_of(rp) != args.split:
            continue
        if f.stat().st_size > 150e6:
            continue
        try:
            M = Model(f)
        except Exception as ex:
            print(f"skip {f.name}: {ex}", file=sys.stderr)
            continue
        spaces = M.m.by_type("IfcSpace")
        if not spaces:
            continue
        sig = (len(spaces), round(sum((M.qto(s, ["NetFloorArea"])[0] or 0) for s in spaces), 1))
        has = 0
        for sp in spaces:
            mp, mps = model_perimeter(M, sp)
            mg, _ = M.qto(sp, ["GrossWallArea"])
            mn, _ = M.qto(sp, ["NetWallArea"])
            if mp is None and mg is None and mn is None:
                continue
            fp, _ = M.footprint(sp)
            if fp is None or fp.is_empty:
                rows.append({"model": f.name, "space": space_display(sp), "error": "no geometry"})
                continue
            has += 1
            poly = fp.simplify(0.005)
            per = W.ring_length(poly)
            _, wper, _ = wall_faces(M, sp, poly)
            h, hsrc = space_height(M, sp)
            ops = space_openings(M, sp, poly)
            r = {"model": f.name, "split": split_of(rp), "space": space_display(sp), "perimeter": per, "wall_perimeter": wper,
                 "model_perimeter": mp, "perimeter_src": mps, "height": h, "height_src": hsrc,
                 "n_openings": len(ops), "dup_of": None}
            if h:
                rw = W.room_walls({"perimeter": per, "height": h, "height_src": hsrc, "openings": ops}, {})
                r.update(gross=rw["gross"], net=rw["net"])
                rw0 = W.room_walls({"perimeter": wper, "height": h, "height_src": hsrc, "openings": ops}, {})
                r.update(gross_along_walls=rw0["gross"], net_along_walls=rw0["net"])
            if mg is not None:
                r["model_gross"] = mg * M._area_scale()
            if mn is not None:
                r["model_net"] = mn * M._area_scale()
            rows.append(r)
        if has:
            dup = sig in seen_models
            seen_models.add(sig)
            if dup:      # the same model shipped twice (ifc_models and ifc_bench copies): count once
                for r in rows:
                    if r["model"] == f.name:
                        r["dup_of"] = True
            print(f"{f.name}: {has} spaces with reference quantities" + (" (duplicate model, not counted)" if dup else ""), file=sys.stderr)
    ok = [r for r in rows if "error" not in r and not r.get("dup_of")]
    report = {}
    # a reference whose unit disagrees with the model's own length unit (ratio off by 10x or more on the
    # median) is not a usable ground truth; such models are reported, not scored
    bad = set()
    for m in {r["model"] for r in ok}:
        rat = [r["model_perimeter"] / r["perimeter"] for r in ok if r["model"] == m and r.get("model_perimeter") and r["perimeter"]]
        if rat and not (0.1 < statistics.median(rat) < 10):
            bad.add(m)
            print(f"reference perimeter unit inconsistent in {m} (median ratio {statistics.median(rat):.1f}); not scored", file=sys.stderr)
    ok = [r for r in ok if r["model"] not in bad]
    report["excluded_models"] = sorted(bad)
    for name, a, b in (("perimeter (whole outline)", "perimeter", "model_perimeter"),
                       ("gross wall area", "gross", "model_gross"), ("net wall area", "net", "model_net"),
                       ("gross wall area, edges along walls only (rejected variant)", "gross_along_walls", "model_gross"),
                       ("net wall area, edges along walls only (rejected variant)", "net_along_walls", "model_net")):
        errs = [rel(r[a], r[b]) for r in ok if r.get(a) is not None and r.get(b)]
        errs = [e for e in errs if e is not None]
        models = sorted({r["model"] for r in ok if r.get(a) is not None and r.get(b)})
        if not errs:
            continue
        report[name] = {"n": len(errs), "models": models, "within_2pct": sum(e <= 0.02 for e in errs) / len(errs),
                        "within_5pct": sum(e <= 0.05 for e in errs) / len(errs), "median_err": statistics.median(errs)}
        print(f"{name:<16} n={len(errs):>4}  within 2%: {report[name]['within_2pct']:.1%}  within 5%: "
              f"{report[name]['within_5pct']:.1%}  median error {report[name]['median_err']:.2%}  models: {', '.join(models)}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"report": report, "rows": rows}, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
