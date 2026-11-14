"""Perceptual check that the unseen images are not in Stoa's dataset (dHash, Hamming distance).

    python tools/unseen_dedupe.py --unseen ../data/unseen --stoa ../data/stoa-full/junction-dataset/data
"""
import argparse
import glob
import os

import numpy as np
import pymupdf
from PIL import Image

Image.MAX_IMAGE_PIXELS = None


def dhash(im, n=16):
    g = np.asarray(im.convert("L").resize((n + 1, n), Image.BILINEAR), dtype=np.int16)
    return (g[:, 1:] > g[:, :-1]).flatten()


ap = argparse.ArgumentParser()
ap.add_argument("--unseen", required=True)
ap.add_argument("--stoa", required=True)
a = ap.parse_args()
mine = {}
for p in glob.glob(os.path.join(a.unseen, "wm_*", "*")):
    if p.endswith(".md"):
        continue
    if p.endswith(".pdf"):
        pix = pymupdf.open(p)[0].get_pixmap(dpi=40)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    else:
        im = Image.open(p)
        if im.mode == "RGBA":
            bg = Image.new("RGB", im.size, "white")
            bg.paste(im, mask=im.split()[3])
            im = bg
    mine[os.path.basename(p)] = dhash(im)
best = {k: (999, "") for k in mine}
n = 0
for src in ("wikimedia", "habs", "finna", "europeana", "cubicasa5k"):
    for p in glob.glob(os.path.join(a.stoa, src, "*")):
        if not p.lower().endswith((".jpg", ".jpeg", ".png")):
            continue
        try:
            with Image.open(p) as im:
                im.draft("RGB", (128, 128))
                h = dhash(im.convert("RGB"))
        except Exception:
            continue
        n += 1
        for k, v in mine.items():
            d = int((v != h).sum())
            if d < best[k][0]:
                best[k] = (d, p)
print("compared against", n, "Stoa images; nearest distance of 256 bits (below ~25 would be a duplicate):")
for k, (d, p) in best.items():
    print(f"  {k}: {d}  {os.path.basename(p)}")
