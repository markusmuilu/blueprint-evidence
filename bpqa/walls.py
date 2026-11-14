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
# a length unit, abbreviated or written out in any inflection ("1,2 metrin korkeuteen", "120 Zentimeter", "1.2 metres")
_UNIT = (r"(mm|cm|m|millimet\w*|millimeter\w*|sentti\w*|centimet\w*|zentimeter\w*|centimeter\w*|"
         r"metri\w*|meter\w*|metre\w*)")
# a percentage, with the sign or the word ("10 %", "10 percent", "10 prosentin")
_PCT = r"\s*(?:%|percent\b|per cent\b|prosent\w*|prozent\w*|procent\w*)"
# waste words after / before the percentage, in every language the rules read. Finnish inflects the stem
# (hukka, hukkaa, hukalla, hukan; varaus, varauksella). bpqa/baseboard.py uses the same patterns.
WASTE_AFTER = (rf"{_NUM}{_PCT}\s*(?:of\s*)?(?:waste|cutting|allowance|extra|spare|reserve|huk(?:ka|a)\w*|lisaa|varaa|"
               r"varau\w*|verschnitt|zuschlag|svinn|spill|tillagg|de chutes?|de perte|chutes?|pertes?|marge)")
WASTE_BEFORE = (r"(?:waste|allowance|huk(?:ka|a)\w*|reserve|verschnitt|zuschlag|svinn|spill|chutes?|pertes?)"
                rf"\s*(?:of|=|:|von|av|de|on)?\s*{_NUM}{_PCT}")
NO_WASTE = (r"\b(no|without|zero|ilman|ohne|utan|kein|keinen|sans)\s+(waste|allowance|cutting|hukkaa?|verschnitt|spill|"
            r"svinn|chutes?|pertes?)")


# a percentage written in words ("ten percent", "kymmenen prosenttia", "zehn Prozent") or as "10pct": rewritten to
# digits before the rules read it, so the waste rules and the check on the language model's reading both see it
_PCT_WORDS = {"five": 5, "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20, "viisi": 5, "viiden": 5,
              "kymmenen": 10, "kahdentoista": 12, "kaksitoista": 12, "viisitoista": 15, "viidentoista": 15,
              "kaksikymmenta": 20, "kahdenkymmenen": 20, "funf": 5, "zehn": 10, "zwolf": 12, "funfzehn": 15,
              "zwanzig": 20, "fem": 5, "tio": 10, "tolv": 12, "femton": 15, "tjugo": 20}
_PCT_WORD_RE = re.compile(r"\b(" + "|".join(sorted(_PCT_WORDS, key=len, reverse=True)) + r")\s*"
                          r"(?=(?:%|percent\b|per cent\b|prosent\w*|prozent\w*|procent\w*))")


def spelled_percent(f: str) -> str:
    """Folded text with percentages in words or 'pct' rewritten to digits; unchanged otherwise."""
    f = re.sub(r"(\d)\s*pct\b", r"\1 %", f)
    return _PCT_WORD_RE.sub(lambda m: f"{_PCT_WORDS[m.group(1)]} ", f)


def _f(s: str) -> float:
    return float(s.replace(",", "."))


def _metres(v: str, unit: str) -> float:
    u = unit.lower()
    div = 1000 if u.startswith(("mm", "milli")) else 100 if u.startswith(("c", "s", "z")) else 1
    return _f(v) / div


def parse_inputs(question: str, tk: dict | None = None) -> dict:
    """Editable inputs stated in the question: ceiling height, tiled height, waste %, order intent.
    `tk` is the takeoff the question was read as: inputs the language model read (tk["inputs"], already
    checked against the question text) fill in what the wording rules did not find."""
    out = _parse_inputs(question)
    for k, v in ((tk or {}).get("inputs") or {}).items():
        if k not in out or (k == "waste_pct" and out.get(k) is None):
            out[k] = v
    if (tk or {}).get("surface") == "baseboard":
        out["baseboard"] = True       # a length, not an area: bpqa/baseboard.py
    return out


def _parse_inputs(question: str) -> dict:
    f = spelled_percent(fold(question))
    out = {}
    m = re.search(rf"(?:ceiling|room|wall)?\s*heights?\s*(?:of|is|=|:)?\s*{_NUM}\s*{_UNIT}\b", f) or \
        re.search(rf"{_NUM}\s*{_UNIT}\s*(?:high|tall|ceilings?|ceiling height|korkea|korkuinen)", f) or \
        re.search(rf"(?:huonekorkeus|raumhohe|deckenhohe|takhojd|rumshojd)\s*(?:on|ist|ar|von)?\s*{_NUM}\s*{_UNIT}", f)
    if m:
        v = _metres(m.group(1), m.group(2))
        if 1.5 <= v <= 12:
            out["ceiling_m"] = v
    m = re.search(rf"(?:tiles?|tiling|tiled|laatoitus|laatat?)\s*(?:up\s*)?(?:to|until|asti|korkeuteen)\s*(?:a\s*height\s*of\s*)?{_NUM}\s*{_UNIT}", f) or \
        re.search(rf"(?:tiling|tile|tiled)\s*height\s*(?:of|=|:)?\s*{_NUM}\s*{_UNIT}", f) or \
        (re.search(r"\btil(e|es|ing|ed)\b|laat|fliese|kakel|klinker|carrelage", f) and (
            re.search(rf"\b(?:up\s*to|until|bis|asti|till|upp till)\s*(?:a\s*height\s*of\s*)?{_NUM}\s*{_UNIT}\b", f) or
            re.search(rf"{_NUM}\s*{_UNIT}\s*(?:korkeu\w*|asti|hohe|hojd)", f)))
    if m:
        out["tile_m"] = _metres(m.group(1), m.group(2))
    if re.search(r"\bfull[- ]height\b|floor to ceiling|kattoon asti", f):
        out["tile_m"] = None
        out["full_height"] = True
    if re.search(NO_WASTE, f):
        out["waste_pct"] = 0.0
    else:
        m = re.search(WASTE_AFTER, f) or re.search(WASTE_BEFORE, f)
        if m:
            out["waste_pct"] = _f(m.group(1))
    # The value is an order quantity (waste included) when the question says order/buy, states a waste, or asks
    # for a material (tiles, flooring: material is something you order, also inside Finnish compounds like
    # "seinalaattaa"). "need" / "tarvitaan" alone says nothing about waste and stays a net area, and so does a
    # question about the area itself. The explanation states waste only when the value includes it.
    out["order"] = bool(re.search(r"\b(order|buy|purchase|tilata|tilaan|ostaa|bestellen|kaufen|commander|acheter|"
                                  r"ordinare|comprare)\w*", f))
    if not out["order"]:
        material = re.search(r"\b(tiles?|tiling|flooring|parquet|laminate|vinyl|\w*laatt\w*|\w*laatoi\w*|parket\w*|"
                             r"lattiamateriaal\w*|\w*fliese\w*|plattor|kakel\w*|carrelage|piastrell\w*)\b", f)
        about_area = re.search(r"\b(area|net|how big|how large|pinta-?ala|flache|yta)\b", f)
        out["order"] = bool(out.get("waste_pct")) or bool(material and not about_area)
    return out


def surfaces(tk: dict) -> tuple[bool, bool, str]:
    """(floor, walls, how it was read) for a takeoff interpretation."""
    s = tk.get("surface", "floor")
    if s == "baseboard":
        return False, True, "baseboard length (asked): room perimeter minus door openings"
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
    if inp.get("baseboard"):
        from .baseboard import room_baseboard
        return room_baseboard(room)
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
    if tk.get("surface") == "baseboard":
        from .baseboard import summary as baseboard_summary
        return baseboard_summary(rooms, tk, question)
    inp = parse_inputs(question, tk)
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
              "value_is": "order quantity incl. waste" if inp.get("order") else "net area, no waste added",
              "waste_in_value": bool(inp.get("order")),
              "inputs": {"ceiling_m": inp.get("ceiling_m"), "tile_m": inp.get("tile_m"), "waste_pct": waste},
              "surfaces": how}
    value = round(order_q if inp.get("order") else net, 2)
    lines = []
    if want_floor:
        lines.append(f"floor {floor_t:.2f} m2")
    if want_walls:
        lines.append(f"walls {walls_t:.2f} m2 (perimeter x height" + ("" if any(r.get("openings_unknown") for r in rooms) else " - openings") + ")")
    if inp.get("order"):
        lines.append(f"net {net:.2f} m2; waste {waste:g}% = {order_q - net:.2f} m2 ({waste_src}); order {order_q:.2f} m2")
    else:       # the value is the net area: the explanation states no waste figure the value does not contain
        lines.append(f"net {net:.2f} m2, no waste added (ask for tiles to order, or add 'with 10% waste', for an order quantity)")
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


def room_caption(i: int, r: dict, inp: dict) -> str:
    """Label of a room on the evidence image: floor and net wall area, or the baseboard length."""
    w = room_walls(r, inp)
    if inp.get("baseboard"):
        from .baseboard import room_caption as cap
        return cap(i, r, w)
    return f"{i + 1}. {r['label'][:22]}: floor {r['floor_area']:.2f}, walls {w['net']:.2f} m²"


def answer_line(value: float, totals: dict, route: str, estimate: str = "") -> str:
    if totals.get("unit") == "m":
        from .baseboard import answer_line as bl
        return bl(value, totals, route, estimate)
    parts = []
    if totals.get("floor_m2") is not None:
        parts.append(f"floor {totals['floor_m2']:.2f}")
    if totals.get("walls_m2") is not None:
        parts.append(f"walls {totals['walls_m2']:.2f}")
    if totals.get("waste_in_value", True):
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
