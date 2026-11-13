"""CubiCasa5K annotation converter: model.svg -> boxes for toilet, sink, shower, bathtub, door, window.

Written against the published format (CubiCasa5K, Kalervo et al. 2019; the reference loader is
floortrans/loaders/house.py in the CubiCasa5K repository):

  <g id="Door"   class="Door Swing Beside">   <polygon points="x,y x,y ..."/> ... </g>
  <g id="Window" class="Window Regular">      <polygon points="..."/> ... </g>
  <g class="FixedFurnitureSet" transform="matrix(...)">           (optional parent)
    <g class="FixedFurniture Toilet" transform="matrix(a,b,c,d,e,f)">
      <g class="BoundaryPolygon"> <polygon points="..."/> | <rect x= y= width= height=/> </g>
      ...
    </g>
  </g>

Coordinates are in the pixel frame of F1_scaled.png. Transforms are composed down the whole
ancestor chain (proper SVG semantics, a superset of the reference loader). A door or window box is
the bounding box of its first polygon (the opening in the wall); a fixture box is its boundary
polygon (or rects) after transforms, falling back to all of its geometry.

Class mapping (CubiCasa furniture type -> detector class). Types not listed are ignored and
counted in `unknown`, so the mapping can be checked against real data:
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import numpy as np

CLASSES = ["toilet", "sink", "shower", "bathtub", "door", "window"]
FURNITURE = {
    "Toilet": "toilet",
    "Sink": "sink", "CornerSink": "sink", "DoubleSink": "sink", "DoubleSinkRight": "sink", "SideSink": "sink",
    "Bathtub": "bathtub", "BathtubRound": "bathtub",
    "Shower": "shower", "ShowerCab": "shower", "ShowerPlatform": "shower",
}
__doc__ += "\n" + "\n".join(f"  {k:16s} -> {v}" for k, v in FURNITURE.items()) + "\n"

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_transform(s: str | None) -> np.ndarray:
    """SVG transform attribute -> 3x3 matrix (matrix, translate, scale, rotate; applied left to right)."""
    M = np.eye(3)
    if not s:
        return M
    for name, args in re.findall(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)", s):
        v = [float(x) for x in re.findall(_NUM, args)]
        T = np.eye(3)
        if name == "matrix" and len(v) == 6:
            a, b, c, d, e, f = v
            T = np.array([[a, c, e], [b, d, f], [0, 0, 1.0]])
        elif name == "translate" and v:
            T[0, 2], T[1, 2] = v[0], (v[1] if len(v) > 1 else 0.0)
        elif name == "scale" and v:
            T[0, 0], T[1, 1] = v[0], (v[1] if len(v) > 1 else v[0])
        elif name == "rotate" and v:
            t = np.deg2rad(v[0])
            R = np.array([[np.cos(t), -np.sin(t), 0], [np.sin(t), np.cos(t), 0], [0, 0, 1.0]])
            if len(v) == 3:
                C, Ci = np.eye(3), np.eye(3)
                C[:2, 2], Ci[:2, 2] = (v[1], v[2]), (-v[1], -v[2])
                R = C @ R @ Ci
            T = R
        M = M @ T
    return M


def _points(el) -> np.ndarray:
    tag = _local(el.tag)
    if tag in ("polygon", "polyline"):
        v = [float(x) for x in re.findall(_NUM, el.get("points", ""))]
        return np.array(v[: len(v) // 2 * 2]).reshape(-1, 2)
    if tag == "rect":
        x, y = float(el.get("x", 0)), float(el.get("y", 0))
        w, h = float(el.get("width", 0)), float(el.get("height", 0))
        return np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])
    if tag == "line":
        return np.array([[float(el.get("x1", 0)), float(el.get("y1", 0))], [float(el.get("x2", 0)), float(el.get("y2", 0))]])
    if tag in ("circle", "ellipse"):
        cx, cy = float(el.get("cx", 0)), float(el.get("cy", 0))
        rx = float(el.get("r", el.get("rx", 0)))
        ry = float(el.get("r", el.get("ry", 0)))
        return np.array([[cx - rx, cy - ry], [cx + rx, cy + ry]])
    if tag == "path":
        v = [float(x) for x in re.findall(_NUM, el.get("d", ""))]
        if re.search(r"[a-z]", re.sub(r"e[-+]?\d", "", el.get("d", ""))):
            return np.zeros((0, 2))       # relative path commands: skip rather than guess
        return np.array(v[: len(v) // 2 * 2]).reshape(-1, 2)
    return np.zeros((0, 2))


def _apply(M, pts):
    if not len(pts):
        return pts
    h = np.hstack([pts, np.ones((len(pts), 1))]) @ M.T
    return h[:, :2]


def _geometry(el, M, only_first_polygon=False):
    """Transformed points of an element's geometry (descendants, with their own transforms)."""
    out = []

    def walk(e, T):
        T = T @ parse_transform(e.get("transform"))
        p = _points(e)
        if len(p):
            out.append(_apply(T, p))
            if only_first_polygon and _local(e.tag) == "polygon":
                return True
        for c in e:
            if walk(c, T):
                return True
        return False

    walk(el, M)
    return np.vstack(out) if out else np.zeros((0, 2))


def _classes(el) -> list[str]:
    return (el.get("class") or "").split()


def convert(svg_text: str):
    """Return (boxes, svg_size, unknown): boxes = [(class, x0, y0, x1, y1)] in SVG pixel units."""
    if "<!ENTITY" in svg_text[:5000] or "<!DOCTYPE" in svg_text[:5000]:
        raise ValueError("SVG with a DTD/entities: refused (untrusted input)")
    root = ET.fromstring(svg_text)
    size = None
    try:
        size = (float(re.findall(_NUM, root.get("width", ""))[0]), float(re.findall(_NUM, root.get("height", ""))[0]))
    except (IndexError, ValueError):
        if root.get("viewBox"):
            v = [float(x) for x in re.findall(_NUM, root.get("viewBox"))]
            size = (v[2], v[3]) if len(v) == 4 else None
    boxes, unknown = [], Counter()

    def walk(e, Mp):
        M = Mp @ parse_transform(e.get("transform"))
        cls = _classes(e)
        kind = None
        if _local(e.tag) == "g":
            if e.get("id") == "Door" or (cls and cls[0] == "Door"):
                kind = "door"
            elif e.get("id") == "Window" or (cls and cls[0] == "Window"):
                kind = "window"
            elif cls and cls[0] == "FixedFurniture" and len(cls) > 1:
                kind = FURNITURE.get(cls[1])
                if kind is None:
                    unknown[cls[1]] += 1
                    return
        if kind in ("door", "window"):
            # the opening polygon is the element's own first polygon child, in the parent frame
            # (the element's transform is already in M)
            first = next((c for c in e if _local(c.tag) == "polygon"), None)
            pts = _apply(M @ parse_transform(first.get("transform")), _points(first)) if first is not None \
                else _geometry(e, Mp)
            _add(kind, pts)
            return
        if kind:
            bp = next((c for c in e if _local(c.tag) == "g" and "BoundaryPolygon" in _classes(c)), None)
            pts = np.zeros((0, 2))
            if bp is not None:
                pts = _geometry(bp, M, only_first_polygon=any(_local(c.tag) == "polygon" for c in bp))
            if len(pts) < 2:
                pts = _geometry(e, Mp)
            _add(kind, pts)
            return
        for c in e:
            walk(c, M)

    def _add(kind, pts):
        if len(pts) >= 2:
            x0, y0 = pts.min(0)
            x1, y1 = pts.max(0)
            if x1 - x0 > 0.5 and y1 - y0 > 0.5:
                boxes.append((kind, float(x0), float(y0), float(x1), float(y1)))

    walk(root, np.eye(3))
    return boxes, size, unknown


def to_yolo(boxes, svg_size, img_size, min_px=2.0):
    """Boxes in SVG units -> YOLO label lines for an image of img_size (w, h)."""
    W, H = img_size
    sx, sy = (W / svg_size[0], H / svg_size[1]) if svg_size else (1.0, 1.0)
    lines = []
    for kind, x0, y0, x1, y1 in boxes:
        x0, x1 = max(0.0, x0 * sx), min(W, x1 * sx)
        y0, y1 = max(0.0, y0 * sy), min(H, y1 * sy)
        if x1 - x0 < min_px or y1 - y0 < min_px:
            continue
        lines.append(f"{CLASSES.index(kind)} {(x0 + x1) / 2 / W:.6f} {(y0 + y1) / 2 / H:.6f} "
                     f"{(x1 - x0) / W:.6f} {(y1 - y0) / H:.6f}")
    return lines


def counts(boxes) -> dict:
    c = Counter(k for k, *_ in boxes)
    return {k: c.get(k, 0) for k in CLASSES} | {"shower_or_bath": c.get("shower", 0) + c.get("bathtub", 0)}


if __name__ == "__main__":
    for f in sys.argv[1:]:
        b, size, unk = convert(Path(f).read_text(encoding="utf-8", errors="replace"))
        print(f, size, counts(b), dict(unk))
