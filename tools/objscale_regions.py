"""Room regions and the printed-area scale of CubiCasa plans, cached for the object-scale validation.

For every plan in --list: the upright OCR pass (as tools/area_eval.py), every walls@ppm region hypothesis of
raster_area, and for each hypothesis the rooms with a printed area and a usable region (printed m2, region
pixels). Also the printed-area scale that raster_area chooses (the reference the object scale is checked
against). One JSON per plan in --out.

    python tools/objscale_regions.py --data <root> --list out/objscale/pool.txt --out out/objscale/regions
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2

from bpqa.data import data_root
from bpqa.routes import raster_area as RA


def one(f: Path) -> dict | None:
    toks = RA.tokens_r0(f, allow_run=False)
    img = cv2.imread(str(f))
    if toks is None or img is None:
        return None
    toks = [t for t in toks if t["conf"] >= 0.5]
    proto, free, others = RA.plan_rooms(toks)
    hyps = RA.hypotheses(img, toks, proto, others, mode="walls")
    name, rs, sc = RA.choose(hyps)
    out = {"file": f.name, "wh": [img.shape[1], img.shape[0]], "scale_m2_per_px": sc.m2_per_px,
           "scale_rooms": len(sc.rooms), "scale_detail": sc.detail, "regions": name, "hyps": {}}
    for hname, ppm, rooms in hyps:
        out["hyps"][str(ppm)] = [{"i": i, "label": r.label, "printed": r.printed, "pixels": r.pixels}
                                 for i, r in enumerate(rooms) if r.region_ok]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    ap.add_argument("--list", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    root = data_root(a.data)
    od = Path(a.out)
    od.mkdir(parents=True, exist_ok=True)
    names = [n for n in Path(a.list).read_text().split() if n]
    for k, n in enumerate(names):
        p = od / f"{Path(n).stem}.json"
        if p.exists():
            continue
        r = one(root / "data" / "cubicasa5k" / n)
        if r is not None:
            p.write_text(json.dumps(r), encoding="utf-8")
        if k % 20 == 0:
            print(f"{k + 1}/{len(names)}", flush=True)


if __name__ == "__main__":
    main()
