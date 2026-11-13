"""Sanitary-fixture candidates on raster plans: enclosed white regions of fixture-like shape.

A toilet bowl, a washbasin or a bathtub is drawn as a closed outline, so its inside is a white
region not connected to the room around it. Candidates are those regions; a small classifier
trained on hand-labelled dev crops decides what each one is.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np


def binarize(img_bgr):
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    return (g < 170).astype(np.uint8)


def stroke_width(ink):
    """Median horizontal run length of ink, a scale reference for size limits."""
    runs = []
    for row in ink[:: max(1, ink.shape[0] // 300)]:
        d = np.diff(np.concatenate([[0], row, [0]]))
        s, e = np.where(d == 1)[0], np.where(d == -1)[0]
        runs.extend((e - s).tolist())
    return float(np.median(runs)) if runs else 2.0


def _touching(inv):
    """Number of components of the inverse mask that touch the crop border (the outside)."""
    n, lab = cv2.connectedComponents(inv.astype(np.uint8))
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    return len(border)


def candidates(img_bgr):
    ink = binarize(img_bgr)
    h, w = ink.shape
    sw = max(1.0, stroke_width(ink))
    white = (1 - ink).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(white, connectivity=4)
    out = []
    min_side = max(6, 3 * sw)
    max_area = 0.015 * h * w
    for i in range(1, n):
        x, y, bw, bh, a = (int(v) for v in st[i])
        if a > max_area or min(bw, bh) < min_side or max(bw, bh) > 0.25 * max(h, w):
            continue
        if x == 0 or y == 0 or x + bw >= w or y + bh >= h:
            continue
        if max(bw, bh) / min(bw, bh) > 3.5:
            continue
        mask = (lab[y:y + bh, x:x + bw] == i).astype(np.uint8)
        cs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        c = max(cs, key=cv2.contourArea)
        hull = cv2.convexHull(c)
        solidity = a / max(cv2.contourArea(hull), 1)
        if solidity < 0.8 or a / (bw * bh) < 0.5:
            continue
        holes = len(cs) - 1 + int(cv2.connectedComponents(1 - mask)[0] - 1 - _touching(1 - mask))
        out.append({"xywh": (x, y, bw, bh), "area": a, "solidity": round(solidity, 2), "holes": min(holes, 3),
                    "fill": round(a / (bw * bh), 2), "sw": sw})
    return out


def crop(img_bgr, xywh, size=48, pad=0.6):
    """Square crop around a candidate, padded by `pad` of its larger side, resized to size x size gray."""
    x, y, bw, bh = xywh
    s = max(bw, bh) * (1 + 2 * pad)
    cx, cy = x + bw / 2, y + bh / 2
    x0, y0 = int(round(cx - s / 2)), int(round(cy - s / 2))
    x1, y1 = int(round(cx + s / 2)), int(round(cy + s / 2))
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    H, W = g.shape
    pad_img = cv2.copyMakeBorder(g, max(0, -y0), max(0, y1 - H), max(0, -x0), max(0, x1 - W),
                                 cv2.BORDER_CONSTANT, value=255)
    oy, ox = max(0, -y0), max(0, -x0)
    c = pad_img[y0 + oy:y1 + oy, x0 + ox:x1 + ox]
    return cv2.resize(c, (size, size), interpolation=cv2.INTER_AREA), (x0, y0, x1, y1)


def hog(g, cell=8, bins=9):
    """Small HOG: unsigned gradient orientation histograms per cell, 2x2-block L2 normalised."""
    g = g.astype(np.float32) / 255.0
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=1)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=1)
    mag = np.hypot(gx, gy)
    ang = (np.arctan2(gy, gx) % np.pi) / np.pi * bins
    b0 = np.floor(ang).astype(int) % bins
    frac = ang - np.floor(ang)
    n = g.shape[0] // cell
    H = np.zeros((n, n, bins), np.float32)
    cy, cx = np.indices(g.shape) // cell
    np.add.at(H, (cy, cx, b0), mag * (1 - frac))
    np.add.at(H, (cy, cx, (b0 + 1) % bins), mag * frac)
    blocks = []
    for i in range(n - 1):
        for j in range(n - 1):
            v = H[i:i + 2, j:j + 2].ravel()
            blocks.append(v / (np.linalg.norm(v) + 1e-3))
    return np.concatenate(blocks)
ORIENTS = 8   # 4 rotations x mirror, used for training augmentation and test-time averaging


def _orient(g, k):
    if k >= 4:
        g = cv2.flip(g, 1)
    return np.ascontiguousarray(np.rot90(g, k % 4))


def features(img_bgr, cands):
    """Per candidate and orientation: HOG of a tight and a wide crop, plus scale-free shape numbers.

    Returns float32 array [n_candidates, ORIENTS, D].
    """
    h, w = img_bgr.shape[:2]
    out = []
    for c in cands:
        x, y, bw, bh = c["xywh"]
        sw = c["sw"]
        geo = np.array([np.log(c["area"] / sw ** 2), np.log(max(bw, bh) / min(bw, bh)), c["fill"], c["solidity"],
                        np.log(c["area"] / (h * w)), c.get("holes", 0), np.log(sw)], np.float32)
        tight, _ = crop(img_bgr, c["xywh"], size=32, pad=0.3)
        wide, _ = crop(img_bgr, c["xywh"], size=32, pad=1.2)
        rows = []
        for k in range(ORIENTS):
            f1 = hog(_orient(tight, k))
            f2 = hog(_orient(wide, k))
            rows.append(np.concatenate([f1, f2, geo]))
        out.append(rows)
    return np.asarray(out, np.float32).reshape(len(cands), ORIENTS, -1)


MODELS = Path(__file__).resolve().parents[2] / "models"


@lru_cache(maxsize=4)
def load_model(name: str):
    p = MODELS / name
    if not p.exists():
        return None
    z = np.load(p)
    return {k: z[k] for k in z.files}


def score(model, X):
    """Numpy forward pass of the MLP trained by tools/train_mil.py; mean over the 8 orientations.
    X: [n, ORIENTS, D] -> scores [n, heads]."""
    x = (X.astype(np.float32) - model["mu"]) / model["sd"]
    h = np.maximum(x @ model["W1"].T + model["b1"], 0)
    s = 1 / (1 + np.exp(-(h @ model["W2"].T + model["b2"])))
    return s.mean(1)
