"""Fill the OCR cache with the upright pass only (out/ocr/<stem>.r0.json), for the area work.

Printed room areas ("12,1 m2") are written horizontally, so one upright pass is enough to find
them; the full eight-pass cache (tools/ocr_cache.py) is only needed for the label counts.

    python tools/ocr_r0.py --data <root> --split dev --limit 1500 [--shard 0 --nshards 2]

Images are taken in a fixed shuffled order (seed 0), so a run that is stopped early is still a
random sample of the split.
"""
import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2

from bpqa.data import data_root, split_of
from bpqa.routes.ocr import _run, cache_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    ap.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    args = ap.parse_args()
    root = data_root(args.data)
    files = sorted((root / "data" / "cubicasa5k").glob("*.png"))
    files = [f for f in files if args.split == "all" or split_of(f"data/cubicasa5k/{f.name}") == args.split]
    random.Random(0).shuffle(files)
    if args.limit:
        files = files[:args.limit]
    files = files[args.shard::args.nshards]
    for i, f in enumerate(files):
        p = cache_path(f, 0, False)
        if p.exists():
            continue
        img = cv2.imread(str(f))
        if img is None:
            continue
        t = _run(img, 0, False)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
        print(i, f.name, len(t), flush=True)


if __name__ == "__main__":
    main()
