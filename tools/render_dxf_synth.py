"""Synthetic detector training images from Swiss Dwellings DXF floors, with automatic boxes.

    python tools/render_dxf_synth.py --data ../data/stoa-full/junction-dataset --out ../data/detector/synth --per 2

Each train-split (dev) DXF floor is drawn black on white like a scanned plan: walls filled, all
lines and arcs, room names as text, at a random scale (35-90 px per metre), a random rotation
(half of them a multiple of 90 degrees, the rest any angle), sometimes mirrored, slightly blurred
and JPEG-compressed. Boxes come from the same rules the dxf-geometry route uses (100% on dev and
test counts): toilets and washbasins (48-vertex ellipses, with or without a drain circle),
bathtubs (69-vertex), showers (5-vertex FIXTURE), windows (grouped WINDOW strips), doors (the
5-vertex DOOR polygon, which is the opening in the wall). Test-split drawings are never rendered. Output is YOLO format:
<out>/images/<name>.png and <out>/labels/<name>.txt, class ids as in tools/cubicasa_svg.CLASSES.
"""
from __future__ import annotations

import argparse
import io
import math
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.data import split_of  # noqa: E402
from bpqa.routes.dxf_swiss import SwissFloor  # noqa: E402
from tools.cubicasa_svg import CLASSES  # noqa: E402

MAX_SIDE = 2400


def door_boxes(floor: SwissFloor):
    """Door boxes (world units): the 5-vertex DOOR polygon is the opening in the wall (the swing
    arc is centred on one of its corners and ends on the other), the same thing a CubiCasa door
    polygon marks."""
    out = []
    for leaf in floor.doors:
        pts = np.array(leaf)
        out.append((*pts.min(0), *pts.max(0)))
    return out


def boxes_world(dxf_path: Path, fl: SwissFloor):
    b = [("toilet", x) for x in fl.toilets] + [("sink", x) for x in fl.washbasins]
    b += [("bathtub", x) for x in fl.bathtubs] + [("shower", x) for x in fl.showers]
    b += [("window", x) for x in fl.windows] + [("door", x) for x in door_boxes(fl)]
    return b


def render(dxf_path: Path, rng: random.Random):
    fl = SwissFloor(dxf_path)
    x0, y0, x1, y1 = fl.bounds
    ppm = rng.uniform(35, 90)
    ang = rng.choice([0, 90, 180, 270]) if rng.random() < 0.5 else rng.uniform(0, 360)
    mirror = rng.random() < 0.3
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    t = math.radians(ang)
    R = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])

    def tf(p):
        p = np.asarray(p, float).reshape(-1, 2) - [cx, cy]
        p = p @ R.T
        p[:, 1] *= -1                         # y up -> image y down
        if mirror:
            p[:, 0] *= -1
        return p

    corners = tf([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
    span = corners.max(0) - corners.min(0)
    ppm = min(ppm, (MAX_SIDE - 80) / max(span.max(), 1e-6))
    margin = rng.randint(20, 80)
    W, H = (span * ppm).astype(int) + 2 * margin
    off = -corners.min(0) * ppm + margin

    def px(p):
        return tf(p) * ppm + off

    img = np.full((H, W), 255, np.uint8)
    wall_gray = rng.choice([0, 40, 90, 140])
    lw = rng.choice([1, 1, 2])
    for layer, pts in fl.lines:
        if len(pts) < 2:
            continue
        q = np.round(px(pts)).astype(np.int32)
        if layer == "WALL" and len(pts) > 3 and pts[0] == pts[-1]:
            cv2.fillPoly(img, [q], int(wall_gray), lineType=cv2.LINE_AA)
            cv2.polylines(img, [q], False, 0, lw, cv2.LINE_AA)
        elif layer in ("UNIT_OUTLINE", "ROOM_OUTLINE"):
            continue                          # not drawn on CubiCasa plans
        else:
            cv2.polylines(img, [q], False, 0, lw, cv2.LINE_AA)
    for layer, c, r in fl.circles:
        q = px([c])[0]
        cv2.circle(img, (int(q[0]), int(q[1])), max(1, int(r * ppm)), 0, 1, cv2.LINE_AA)
    fs = ppm / 120 * rng.uniform(0.5, 0.9)
    for layer, s, xy in fl.texts:
        if layer == "ROOM_TEXT" and s and s.isascii():
            q = px([xy])[0]
            cv2.putText(img, s, (int(q[0]) - 4 * len(s), int(q[1])), cv2.FONT_HERSHEY_SIMPLEX, fs, 0, 1, cv2.LINE_AA)
    if rng.random() < 0.5:
        img = cv2.GaussianBlur(img, (3, 3), rng.uniform(0.3, 0.9))

    labels = []
    for kind, (a0, b0, a1, b1) in boxes_world(dxf_path, fl):
        q = px([[a0, b0], [a1, b0], [a1, b1], [a0, b1]])
        u0, v0 = q.min(0)
        u1, v1 = q.max(0)
        u0, v0, u1, v1 = max(0, u0 - 1), max(0, v0 - 1), min(W, u1 + 1), min(H, v1 + 1)
        if u1 - u0 >= 3 and v1 - v0 >= 3:
            labels.append(f"{CLASSES.index(kind)} {(u0 + u1) / 2 / W:.6f} {(v0 + v1) / 2 / H:.6f} {(u1 - u0) / W:.6f} {(v1 - v0) / H:.6f}")
    buf = io.BytesIO()
    Image.fromarray(img).convert("RGB").save(buf, "JPEG", quality=rng.randint(60, 90))
    return np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB")), labels


def job(args):
    path, out, k, seed = args
    rng = random.Random(f"{seed}-{path.name}-{k}")
    try:
        img, labels = render(path, rng)
    except Exception as ex:
        return f"{path.name}: {type(ex).__name__}: {ex}"
    name = f"swiss-{path.stem}-r{k}"
    Image.fromarray(img).save(out / "images" / f"{name}.png", optimize=False, compress_level=3)
    (out / "labels" / f"{name}.txt").write_text("\n".join(labels) + ("\n" if labels else ""), encoding="utf-8")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="Stoa dataset root (folder with data/swiss_dwellings/)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--per", type=int, default=2, help="renders per drawing")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--seed", default="synth1")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    root, out = Path(a.data), Path(a.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    dxfs = sorted((root / "data" / "swiss_dwellings").glob("*.dxf"))
    keep = [p for p in dxfs if split_of(f"data/swiss_dwellings/{p.name}") == "dev"]
    print(f"{len(dxfs)} DXF floors, {len(keep)} in the train (dev) split; test-split floors are not rendered")
    if a.limit:
        keep = keep[: a.limit]
    jobs = [(p, out, k, a.seed) for p in keep for k in range(a.per)]
    errs = []
    with ProcessPoolExecutor(a.workers) as ex:
        for i, e in enumerate(ex.map(job, jobs, chunksize=4)):
            if e:
                errs.append(e)
            if (i + 1) % 200 == 0:
                print(f"  {i + 1}/{len(jobs)}")
    print(f"rendered {len(jobs) - len(errs)} images, {len(errs)} errors")
    for e in errs[:10]:
        print("  ", e)


if __name__ == "__main__":
    main()
