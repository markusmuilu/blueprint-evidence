"""Which public CubiCasa5K plans also appear in Stoa's dataset, by perceptual image hash.

    python tools/overlap.py --stoa ../data/stoa-full/junction-dataset --public ../data/cubicasa5k-public/cubicasa5k
                            --out out/overlap

Stoa side: every raster image under <stoa>/data/<source>/ (any source folder), assigned to our
train (dev) or test split with the same function and seed as the preview (bpqa.data.split_of).
Public side: every raster image under the CubiCasa5K root, grouped by plan folder
(e.g. high_quality_architectural/2003, which holds F1_original.png and F1_scaled.png).

Matching is robust to resizing, recompression, white margins and left-right mirroring:
each image is cropped to its non-white content, then hashed with pHash (DCT) and dHash
(gradient) at 8x8 and 16x16. The public image is hashed as is and mirrored. A pair matches when
the 64-bit pHash is within a loose prefilter and both 256-bit hashes are within their thresholds
and the content aspect ratios agree. Thresholds were set on the preview with resized, recompressed
and mirrored copies (tools/test_overlap.py).

Output in --out:
  public_in_stoa_train.txt   public plan keys whose image is in a Stoa train-split image
  public_in_stoa_test.txt    public plan keys whose image is in a Stoa test-split image (never train on these)
  public_not_in_stoa.txt     public plan keys with no match anywhere in Stoa's data
  train_clean.txt            plans that may be used for training: not_in_stoa + in_stoa_train
  matches.csv                every matched pair with distances and whether the file names agree
  summary.json               counts
A public plan that matches any test image is test, whatever else it matches. Images are only
decoded (PIL), never executed.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.data import split_of  # noqa: E402

Image.MAX_IMAGE_PIXELS = 300_000_000
IMG_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
PRE_T = 18          # 64-bit pHash prefilter
P16_T = 60          # 256-bit pHash
D16_T = 60          # 256-bit dHash
ASPECT_TOL = 0.06

_DCT = {}


def _dct_mat(n):
    if n not in _DCT:
        k = np.arange(n)
        m = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n)) * np.sqrt(2 / n)
        m[0] /= np.sqrt(2)
        _DCT[n] = m
    return _DCT[n]


def load_gray(path, work=512, crop=True):
    """Grayscale, downscaled so the long side is about `work` px, optionally cropped to its content."""
    im = Image.open(path)
    im.draft("L", (work * 2, work * 2))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    im = im.convert("L")
    f = max(1, max(im.size) // (work * 2))
    if f > 1:
        im = im.reduce(f)
    a = np.asarray(im)
    if not crop:
        return a
    # crop to the drawn content: dark pixels, ignoring the outermost 0.1% (specks, JPEG ringing)
    ys, xs = np.where(a < 200)
    if len(xs) > 200:
        y0, y1 = np.quantile(ys, [0.001, 0.999]).astype(int)
        x0, x1 = np.quantile(xs, [0.001, 0.999]).astype(int)
        a = a[y0:y1 + 1, x0:x1 + 1]
    return a


def _resize(a, w, h):
    return np.asarray(Image.fromarray(a).resize((w, h), Image.LANCZOS), dtype=np.float64)


def phash(a, hs):
    n = hs * 4
    d = _dct_mat(n)
    c = d @ _resize(a, n, n) @ d.T
    low = c[:hs, :hs].ravel()
    return low > np.median(low[1:])


def dhash(a, hs):
    r = _resize(a, hs + 1, hs)
    return (r[:, 1:] > r[:, :-1]).ravel()


def hashes(a):
    return {"p8": np.packbits(phash(a, 8)), "p16": np.packbits(phash(a, 16)), "d16": np.packbits(dhash(a, 16))}


def hash_file(path):
    """Two variants, each as is and mirrored: cropped to content (robust to margins) and the full
    frame (robust when the crop is unstable, e.g. light-grey fills near the threshold)."""
    try:
        full = load_gray(path, crop=False)
        a = load_gray(path)
    except Exception as ex:      # unreadable or truncated file: report, do not stop
        return path, None, f"{type(ex).__name__}: {ex}"
    return path, {"aspect": a.shape[1] / max(a.shape[0], 1), "plain": hashes(a), "mirror": hashes(a[:, ::-1]),
                  "faspect": full.shape[1] / max(full.shape[0], 1), "fplain": hashes(full),
                  "fmirror": hashes(full[:, ::-1])}, None


def hash_all(paths, cache_file: Path, workers):
    cache = {}
    if cache_file.exists():
        cache = {k: v for k, v in np.load(cache_file, allow_pickle=True).item().items()}
    todo = []
    for p in paths:
        st = os.stat(p)
        if cache.get(str(p), {}).get("key") != (st.st_size, int(st.st_mtime)):
            todo.append(p)
    errors = {}
    if todo:
        print(f"  hashing {len(todo)} images ({len(paths) - len(todo)} cached)", file=sys.stderr)
        with ProcessPoolExecutor(workers) as ex:
            for i, (p, h, err) in enumerate(ex.map(hash_file, todo, chunksize=16)):
                if h is None:
                    errors[str(p)] = err
                    continue
                st = os.stat(p)
                h["key"] = (st.st_size, int(st.st_mtime))
                cache[str(p)] = h
                if (i + 1) % 1000 == 0:
                    print(f"  {i + 1}/{len(todo)}", file=sys.stderr)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache_file, np.array(cache, dtype=object), allow_pickle=True)
    return {str(p): cache[str(p)] for p in paths if str(p) in cache}, errors


def _stack(hs, kind, orient):
    return np.stack([h[orient][kind] for h in hs]) if hs else np.zeros((0, 1), np.uint8)


def ham(a, b):
    """Hamming distance matrix between packed bit rows a (n, k) and b (m, k)."""
    return np.bitwise_count(a[:, None, :] ^ b[None, :, :]).sum(-1, dtype=np.int32)


def match(stoa: dict, public: dict, pre_t=PRE_T, p16_t=P16_T, d16_t=D16_T, aspect_tol=ASPECT_TOL):
    """Return [(public_path, stoa_path, orient, d_p8, d_p16, d_d16)] for every matching pair."""
    sk, pk = list(stoa), list(public)
    sh, ph = [stoa[k] for k in sk], [public[k] for k in pk]
    found = set()
    out = []
    for orient, sv, asp in (("plain", "plain", "aspect"), ("mirror", "plain", "aspect"),
                            ("fplain", "fplain", "faspect"), ("fmirror", "fplain", "faspect")):
        s_asp = np.array([h[asp] for h in sh])
        s8, s16, sd = _stack(sh, "p8", sv), _stack(sh, "p16", sv), _stack(sh, "d16", sv)
        p8, p16, pd = _stack(ph, "p8", orient), _stack(ph, "p16", orient), _stack(ph, "d16", orient)
        p_asp = np.array([h[asp] for h in ph])
        for i0 in range(0, len(pk), 512):
            sl = slice(i0, i0 + 512)
            d8 = ham(p8[sl], s8)
            ii, jj = np.where(d8 <= pre_t)
            for i, j in zip(ii, jj):
                gi = i0 + i
                if abs(p_asp[gi] - s_asp[j]) > aspect_tol * max(p_asp[gi], s_asp[j]):
                    continue
                e16 = int(np.bitwise_count(p16[gi] ^ s16[j]).sum())
                ed = int(np.bitwise_count(pd[gi] ^ sd[j]).sum())
                if e16 <= p16_t and ed <= d16_t and (pk[gi], sk[j]) not in found:
                    found.add((pk[gi], sk[j]))
                    out.append((pk[gi], sk[j], orient, int(d8[i, j]), e16, ed))
    return out


def plan_key(public_root: Path, p: Path) -> str:
    rel = p.relative_to(public_root).as_posix()
    return rel.rsplit("/", 1)[0] if "/" in rel else rel


def name_agrees(key: str, stoa_rel: str) -> bool:
    """Stoa renamed CubiCasa files <category>/<id>/F1_*.png to <category>-<id>.png."""
    m = re.search(r"([a-z_]+)/(\d+)$", key)
    return bool(m) and Path(stoa_rel).stem == f"{m.group(1)}-{m.group(2)}"


def list_images(root: Path, sub=""):
    base = root / sub if sub else root
    return sorted(p for p in base.rglob("*") if p.suffix.lower() in IMG_EXT and p.is_file())


def run(stoa_root: Path, public_root: Path, out: Path, workers=8, sources=None):
    out.mkdir(parents=True, exist_ok=True)
    stoa_imgs = [p for p in list_images(stoa_root, "data")
                 if sources is None or p.relative_to(stoa_root / "data").parts[0] in sources]
    pub_imgs = list_images(public_root)
    print(f"stoa images: {len(stoa_imgs)}  public images: {len(pub_imgs)}", file=sys.stderr)
    sh, serr = hash_all(stoa_imgs, out / "hash_stoa.npy", workers)
    ph, perr = hash_all(pub_imgs, out / "hash_public.npy", workers)
    pairs = match(sh, ph)

    srel = {str(p): p.relative_to(stoa_root).as_posix() for p in stoa_imgs}
    plans = sorted({plan_key(public_root, p) for p in pub_imgs})
    hit = defaultdict(set)
    rows = []
    for pp, sp, orient, d8, d16, dd in pairs:
        key = plan_key(public_root, Path(pp))
        rel = srel[sp]
        sp_split = "train" if split_of(rel) == "dev" else "test"
        hit[key].add(sp_split)
        rows.append({"public_plan": key, "public_file": Path(pp).name, "stoa_file": rel, "stoa_split": sp_split,
                     "orient": orient, "d_p8": d8, "d_p16": d16, "d_d16": dd, "name_agrees": name_agrees(key, rel)})
    test = sorted(k for k in plans if "test" in hit.get(k, ()))
    train = sorted(k for k in plans if hit.get(k) == {"train"})
    clean = sorted(k for k in plans if k not in hit)
    for name, lst in [("public_in_stoa_train", train), ("public_in_stoa_test", test),
                      ("public_not_in_stoa", clean), ("train_clean", sorted(clean + train))]:
        (out / f"{name}.txt").write_text("".join(k + "\n" for k in lst), encoding="utf-8")
    with open(out / "matches.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["public_plan"])
        w.writeheader()
        w.writerows(rows)

    stoa_cubi = [p for p in stoa_imgs if "cubicasa" in srel[str(p)]]
    matched_stoa = {r["stoa_file"] for r in rows}
    by_name = {Path(srel[str(p)]).stem for p in stoa_cubi}
    name_only = [k for k in plans if re.search(r"([a-z_]+)/(\d+)$", k)
                 and "-".join(re.search(r"([a-z_]+)/(\d+)$", k).groups()) in by_name and k not in hit]
    summary = {
        "stoa_images": len(stoa_imgs), "stoa_cubicasa_images": len(stoa_cubi),
        "stoa_cubicasa_train": sum(split_of(srel[str(p)]) == "dev" for p in stoa_cubi),
        "stoa_cubicasa_test": sum(split_of(srel[str(p)]) == "test" for p in stoa_cubi),
        "public_images": len(pub_imgs), "public_plans": len(plans),
        "public_plans_in_stoa": len(hit), "in_stoa_train": len(train), "in_stoa_test": len(test),
        "not_in_stoa": len(clean), "train_clean": len(clean) + len(train),
        "stoa_images_matched": len(matched_stoa),
        "stoa_cubicasa_unmatched": len({srel[str(p)] for p in stoa_cubi} - matched_stoa),
        "matched_pairs": len(rows), "pairs_name_agrees": sum(r["name_agrees"] for r in rows),
        "pairs_mirrored": sum("mirror" in r["orient"] for r in rows),
        "plans_name_matches_but_no_hash_match": len(name_only),
        "unreadable": {"stoa": len(serr), "public": len(perr)},
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    if serr or perr:
        (out / "unreadable.json").write_text(json.dumps({"stoa": serr, "public": perr}, indent=1), encoding="utf-8")
    if name_only:
        (out / "name_match_no_hash.txt").write_text("".join(k + "\n" for k in name_only), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stoa", required=True, help="Stoa dataset root (folder with data/<source>/)")
    ap.add_argument("--public", required=True, help="CubiCasa5K root (folder with colorful/, high_quality/ ...)")
    ap.add_argument("--out", default="out/overlap")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--sources", default="", help="only these Stoa source folders (default: all)")
    a = ap.parse_args()
    run(Path(a.stoa), Path(a.public), Path(a.out), a.workers, set(a.sources.split(",")) if a.sources else None)


if __name__ == "__main__":
    main()
