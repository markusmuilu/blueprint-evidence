"""Room-label reading on raster plans with a small local OCR model (RapidOCR, ONNX, CPU).

Text is read at 2x scale in all four orientations (CubiCasa plans often have labels written
along the vertical axis or upside down, and the OCR's own 180-degree classifier misses short
labels: an upside-down "MH" reads "HW"), and again on the left-right mirrored image, because some
plans in the set are mirrored ("MH" reads "HM", "ET" reads "T3"). Which family of reads to trust
is decided per image in labels.py.
Results are cached per rotation as JSON under out/ocr/ because OCR is the slow part.
Each token: {"text", "conf", "quad": [[x, y] x4] in original pixels, "rot"}.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import cv2

CACHE = Path(__file__).resolve().parents[2] / "out" / "ocr"
SCALE = 2.0
ROTATIONS = (0, 90, 180, 270)
PASSES = tuple((m, r) for m in (False, True) for r in ROTATIONS)


def cache_path(f: Path, rot: int, mirror: bool = False) -> Path:
    return CACHE / f"{Path(f).stem}.{'m' if mirror else 'r'}{rot}.json"


def cached(f: Path) -> bool:
    return all(cache_path(f, r, m).exists() for m, r in PASSES)


@lru_cache(maxsize=1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def _run(img, rot, mirror=False):
    h, w = img.shape[:2]
    sc = SCALE if not mirror else min(SCALE, max(1.0, 1800 / max(h, w)))   # mirrored passes: cheaper
    H, W = h * sc, w * sc
    im = cv2.resize(img, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
    if mirror:
        im = cv2.flip(im, 1)
    if rot == 90:
        im = cv2.rotate(im, cv2.ROTATE_90_CLOCKWISE)
    elif rot == 180:
        im = cv2.rotate(im, cv2.ROTATE_180)
    elif rot == 270:
        im = cv2.rotate(im, cv2.ROTATE_90_COUNTERCLOCKWISE)
    res, _ = _engine()(im)
    out = []
    for quad, txt, conf in res or []:
        pts = []
        for x, y in quad:
            if rot == 90:
                x, y = y, H - 1 - x
            elif rot == 180:
                x, y = W - 1 - x, H - 1 - y
            elif rot == 270:
                x, y = W - 1 - y, x
            if mirror:
                x = W - 1 - x
            pts.append([round(x / sc, 1), round(y / sc, 1)])
        out.append({"text": txt, "conf": round(float(conf), 3), "quad": pts, "rot": rot, "mirror": mirror})
    return out


def ocr_image(f: Path, allow_run: bool = True, partial: bool = False) -> list[dict] | None:
    toks, img = [], None
    for mirror, rot in PASSES:
        p = cache_path(f, rot, mirror)
        if p.exists():
            toks += json.loads(p.read_text(encoding="utf-8"))
            continue
        if not allow_run:
            if partial:
                continue
            return None
        if img is None:
            img = cv2.imread(str(f))
        t = _run(img, rot, mirror)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
        toks += t
    return toks
