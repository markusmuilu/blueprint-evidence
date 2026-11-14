"""Geometry of tiled inference and test-time augmentation (bpqa/tiling.py).

    python tests/test_tiling.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa import tiling  # noqa: E402


def test_views_map_back():
    h, w = 50, 80
    img = np.zeros((h, w), np.uint8)
    img[10:20, 30:45] = 1
    for k, m in tiling.DIHEDRAL:
        t = tiling.transform(img, k, m)
        ys, xs = np.nonzero(t)
        b = [xs.min(), ys.min(), xs.max() + 1, ys.max() + 1]
        assert [int(v) for v in tiling.untransform_box(b, k, m, w, h)] == [30, 10, 45, 20], (k, m)


def test_tiles_cover_and_end_at_edge():
    o = tiling.tile_origins(1800, 900, 1024, 192)
    assert o == [(0, 0), (776, 0)]
    assert tiling.tile_origins(500, 400, 1024, 192) == [(0, 0)]
    xs = sorted({x for x, _ in tiling.tile_origins(3000, 100, 1024, 192)})
    assert xs[-1] == 3000 - 1024 and all(b - a <= 1024 - 192 for a, b in zip(xs, xs[1:]))


def test_seam_merge():
    d = [{"cls": "door", "conf": 0.9, "xyxy": [0, 0, 10, 10]},
         {"cls": "door", "conf": 0.5, "xyxy": [0, 0, 6, 10]},     # the same door cut by a tile edge
         {"cls": "door", "conf": 0.5, "xyxy": [10, 0, 20, 10]},   # the next door, touching
         {"cls": "window", "conf": 0.5, "xyxy": [0, 0, 6, 10]}]   # another class is never merged
    out = tiling.merge_seams(d)
    assert len(out) == 3 and out[0]["n_merged"] == 2


def test_fuse_views():
    a = {"cls": "sink", "conf": 0.8, "xyxy": [0, 0, 10, 10]}
    b = {"cls": "sink", "conf": 0.6, "xyxy": [1, 0, 11, 10]}
    c = {"cls": "sink", "conf": 0.4, "xyxy": [50, 50, 60, 60]}
    out = tiling.fuse_views([[a, c], [b]])
    assert len(out) == 2
    one = [o for o in out if o["xyxy"][0] < 20][0]
    assert one["views"] == 2 and abs(one["conf"] - 0.7) < 1e-6
    lone = [o for o in out if o["xyxy"][0] >= 20][0]
    assert lone["views"] == 1 and abs(lone["conf"] - 0.2) < 1e-6


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
