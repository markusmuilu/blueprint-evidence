"""Test tools/overlap.py on the preview: known overlaps under resizing, recompression, margins, mirroring.

    python tools/test_overlap.py --work <scratch dir>

Builds two fake roots in --work:
  stoa/data/cubicasa5k/     half of the preview CubiCasa images, unchanged (plus all Swiss PNGs as distractors)
  public/<cat>/<id>/        every preview CubiCasa image, laid out like CubiCasa5K, as a degraded copy:
                            resized to 35-90%, JPEG-recompressed, some with a white margin, some mirrored,
                            and half also keep an unchanged F1_original.png
Expected: exactly the plans whose image is on the Stoa side match, each to its own image, and the
other half (real plans of the same style, the hard negatives) match nothing. Also prints the
distance distributions of true pairs and of the nearest wrong pairs, which set the thresholds.
"""
from __future__ import annotations

import argparse
import io
import json
import random
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.overlap as ov  # noqa: E402
from bpqa.data import DEFAULT_DATA, split_of  # noqa: E402


def degrade(src: Path, dst: Path, rng: random.Random):
    im = Image.open(src).convert("RGB")
    s = rng.uniform(0.35, 0.9)
    im = im.resize((max(8, int(im.width * s)), max(8, int(im.height * s))), Image.BILINEAR)
    if rng.random() < 0.3:
        m = rng.randint(10, 60)
        bg = Image.new("RGB", (im.width + 2 * m, im.height + 2 * m), "white")
        bg.paste(im, (m, m))
        im = bg
    mirrored = rng.random() < 0.3
    if mirrored:
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=rng.randint(55, 85))
    buf.seek(0)
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.open(buf).save(dst)
    return mirrored


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", default=str(DEFAULT_DATA))
    ap.add_argument("--work", required=True)
    a = ap.parse_args()
    prev, work = Path(a.preview), Path(a.work)
    if work.exists():
        shutil.rmtree(work)
    rng = random.Random(7)
    cubi = sorted((prev / "data" / "cubicasa5k").glob("*.png"))
    rng.shuffle(cubi)
    on_stoa = set(p.name for p in cubi[: len(cubi) // 2])
    for p in cubi:
        cat, pid = p.stem.rsplit("-", 1)
        d = work / "public" / cat / pid
        degrade(p, d / "F1_scaled.png", rng)
        if rng.random() < 0.5:
            shutil.copy(p, d / "F1_original.png")
        if p.name in on_stoa:
            (work / "stoa" / "data" / "cubicasa5k").mkdir(parents=True, exist_ok=True)
            shutil.copy(p, work / "stoa" / "data" / "cubicasa5k" / p.name)
    swiss = sorted((prev / "data" / "swiss_dwellings").glob("*.png"))
    (work / "stoa" / "data" / "swiss_dwellings").mkdir(parents=True, exist_ok=True)
    for p in swiss:
        shutil.copy(p, work / "stoa" / "data" / "swiss_dwellings" / p.name)

    summ = ov.run(work / "stoa", work / "public", work / "out", workers=8)
    out = work / "out"
    read = lambda n: set((out / f"{n}.txt").read_text().split())
    expect_hit = {"/".join(p.rsplit("-", 1)) for p in (Path(n).stem for n in on_stoa)}
    expect_test = {k for k in expect_hit if split_of(f"data/cubicasa5k/{k.replace('/', '-')}.png") == "test"}
    got_train, got_test, got_clean = read("public_in_stoa_train"), read("public_in_stoa_test"), read("public_not_in_stoa")
    rows = list(__import__("csv").DictReader(open(out / "matches.csv", encoding="utf-8")))
    wrong_pairs = [r for r in rows if r["name_agrees"] != "True"]

    # distance distributions: each public image vs its own Stoa image (best variant), and vs the
    # nearest wrong Stoa image, as max(p16, d16)
    sh = np.load(out / "hash_stoa.npy", allow_pickle=True).item()
    ph = np.load(out / "hash_public.npy", allow_pickle=True).item()
    sk = list(sh)
    true_d, near_wrong = [], []
    for k, h in ph.items():
        key = Path(k).parent.relative_to(work / "public").as_posix()
        own = str(work / "stoa" / "data" / "cubicasa5k" / (key.replace("/", "-") + ".png"))
        best_wrong, best_true = 999, None
        for orient, sv in (("plain", "plain"), ("mirror", "plain"), ("fplain", "fplain"), ("fmirror", "fplain")):
            s16 = np.stack([sh[x][sv]["p16"] for x in sk])
            sd = np.stack([sh[x][sv]["d16"] for x in sk])
            d = np.maximum(np.bitwise_count(s16 ^ h[orient]["p16"]).sum(1), np.bitwise_count(sd ^ h[orient]["d16"]).sum(1))
            for j, x in enumerate(sk):
                if x == own:
                    best_true = int(d[j]) if best_true is None else min(best_true, int(d[j]))
                else:
                    best_wrong = min(best_wrong, int(d[j]))
        near_wrong.append(best_wrong)
        if best_true is not None:
            true_d.append(best_true)
    res = {
        "expected_hits": len(expect_hit), "expected_test": len(expect_test),
        "got_train": len(got_train), "got_test": len(got_test), "got_clean": len(got_clean),
        "false_negatives": sorted(expect_hit - got_train - got_test),
        "false_positives": sorted((got_train | got_test) - expect_hit),
        "test_split_correct": got_test == expect_test,
        "pairs_to_wrong_image": len(wrong_pairs),
        "true_pair_distance_p50_p95_max": [float(np.percentile(true_d, q)) for q in (50, 95, 100)] if true_d else None,
        "nearest_wrong_distance_min_p5": [min(near_wrong), float(np.percentile(near_wrong, 5))],
        "summary": summ,
    }
    print(json.dumps(res, indent=1))
    ok = not res["false_negatives"] and not res["false_positives"] and res["test_split_correct"] and not wrong_pairs
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
