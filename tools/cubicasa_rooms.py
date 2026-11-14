"""CubiCasa5K room converter: model.svg -> room polygons typed as Stoa's room question types.

Rooms are the `<g class="Space ...">` groups (their first polygon is the room outline, in the
F1_scaled.png pixel frame, like tools/cubicasa_svg.py). Each room also gets the fixed furniture
whose centre lies inside it (toilet, sink, shower, bathtub, sauna stove, sauna bench), because
Stoa's WC / bathroom / sauna counts follow the room's content, not only its class name.

Mapping to Stoa types (checked against Stoa's train-split answers by tools/build_rooms_dataset.py,
count_check.json; agreement on train-split images: bedroom 2003/2003, kitchen 2217/2217, bathroom
1642/1642, WC 1606/1607, sauna 2220/2239, balcony 989/991):
  Space Bath* named "WC"                  -> wc_room
  Space Bath* otherwise (KH, KPH, PH ...) -> bathroom
  Space Bedroom*                          -> bedroom
  Space Kitchen* (kitchenette, open ...)  -> kitchen
  Space Outdoor Balcony* (incl. glazed)   -> balcony
  Space Sauna, or any other space holding a sauna stove -> sauna
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.cubicasa_svg import _apply, _classes, _geometry, _local, _points, parse_transform  # noqa: E402

ROOM_TYPES = ["kitchen", "bathroom", "wc_room", "sauna", "bedroom", "balcony"]
FIXTURE_KIND = {"Toilet": "toilet", "Shower": "shower", "Bathtub": "bathtub", "Jacuzzi": "bathtub",
                "SaunaStove": "stove", "SaunaStoveRound": "stove", "SaunaBench": "bench", "SaunaBenchHigh": "bench",
                "SaunaBenchMid": "bench", "SaunaBenchLow": "bench",
                "Sink": "sink", "RoundSink": "sink", "CornerSink": "sink", "DoubleSink": "sink",
                "DoubleSinkRight": "sink", "SideSink": "sink", "ShowerCab": "showercab"}


def _fixture_kind(cls: list[str]) -> str | None:
    for c in cls[1:]:
        if c in FIXTURE_KIND:
            return FIXTURE_KIND[c]
    return None


def _name(space) -> str:
    """The space's NameLabel text (the room name printed on the plan, e.g. "WC", "KH", "MH 2")."""
    for g in space.iter():
        if _local(g.tag) == "g" and "NameLabel" in _classes(g):
            return " ".join("".join(t.itertext()).strip() for t in g.iter() if _local(t.tag) == "text").strip()
    return ""


def parse(svg_text: str):
    """Return (rooms, fixtures): rooms = [{"cls": "Space Bath Shower", "poly": Nx2}],
    fixtures = [{"kind", "c": (x, y)}], both in SVG (F1_scaled) pixels."""
    if "<!ENTITY" in svg_text[:5000] or "<!DOCTYPE" in svg_text[:5000]:
        raise ValueError("SVG with a DTD/entities: refused (untrusted input)")
    root = ET.fromstring(svg_text)
    rooms, fixtures = [], []

    def walk(e, Mp):
        M = Mp @ parse_transform(e.get("transform"))
        cls = _classes(e)
        if _local(e.tag) == "g" and cls and cls[0] == "Space":
            first = next((c for c in e if _local(c.tag) == "polygon"), None)
            if first is not None:
                pts = _apply(M @ parse_transform(first.get("transform")), _points(first))
                if len(pts) >= 3:
                    rooms.append({"cls": " ".join(cls), "poly": pts, "name": _name(e)})
        if _local(e.tag) == "g" and cls and cls[0] == "FixedFurniture":
            k = _fixture_kind(cls)
            if k:
                pts = _geometry(e, Mp)
                if len(pts) >= 2:
                    fixtures.append({"kind": k, "c": tuple(((pts.min(0) + pts.max(0)) / 2).tolist())})
            return
        for c in e:
            walk(c, M)

    walk(root, np.eye(3))
    return rooms, fixtures


def _inside(pt, poly) -> bool:
    x, y = pt
    n, inside = len(poly), False
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / ((y1 - y0) or 1e-12) + x0:
            inside = not inside
    return inside


def area(poly) -> float:
    x, y = poly[:, 0], poly[:, 1]
    return float(abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))) / 2)


def room_type(cls: str, content: Counter, name: str = "") -> str | None:
    """Stoa room type of one CubiCasa space, from its class, its name label and the fixtures inside it."""
    c = cls.split()
    if len(c) < 2:
        return None
    if c[1] == "Bath":                       # WC rooms are Bath spaces named "WC"; KH, KPH, PH, PESUH ... are bathrooms
        return "wc_room" if name.strip().upper() == "WC" else "bathroom"
    if c[1] == "Bedroom":
        return "bedroom"
    if c[1] == "Kitchen":                    # Kitchen, Kitchen Kitchenette, Kitchen Open, Kitchen Scullery
        return "kitchen"
    if c[1] == "Outdoor" and len(c) > 2 and c[2] == "Balcony":     # incl. Balcony Glazed
        return "balcony"
    if c[1] == "Sauna" or content["stove"]:  # most saunas are "Space Undefined" holding a sauna stove
        return "sauna"
    return None


def typed_rooms(svg_text: str):
    """[(type or None, cls, poly, content Counter)] for every space in the plan."""
    rooms, fixtures = parse(svg_text)
    out = []
    for r in rooms:
        content = Counter(f["kind"] for f in fixtures if _inside(f["c"], r["poly"]))
        out.append((room_type(r["cls"], content, r["name"]), r["cls"], r["poly"], content))
    return out


def counts(typed) -> dict:
    c = Counter(t for t, *_ in typed if t)
    return {k: c.get(k, 0) for k in ROOM_TYPES}


if __name__ == "__main__":
    for f in sys.argv[1:]:
        t = typed_rooms(Path(f).read_text(encoding="utf-8", errors="replace"))
        print(f, counts(t))
        for typ, cls, poly, content in t:
            print("   ", typ, cls, dict(content), round(area(poly)))
