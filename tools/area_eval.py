"""Leave-one-out check of measured room areas on CubiCasa scans, against the areas printed on them.

Stoa asks no area questions on CubiCasa, so the reference is the plan itself: for every room that
has a printed area and a closed region, the pixel scale is estimated from the OTHER rooms only (the
room's own printed value is held out), the region is converted with that scale, and the result is
compared with the printed value. Only images whose r0 OCR is cached are used (tools/ocr_r0.py).

    python tools/area_eval.py --data <root> --split dev [--limit N] [--debug out/area_debug]

Reports: plans with printed areas, rooms with a printed area, rooms with a closed region, rooms that
got a held-out scale, and agreement within 2% / 5% of the printed value (of those measured, and of
all printed rooms). Use --split dev while tuning; run --split test once at the end.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np

from bpqa.data import data_root, split_of
from bpqa.routes import raster_area as RA
from bpqa.routes.ocr import cache_path


def draw(img, pa, rows, out):
    im = img.copy()
    over = im.copy()
    for r in pa.rooms:
        if r.region_ok:
            col = (60, 160, 60) if r.printed is not None else (200, 140, 40)
            cv2.drawContours(over, [r.contour], -1, col, -1)
    im = cv2.addWeighted(over, 0.35, im, 0.65, 0)
    for r in pa.rooms:
        if r.area_box is not None:
            b = [int(v) for v in r.area_box]
            cv2.rectangle(im, (b[0], b[1]), (b[2], b[3]), (200, 30, 30), 1)
        if not r.region_ok:
            x, y = (int(v) for v in r.seed)
            cv2.drawMarker(im, (x, y), (0, 0, 255), cv2.MARKER_TILTED_CROSS, 10, 2)
    for row in rows:
        x, y = (int(v) for v in row["seed"])
        cv2.putText(im, f"{row['pred']:.1f}/{row['printed']:.1f}", (x - 20, y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                    (0, 0, 200) if abs(row["err"]) > 0.05 else (0, 120, 0), 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), im)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--debug")
    ap.add_argument("--k", type=int, default=0, help="barrier thickening in px (0: from image size)")
    ap.add_argument("--dark", type=int, default=RA.DARK)
    ap.add_argument("--tol", type=float, default=RA.SCALE_TOL)
    ap.add_argument("--out")
    ap.add_argument("--mode", default="auto", choices=["auto", "lines", "walls"])
    ap.add_argument("--close-k", type=int, default=RA.CLOSE_K)
    ap.add_argument("--open-k", type=int, default=RA.OPEN_K)
    ap.add_argument("--door-m", type=float, default=RA.DOOR_M)
    ap.add_argument("--furn-m", type=float, default=RA.FURN_M)
    ap.add_argument("--thick-add", type=int, default=RA.THICK_ADD)
    ap.add_argument("--no-refine", action="store_true")
    ap.add_argument("--list", help="file of image names to use (fixed set across runs)")
    args = ap.parse_args()
    RA.DARK = args.dark
    RA.SCALE_TOL = args.tol
    RA.CLOSE_K, RA.OPEN_K, RA.DOOR_M = args.close_k, args.open_k, args.door_m
    RA.FURN_M, RA.THICK_ADD, RA.REFINE = args.furn_m, args.thick_add, not args.no_refine
    root = data_root(args.data)
    files = sorted((root / "data" / "cubicasa5k").glob("*.png"))
    files = [f for f in files if split_of(f"data/cubicasa5k/{f.name}") == args.split and cache_path(f, 0).exists()]
    if args.list:
        keep = set(Path(args.list).read_text().split())
        files = [f for f in files if f.name in keep]
    if args.limit:
        files = files[:args.limit]
    st = dict(images=len(files), plans=0, printed=0, region=0, scaled=0, w2=0, w5=0, plans_scaled=0)
    rows_all = []
    for f in files:
        toks = RA.tokens_r0(f, allow_run=False)
        toks = [t for t in toks if t["conf"] >= 0.5]
        rooms, free = RA.read_rooms(toks)
        if sum(r.printed is not None for r in rooms) < 3:
            continue
        img = cv2.imread(str(f))
        st["plans"] += 1
        proto, free, others = RA.plan_rooms(toks)
        hyps = RA.hypotheses(img, toks, proto, others, mode=args.mode)
        _, rs0, sc0 = RA.choose(hyps)
        pa = RA.PlanAreas(rs0 or hyps[0][2], free, sc0, "")
        rooms_all = pa.rooms
        rows = []
        got = False
        for i, r in enumerate(rooms_all):
            if r.printed is None:
                continue
            st["printed"] += 1
            name, rs, sc = RA.choose(hyps, exclude=i)      # scale and region set chosen without room i
            if not sc.known:
                continue
            r = rs[i]
            if not r.region_ok:
                continue
            st["region"] += 1
            got = True
            st["scaled"] += 1
            pred = r.pixels * sc.m2_per_px
            err = (pred - r.printed) / r.printed
            st["w2"] += abs(err) <= 0.02
            st["w5"] += abs(err) <= 0.05
            rows.append({"file": f.name, "label": r.label, "printed": r.printed, "pred": round(pred, 2),
                         "err": round(err, 4), "seed": r.seed, "n_scale": len(sc.rooms), "regions": name})
        st["plans_scaled"] += got
        rows_all += rows
        if args.debug:
            draw(img, pa, rows, Path(args.debug) / f"{f.stem}.png")
    s = st["scaled"] or 1
    p = st["printed"] or 1
    print(json.dumps(st))
    print(f"measured with a held-out scale: {st['scaled']} of {st['printed']} printed rooms ({st['scaled'] / p:.1%}), "
          f"{st['plans_scaled']} of {st['plans']} plans")
    print(f"within 2%: {st['w2'] / s:.1%}   within 5%: {st['w5'] / s:.1%}   (of measured)")
    print(f"within 2%: {st['w2'] / p:.1%}   within 5%: {st['w5'] / p:.1%}   (of all printed rooms)")
    if rows_all:
        e = np.array([abs(r["err"]) for r in rows_all])
        print(f"median abs error {np.median(e):.1%}, 90th pct {np.percentile(e, 90):.1%}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text("\n".join(json.dumps(r) for r in rows_all), encoding="utf-8")


if __name__ == "__main__":
    main()
