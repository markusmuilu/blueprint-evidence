"""Geometry shared by the tiled detector dataset, tiled inference and test-time augmentation.

Scale rule: an image is resized by a fixed factor relative to its native pixels, with the long side
capped, the same in training (tools/build_tile_dataset.py) and at inference (routes/detector.py).
Tiles are square, overlap by a fixed margin, and the last row and column are moved in so that they
end at the image edge.
"""
from __future__ import annotations

import numpy as np


def scale_for(w: int, h: int, scale: float, max_side: int) -> float:
    return min(scale, max_side / max(w, h))


def _starts(n: int, t: int, ov: int) -> list[int]:
    if n <= t:
        return [0]
    step = t - ov
    xs = list(range(0, n - t + 1, step))
    if xs[-1] != n - t:
        xs.append(n - t)
    return xs


def tile_origins(w: int, h: int, t: int, ov: int) -> list[tuple[int, int]]:
    return [(x, y) for y in _starts(h, t, ov) for x in _starts(w, t, ov)]


def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def merge_seams(dets: list[dict], iou: float = 0.5, iomin: float = 0.6) -> list[dict]:
    """Merge boxes of one class found twice where tiles overlap.

    A box cut by a tile edge lies mostly inside the whole box from the neighbouring tile, so two boxes
    of the same class are one object when their IoU >= `iou` or the smaller lies `iomin` inside the
    larger. The merged box is the higher-scored one, grown to the union. Adjacent windows that share an
    edge overlap far less than that and stay separate.
    """
    out: list[dict] = []
    for d in sorted(dets, key=lambda d: -d["conf"]):
        b = d["xyxy"]
        for o in out:
            if o["cls"] != d["cls"]:
                continue
            ob = o["xyxy"]
            i = _inter(b, ob)
            if i <= 0:
                continue
            u = _area(b) + _area(ob) - i
            if i / max(u, 1e-6) >= iou or i / max(min(_area(b), _area(ob)), 1e-6) >= iomin:
                o["xyxy"] = [min(b[0], ob[0]), min(b[1], ob[1]), max(b[2], ob[2]), max(b[3], ob[3])]
                o["n_merged"] = o.get("n_merged", 1) + 1
                break
        else:
            out.append(dict(d, xyxy=list(b)))
    return out


# ---------------------------------------------------------------- test-time augmentation

# the eight symmetries of the square: (k quarter turns counter-clockwise, then mirror left-right)
DIHEDRAL = [(k, m) for m in (False, True) for k in range(4)]


def transform(img: np.ndarray, k: int, mirror: bool) -> np.ndarray:
    out = np.rot90(img, k)
    if mirror:
        out = out[:, ::-1]
    return np.ascontiguousarray(out)


def untransform_box(b, k: int, mirror: bool, w: int, h: int):
    """Map a box found on transform(img, k, mirror) back to the original w x h image."""
    x0, y0, x1, y1 = b
    # size of the rotated image (before the mirror)
    rw, rh = (w, h) if k % 2 == 0 else (h, w)
    if mirror:
        x0, x1 = rw - x1, rw - x0
    pts = [(x0, y0), (x1, y1)]
    for _ in range(k % 4):
        # undo one counter-clockwise quarter turn (continuous coordinates): a point (x, y) of the
        # turned image came from (ch - y, x) in the image before the turn, whose width is ch
        cw, ch = rw, rh
        pts = [(ch - y, x) for x, y in pts]
        rw, rh = ch, cw
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def iou(a, b) -> float:
    i = _inter(a, b)
    return i / max(_area(a) + _area(b) - i, 1e-6)


def fuse_views(views: list[list[dict]], match_iou: float = 0.45) -> list[dict]:
    """Fuse detections from several augmented views into one list.

    Boxes of one class from different views that overlap (IoU >= match_iou) are one object; its fused
    score is the sum of its scores divided by the number of views (a view that missed it adds 0), so
    an object every view finds keeps its score and one found by a single view is discounted. Each
    fused box carries `views` (how many views found it) and `spread` (max - min score among them).
    """
    nv = max(1, len(views))
    clusters: list[dict] = []
    flat = sorted(((vi, d) for vi, ds in enumerate(views) for d in ds), key=lambda t: -t[1]["conf"])
    for vi, d in flat:
        best, bi = None, match_iou
        for c in clusters:
            if c["cls"] != d["cls"] or vi in c["vs"]:
                continue
            v = iou(c["xyxy"], d["xyxy"])
            if v >= bi:
                best, bi = c, v
        if best is None:
            clusters.append({"cls": d["cls"], "xyxy": list(d["xyxy"]), "vs": {vi}, "scores": [d["conf"]],
                             "boxes": [d["xyxy"]]})
        else:
            best["vs"].add(vi)
            best["scores"].append(d["conf"])
            best["boxes"].append(d["xyxy"])
            wts = np.array(best["scores"])
            bx = np.array(best["boxes"])
            best["xyxy"] = [float(v) for v in (bx * wts[:, None]).sum(0) / wts.sum()]
    out = []
    for c in clusters:
        out.append({"cls": c["cls"], "conf": round(sum(c["scores"]) / nv, 4),
                    "xyxy": [round(v, 1) for v in c["xyxy"]], "views": len(c["vs"]),
                    "spread": round(max(c["scores"]) - min(c["scores"]) if len(c["vs"]) == nv else max(c["scores"]), 4)})
    return out
