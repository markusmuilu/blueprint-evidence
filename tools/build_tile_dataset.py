"""Cut the detector dataset (build_detector_dataset.py output) into fixed-size tiles at a fixed scale.

    python tools/build_tile_dataset.py --ds ../data/detector/ds_cubi --public ../data/cubicasa5k-public/cubicasa5k
        --overlap ../stoa/out/overlap --out ../data/detector/ds_tiles

Same plans as the source dataset: every image listed in its train.txt and val.txt, nothing else.
CubiCasa plans are re-read from the public F1_original.png (the source dataset capped the long side
at 2048 px, which loses the native scale) and checked again against the overlap lists: a plan that
is not in train_clean.txt, or that matches a Stoa test-split image, is refused. Labels are the
source dataset's (normalised, so they apply at any resolution).

Each image is resized by --scale relative to its native pixels (long side capped at --max-side), the
same rule bpqa/routes/detector.py uses at inference, and cut into --tile x --tile tiles with
--overlap-px overlap. Tiles at the right or bottom edge are padded white to the full tile size, so
YOLO never rescales a tile and the scale seen in training is the scale seen at inference. A box is
kept in a tile when at least --min-vis of its area is inside; it is clipped to the tile. Tiles with
no boxes are kept with probability --empty-keep when they hold drawing (not blank margin).
Synthetic renders are tiled at their own scale (--synth-scale), a --synth-frac share of them.
"""
from __future__ import annotations

import argparse
import json
import zlib
import random
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.tiling import scale_for, tile_origins  # noqa: E402

Image.MAX_IMAGE_PIXELS = 300_000_000
CLASSES = ["toilet", "sink", "shower", "bathtub", "door", "window"]


def read_list(p: Path) -> list[str]:
    return [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def label_path(img: str) -> Path:
    p = Path(img)
    return p.parent.parent / "labels" / (p.stem + ".txt")


def cut(job):
    src_img, lab, plan, out, split, scale, max_side, T, ov, min_vis, empty_keep, seed = job
    try:
        im = Image.open(src_img).convert("RGB")
    except Exception as ex:
        return split, [], f"{src_img}: {type(ex).__name__}: {ex}"
    W0, H0 = im.size
    f = scale_for(W0, H0, scale, max_side)
    W, H = round(W0 * f), round(H0 * f)
    if (W, H) != (W0, H0):
        im = im.resize((W, H), Image.BICUBIC)
    boxes = []
    for line in Path(lab).read_text(encoding="utf-8").splitlines():
        p = line.split()
        if len(p) == 5:
            c, x, y, w, h = int(p[0]), *map(float, p[1:])
            boxes.append((c, (x - w / 2) * W, (y - h / 2) * H, (x + w / 2) * W, (y + h / 2) * H))
    arr = np.asarray(im)
    rng = random.Random(seed)
    stem = plan.replace("/", "-") if plan else Path(src_img).stem
    made = []
    for (x0, y0) in tile_origins(W, H, T, ov):
        x1, y1 = min(x0 + T, W), min(y0 + T, H)
        lines, ncls = [], Counter()
        for c, a, b, cc, d in boxes:
            ia, ib, ic, id_ = max(a, x0), max(b, y0), min(cc, x1), min(d, y1)
            if ic <= ia or id_ <= ib:
                continue
            full = max((cc - a) * (d - b), 1e-6)
            if (ic - ia) * (id_ - ib) < min_vis * full:
                continue
            cx, cy = ((ia + ic) / 2 - x0) / T, ((ib + id_) / 2 - y0) / T
            lines.append(f"{c} {cx:.6f} {cy:.6f} {(ic - ia) / T:.6f} {(id_ - ib) / T:.6f}")
            ncls[CLASSES[c]] += 1
        crop = arr[y0:y1, x0:x1]
        if not lines:
            ink = float((crop.min(axis=2) < 200).mean()) if crop.size else 0.0
            if ink < 0.02 or rng.random() >= empty_keep:
                continue
        tile = np.full((T, T, 3), 255, np.uint8)
        tile[: y1 - y0, : x1 - x0] = crop
        name = f"{stem}_{x0}_{y0}"
        Image.fromarray(tile).save(out / "images" / f"{name}.jpg", quality=92)
        (out / "labels" / f"{name}.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        made.append((str((out / "images" / f"{name}.jpg").resolve()), dict(ncls)))
    return split, made, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ds", required=True, help="source dataset (build_detector_dataset.py output)")
    ap.add_argument("--public", required=True, help="public CubiCasa5K root (folders colorful/, high_quality/, ...)")
    ap.add_argument("--overlap", required=True, help="output folder of tools/overlap.py")
    ap.add_argument("--out", required=True)
    ap.add_argument("--scale", type=float, default=2.0, help="resize factor relative to native pixels")
    ap.add_argument("--max-side", type=int, default=4096, help="cap on the resized long side")
    ap.add_argument("--tile", type=int, default=1024)
    ap.add_argument("--overlap-px", type=int, default=192)
    ap.add_argument("--min-vis", type=float, default=0.4)
    ap.add_argument("--empty-keep", type=float, default=0.25)
    ap.add_argument("--synth-scale", type=float, default=1.0)
    ap.add_argument("--synth-frac", type=float, default=0.5)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    ds, public, ov, out = Path(a.ds), Path(a.public), Path(a.overlap), Path(a.out)
    clean, test = set(read_list(ov / "train_clean.txt")), set(read_list(ov / "public_in_stoa_test.txt"))
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    rng = random.Random(0)
    jobs, refused, synth_skipped = [], [], 0
    for split in ("train", "val"):
        for img in read_list(ds / f"{split}.txt"):
            lab = label_path(img)
            if Path(img).parent.parent.resolve() == ds.resolve():      # a CubiCasa plan
                cat, num = Path(img).stem.split("-", 1)
                plan = f"{cat}/{num}"
                if plan not in clean or plan in test:
                    refused.append(plan)
                    continue
                src = public / cat / num / "F1_original.png"
                jobs.append((str(src), str(lab), plan, out, split, a.scale, a.max_side, a.tile, a.overlap_px,
                             a.min_vis, a.empty_keep, zlib.crc32(plan.encode())))
            else:                                                       # a synthetic render
                if rng.random() >= a.synth_frac:
                    synth_skipped += 1
                    continue
                jobs.append((img, str(lab), "", out, split, a.synth_scale, a.max_side, a.tile, a.overlap_px,
                             a.min_vis, a.empty_keep, 1))
    if refused:
        raise SystemExit(f"refusing: {len(refused)} plans not in train_clean.txt or matching a test image, e.g. {refused[:3]}")
    lists = {"train": [], "val": []}
    boxes = Counter()
    errors = []
    with ProcessPoolExecutor(a.workers) as ex:
        for i, (split, made, err) in enumerate(ex.map(cut, jobs, chunksize=4)):
            if err:
                errors.append(err)
            for p, c in made:
                lists[split].append(p)
                boxes.update(c)
            if (i + 1) % 500 == 0:
                print(f"  {i + 1}/{len(jobs)} images, {len(lists['train'])} train tiles", file=sys.stderr)
    for split, ps in lists.items():
        (out / f"{split}.txt").write_text("".join(p + "\n" for p in sorted(ps)), encoding="utf-8")
    (out / "data.yaml").write_text(f"path: {out.resolve().as_posix()}\ntrain: train.txt\nval: val.txt\nnames:\n"
                                   + "".join(f"  {i}: {n}\n" for i, n in enumerate(CLASSES)), encoding="utf-8")
    rep = {"source": str(ds), "images": len(jobs), "synthetic_skipped": synth_skipped, "train_tiles": len(lists["train"]),
           "val_tiles": len(lists["val"]), "boxes": dict(boxes), "errors": len(errors), "error_examples": errors[:5],
           "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(a).items()}}
    (out / "build.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
