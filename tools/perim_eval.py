"""Region perimeter and area of CubiCasa scans against the annotation's own room polygons.

The wall estimate on a scan is perimeter of the measured room region x scale x assumed height. The scan
carries no printed perimeter, so the reference is CubiCasa's model.svg (`Space` polygons, the annotated
room outlines), read as data only. The comparison is scale-free (region pixels against annotation
pixels), so it measures the region and not the printed-area scale. For each room that has a printed
label and a closed region, the room is matched to the Space polygon containing its seed.

    python tools/perim_eval.py --data <root> --public <cubicasa5k-public/cubicasa5k> --split dev [--limit N]

Reports the distribution of |region perimeter / annotated perimeter - 1| and the same for area.
"""
import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np

from bpqa.data import data_root, split_of
from bpqa.routes import raster_area as RA
from bpqa.routes.ocr import cache_path

_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)")


def spaces(svg_text: str):
    """[(class, Nx2 float array in SVG units)] for every Space; (size)."""
    if "<!ENTITY" in svg_text[:5000] or "<!DOCTYPE" in svg_text[:5000]:
        raise ValueError("SVG with a DTD/entities: refused")
    root = ET.fromstring(svg_text)
    size = (float(_NUM.findall(root.get("width"))[0]), float(_NUM.findall(root.get("height"))[0]))
    out = []
    for g in root.iter():
        cls = g.get("class") or ""
        if g.tag.endswith("g") and cls.startswith("Space"):
            for ch in g:
                if ch.tag.endswith("polygon"):
                    v = [float(x) for x in _NUM.findall(ch.get("points", ""))]
                    pts = np.array(v[: len(v) // 2 * 2]).reshape(-1, 2)
                    if len(pts) >= 3:
                        out.append((cls, pts))
                    break
    return out, size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    ap.add_argument("--public", default=str(Path(__file__).resolve().parents[2] / "data" / "cubicasa5k-public" / "cubicasa5k"))
    ap.add_argument("--split", default="dev")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out")
    args = ap.parse_args()
    root = data_root(args.data)
    files = sorted((root / "data" / "cubicasa5k").glob("*.png"))
    files = [f for f in files if split_of(f"data/cubicasa5k/{f.name}") == args.split and cache_path(f, 0).exists()]
    if args.limit:
        files = files[:args.limit]
    rows, plans = [], 0
    for f in files:
        style, num = f.stem.rsplit("-", 1)
        svg = Path(args.public) / style / num / "model.svg"
        if not svg.exists():
            continue
        toks = [t for t in RA.tokens_r0(f, allow_run=False) if t["conf"] >= 0.5]
        rooms, _ = RA.read_rooms(toks)
        if sum(r.printed is not None for r in rooms) < 3:
            continue
        pa = RA.analyse(str(f), allow_run=False)
        RA.analyse.cache_clear()
        if pa is None or not pa.scale.known:
            continue
        img = cv2.imread(str(f))
        H, W = img.shape[:2]
        try:
            sp, size = spaces(svg.read_text(encoding="utf-8", errors="replace"))
        except (ET.ParseError, ValueError, IndexError):
            continue
        from PIL import Image
        with Image.open(svg.with_name('F1_scaled.png')) as im:      # annotation frame; the dataset image is F1_original
            sw, sh = im.size
        sx, sy = W / sw, H / sh
        plans += 1
        for r in pa.rooms:
            if not r.region_ok:
                continue
            hit = None
            for cls, pts in sp:
                p = (pts * [sx, sy]).astype(np.float32)
                if cv2.pointPolygonTest(p.reshape(-1, 1, 2), (float(r.seed[0]), float(r.seed[1])), False) >= 0 \
                        and "Outdoor" not in cls:
                    a = cv2.contourArea(p)
                    if hit is None or a < hit[0]:
                        hit = (a, float(cv2.arcLength(p.reshape(-1, 1, 2), True)), cls)
            if hit is None or hit[0] < 50:
                continue
            per = RA.region_perimeter_px(r.contour)
            rows.append({"file": f.name, "label": r.label, "printed": r.printed, "cls": hit[2],
                         "area_err": r.pixels / hit[0] - 1, "perim_err": per / hit[1] - 1})
    print(f"plans with a scale: {plans}, rooms matched to an annotated space: {len(rows)}")
    if rows:
        for k in ("perim_err", "area_err"):
            e = np.abs([r[k] for r in rows])
            s = np.array([r[k] for r in rows])
            print(f"{k}: median abs {np.median(e):.1%}, 80th {np.percentile(e, 80):.1%}, 90th {np.percentile(e, 90):.1%}, "
                  f"within 5% {np.mean(e <= .05):.1%}, within 10% {np.mean(e <= .10):.1%}, signed median {np.median(s):+.1%}")
    if args.out:
        Path(args.out).write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


if __name__ == "__main__":
    main()
