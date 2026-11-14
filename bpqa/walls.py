"""Wall-surface takeoff: tiles (or paint) on the walls of rooms, from room outlines, heights and openings.

    net wall area of a room = perimeter x tiled height - door and window openings on that room's walls

Inputs and where they come from (each one is stated in the answer, with its source):

  perimeter     the room outline: IfcSpace geometry projected to the floor (IFC), ROOM_OUTLINE polygon (DXF).
                Holes (columns, shafts inside the room) add their ring length. An open side of a room (no wall)
                is counted too; the IFC answer notes how much of the outline runs along walls.
  height        IFC: the space's own height quantity (FinishCeilingHeight, ClearHeight, Height), else the
                height of the space geometry, else the storey height; drawings: an ASSUMED ceiling height
                (2.50 m unless the question gives one), never hidden.
  tiled height  full room height unless the question says otherwise ("tiles to 1.2 m").
  openings      doors and windows whose plan footprint touches the room outline. IFC: width x the part of
                the opening's own vertical extent that lies inside the tiled band (so a window above a
                1.2 m tile line deducts nothing). Drawings: door width from the leaf, door height ASSUMED
                2.00 m; window width from the drawing, ASSUMED 1.20 m high on a 0.90 m sill.
  waste         10% cutting allowance unless the question gives one ("with 15% waste", "no waste").

The answer lists floor, walls, waste and the order quantity as separate lines. The value is the order
quantity (with waste) when the question asks what to order / buy / need, else the net area.
"""
from __future__ import annotations

import os
import re

from .query import fold

DEFAULT_CEILING_M = float(os.environ.get("TAKEOFF_CEILING_M", "2.5"))
DEFAULT_WASTE_PCT = float(os.environ.get("TAKEOFF_WASTE_PCT", "10"))
DOOR_H_M = 2.0
WINDOW_H_M, WINDOW_SILL_M = 1.2, 0.9
WET = {"bathroom", "wc", "sauna"}

_NUM = r"(\d+(?:[.,]\d+)?)"


def _f(s: str) -> float:
    return float(s.replace(",", "."))


def parse_inputs(question: str) -> dict:
    """Editable inputs stated in the question: ceiling height, tiled height, waste %, order intent."""
    f = fold(question)
    out = {}
    m = re.search(rf"(?:ceiling|room|wall)?\s*heights?\s*(?:of|is|=|:)?\s*{_NUM}\s*(m|cm|mm)\b", f) or \
        re.search(rf"{_NUM}\s*(m|cm|mm)\s*(?:high|tall|ceilings?|ceiling height|korkea|korkuinen)", f) or \
        re.search(rf"huonekorkeus\s*(?:on)?\s*{_NUM}\s*(m|cm|mm)", f)
    if m:
        v = _f(m.group(1)) / {"m": 1, "cm": 100, "mm": 1000}[m.group(2)]
        if 1.5 <= v <= 12:
            out["ceiling_m"] = v
    m = re.search(rf"(?:tiles?|tiling|tiled|laatoitus|laatat?)\s*(?:up\s*)?(?:to|until|asti|korkeuteen)\s*(?:a\s*height\s*of\s*)?{_NUM}\s*(m|cm|mm)", f) or \
        re.search(rf"(?:tiling|tile|tiled)\s*height\s*(?:of|=|:)?\s*{_NUM}\s*(m|cm|mm)", f)
    if m:
        out["tile_m"] = _f(m.group(1)) / {"m": 1, "cm": 100, "mm": 1000}[m.group(2)]
    if re.search(r"\bfull[- ]height\b|floor to ceiling|kattoon asti", f):
        out["tile_m"] = None
        out["full_height"] = True
    if re.search(r"\b(no|without|zero|ilman)\s+(waste|allowance|cutting|hukkaa?)", f):
        out["waste_pct"] = 0.0
    else:
        m = re.search(rf"{_NUM}\s*%\s*(?:of\s*)?(?:waste|cutting|allowance|extra|reserve|spare|hukka|lisaa)", f) or \
            re.search(rf"(?:waste|allowance|hukka|reserve)\s*(?:of|=|:)?\s*{_NUM}\s*%", f)
        if m:
            out["waste_pct"] = _f(m.group(1))
    out["order"] = bool(re.search(r"\b(order|buy|purchase|need|needed|get|tilata|tilaan|ostaa|tarvit|bestellen|kaufen|"
                                  r"brauche|commander|acheter|ordinare|comprare)\w*", f))
    # "tiles to 1.2 m for the bathroom" or "flooring ... with 10% waste" asks for material to get, not a net area
    # (QA 14 Nov: the waste was listed but left out of the value). A question about the area itself stays net.
    if not out["order"]:
        material = re.search(r"\b(tiles?|tiling|flooring|parquet|laminate|vinyl|laatt\w*|laatoitu\w*|parket\w*|lattiamateriaal\w*|"
                             r"fliesen|plattor|kakel)\b", f)
        about_area = re.search(r"\b(area|net|how big|how large|pinta-?ala|flache|yta)\b", f)
        out["order"] = bool(out.get("waste_pct")) or bool(material and not about_area)
    return out


def surfaces(tk: dict) -> tuple[bool, bool, str]:
    """(floor, walls, how it was read) for a takeoff interpretation."""
    s = tk.get("surface", "floor")
    if s == "walls":
        return False, True, "walls only (asked)"
    if s == "floor+walls":
        return True, True, "floor and walls (asked)"
    if s == "floor" and tk.get("material") == "tiles" and set(tk["types"]) <= WET and not tk.get("floor_explicit"):
        return True, True, ("tiles in wet rooms read as floor and walls (walls to full height unless a tiling "
                            "height is given); ask for 'floor tiles' for the floor alone")
    return True, False, "floor only"


def room_walls(room: dict, inp: dict) -> dict:
    """room: {label, perimeter, height, height_src, openings: [{kind, id, width, z0, z1, src}]}.
    Returns gross, deductions and net wall area of the tiled band."""
    h = room.get("height")
    hsrc = room.get("height_src", "")
    if inp.get("ceiling_m"):
        h, hsrc = inp["ceiling_m"], "given in the question"
    if h is None:
        h, hsrc = DEFAULT_CEILING_M, f"ASSUMED ceiling height {DEFAULT_CEILING_M:.2f} m (not in the drawing; say e.g. 'ceiling height 2.6 m' to change)"
    band = h
    bsrc = "full height"
    if inp.get("tile_m"):
        band = min(inp["tile_m"], h)
        bsrc = f"tiled to {band:.2f} m (given)"
    gross = room["perimeter"] * band
    deds = []
    for o in room.get("openings", []):
        z0, z1 = max(o["z0"], 0.0), min(o["z1"], band)
        if z1 <= z0 or o["width"] <= 0:
            deds.append(dict(o, area=0.0, note="outside the tiled band"))
            continue
        deds.append(dict(o, area=o["width"] * (z1 - z0)))
    ded = sum(d["area"] for d in deds)
    return {"height": h, "height_src": hsrc, "band": band, "band_src": bsrc, "gross": gross,
            "deductions": deds, "openings_area": ded, "net": max(gross - ded, 0.0)}


def summary(rooms: list[dict], tk: dict, question: str) -> tuple[float, list, str, dict]:
    """rooms: {label, number, floor_area, floor_src, perimeter, perimeter_src, height, height_src, openings}.
    Returns (value, items, explanation, totals)."""
    inp = parse_inputs(question)
    want_floor, want_walls, how = surfaces(tk)
    waste = inp.get("waste_pct", DEFAULT_WASTE_PCT)
    waste_src = "given in the question" if "waste_pct" in inp else f"default {DEFAULT_WASTE_PCT:g}% cutting allowance (say 'with 15% waste' or 'no waste' to change)"
    items, floor_t, walls_t = [], 0.0, 0.0
    assumed = set()
    for i, r in enumerate(rooms):
        w = room_walls(r, inp) if want_walls else None
        it = {"n": i + 1, "room": r["label"]}
        if r.get("number"):
            it["number"] = r["number"]
        if want_floor:
            it["floor_m2"] = round(r["floor_area"], 2)
            it["floor_source"] = r.get("floor_src", "")
            floor_t += r["floor_area"]
        if w:
            it.update({"perimeter_m": round(r["perimeter"], 2), "perimeter_source": r.get("perimeter_src", ""),
                       "height_m": round(w["height"], 2), "height_source": w["height_src"],
                       "tiled_height_m": round(w["band"], 2), "gross_wall_m2": round(w["gross"], 2),
                       "openings": [{"kind": d["kind"], "id": d.get("id"), "width_m": round(d["width"], 2),
                                     "deducted_m2": round(d["area"], 2), "source": d.get("src", ""),
                                     "z0_m": round(d["z0"], 3), "z1_m": round(d["z1"], 3)} for d in w["deductions"]],
                       "openings_m2": round(w["openings_area"], 2), "net_wall_m2": round(w["net"], 2)})
            if r.get("openings_unknown"):
                it["openings_not_deducted"] = True
            if r.get("model_net_wall") is not None:
                it["model_net_wall_m2"] = round(r["model_net_wall"], 2)
            walls_t += w["net"]
            if "ASSUMED" in w["height_src"]:
                assumed.add(w["height_src"])
            for d in w["deductions"]:
                if "ASSUMED" in d.get("src", ""):
                    assumed.add(d["src"])
        items.append(it)
    net = floor_t + walls_t
    order_q = net * (1 + waste / 100.0)
    totals = {"floor_m2": round(floor_t, 2) if want_floor else None, "walls_m2": round(walls_t, 2) if want_walls else None,
              "net_m2": round(net, 2), "waste_pct": waste, "waste_m2": round(order_q - net, 2), "order_m2": round(order_q, 2),
              "value_is": "order quantity incl. waste" if inp.get("order") else "net area (waste shown separately)",
              "inputs": {"ceiling_m": inp.get("ceiling_m"), "tile_m": inp.get("tile_m"), "waste_pct": waste},
              "surfaces": how}
    value = round(order_q if inp.get("order") else net, 2)
    lines = []
    if want_floor:
        lines.append(f"floor {floor_t:.2f} m2")
    if want_walls:
        lines.append(f"walls {walls_t:.2f} m2 (perimeter x height" + ("" if any(r.get("openings_unknown") for r in rooms) else " - openings") + ")")
    lines.append(f"net {net:.2f} m2; waste {waste:g}% = {order_q - net:.2f} m2 ({waste_src}); order {order_q:.2f} m2")
    per = []
    for it in items[:12]:
        bits = []
        if "floor_m2" in it:
            bits.append(f"floor {it['floor_m2']:.2f}")
        if "net_wall_m2" in it:
            if it.get("openings_not_deducted"):
                bits.append(f"walls {it['perimeter_m']:.2f} m x {it['tiled_height_m']:.2f} m = {it['gross_wall_m2']:.2f} "
                            f"(openings not deducted)")
            else:
                bits.append(f"walls {it['perimeter_m']:.2f} m x {it['tiled_height_m']:.2f} m = {it['gross_wall_m2']:.2f} - openings "
                            f"{it['openings_m2']:.2f} = {it['net_wall_m2']:.2f}")
        per.append(f"{it['room']}: " + ", ".join(bits))
    expl = (f"{value:.2f} m2 = {'; '.join(lines)}. Value is the {totals['value_is']}. Surfaces: {how}. Per room: "
            + " | ".join(per) + (" | ..." if len(items) > 12 else "") + "."
            + (" Assumptions: " + "; ".join(sorted(assumed)) + "." if assumed else ""))
    from .takeoff import interpretation
    head = interpretation(tk, [((f"{r['label']} {r['number']}" if r.get("number") and str(r["number"]) not in str(r["label"])
                                 else r["label"]), r["floor_area"]) for r in rooms])
    return value, items, (head + " " + expl) if head else expl, totals


def answer_line(value: float, totals: dict, route: str, estimate: str = "") -> str:
    parts = []
    if totals.get("floor_m2") is not None:
        parts.append(f"floor {totals['floor_m2']:.2f}")
    if totals.get("walls_m2") is not None:
        parts.append(f"walls {totals['walls_m2']:.2f}")
    parts.append(f"{totals['waste_pct']:g}% waste {totals['waste_m2']:.2f}")
    tag = f" [ESTIMATE: {estimate}]" if estimate else ""
    return f"Answer{tag}: {value:.2f} m2 ({' + '.join(parts)}; {totals['value_is']})   route: {route} (cost €0)"


def ring_length(poly) -> float:
    """Perimeter of a room polygon: exterior plus holes (a column inside the room has wall faces too)."""
    geoms = getattr(poly, "geoms", [poly])
    return sum(p.exterior.length + sum(r.length for r in p.interiors) for p in geoms if hasattr(p, "exterior"))


def draw_rooms(cv, rooms, color_room, color_wall, color_open, label_fn, label_at=None):
    """Room outlines filled, wall segments (the outline) drawn thick, openings marked as boxes on the wall.
    `label_at(i, r)` -> (x, y) places a room's label (default: its representative point)."""
    for i, r in enumerate(rooms):
        poly = r["poly"]
        for p in getattr(poly, "geoms", [poly]):
            if not hasattr(p, "exterior"):
                continue
            cv.poly(list(p.exterior.coords), fill=color_room + (55,), outline=None)
            if r.get("wall_lines") is None:
                cv.line(list(p.exterior.coords), color=color_wall, width=5)
                for ring in p.interiors:
                    cv.line(list(ring.coords), color=color_wall, width=5)
        wl = r.get("wall_lines")
        if wl is not None:
            for g in getattr(wl, "geoms", [wl]):
                if hasattr(g, "coords") and len(g.coords) > 1:
                    cv.line(list(g.coords), color=color_wall, width=5)
                elif hasattr(g, "exterior"):
                    cv.line(list(g.exterior.coords), color=color_wall, width=5)
        for o in r.get("openings", []):
            if o.get("bbox") is not None:
                cv.box(o["bbox"], n=None, color=color_open, pad=0.0)
                b = o["bbox"]
                if o.get("deducted") is not None:
                    cv.text(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), f"{o['kind']} -{o['deducted']:.2f}", size=10)
        c = poly.representative_point()
        cv.text(label_at(i, r) if label_at else (c.x, c.y), label_fn(i, r), size=12)
