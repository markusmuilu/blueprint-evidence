"""Baseboard (skirting) length: "how many linear metres of baseboard for the bedrooms?"

    baseboard length of a room = room perimeter - the widths of its door openings

A baseboard runs along the foot of every wall and stops at a door opening. Windows do not interrupt it,
so only doors are deducted. The perimeter and the doors are the ones bpqa/walls.py already measures for
wall takeoffs (IfcSpace outline and IfcDoor widths; Swiss ROOM_OUTLINE and DOOR leaves; on a scan the
perimeter of the measured room region, doors not detectable), so this is a third surface of the same
takeoff machinery: tk["surface"] == "baseboard". The answer is a length in metres, one line per room
(perimeter, doors deducted, net length), no waste unless the question states one.

  parse_baseboard(question)   question -> takeoff dict, or None when the question is not about baseboards
  room_baseboard(room)        one room record (walls.py shape) -> perimeter, doors, net length
  summary(rooms, tk, q)       the answer: value, items, explanation, totals (called by walls.summary)

A room type is named in the question ("for the bedrooms") or not; with none named, or "all rooms", every
room of the floor counts except balconies and terraces (no baseboard outdoors), and the answer says so.
"""
from __future__ import annotations

import re

from .query import fold

# the product, EN FI DE SV FR (+ IT, ES, DA, ET); folded text (lower case, accents stripped, sharp s -> ss)
BASEBOARD_RE = re.compile(
    r"\bbase ?boards?\b|\bskirting\b|\bskirting ?boards?\b|\bkick ?boards?\b|\bwall base\b|"
    r"\bjalkalist\w*|\bsockel ?leist\w*|\bfuss ?leist\w*|\bleisten\b(?=.*\bsockel)|\bgolvlist\w*|\bsockellist\w*|\bfotlist\w*|"
    r"\bplinthes?\b|\bbattiscopa\w*|\brodapie\w*|\bsoklilist\w*|\bfodlist\w*|\bjalalist\w*")
ALL_ROOMS_RE = re.compile(
    r"\ball (?:the |of the |of my |my |our )?(?:rooms|spaces)\b|\bevery room\b|\beach room\b|\bwhole (?:floor|flat|apartment|house|plan)\b|"
    r"\bentire (?:floor|flat|apartment|house|plan)\b|\bthroughout\b|"
    r"\bkaikki(?:in|issa|sta|en)? (?:huone|tila)\w*|\bkaikk\w* huone\w*|"
    r"\balle (?:raume|zimmer)\b|\bin allen (?:raumen|zimmern)\b|\bsamtliche\w* (?:raume|zimmer)\b|\bjeden raum\b|"
    r"\balla rum\b|\bi alla rum\b|\bvarje rum\b|"
    r"\btoutes les (?:pieces|chambres)\b|\bchaque piece\b|\btutte le (?:stanze|camere)\b|\btodas las habitaciones\b")
from .walls import WASTE_AFTER as _WASTE, WASTE_BEFORE as _WASTE_BEFORE, NO_WASTE as _NO_WASTE   # one waste vocabulary

# types an "all rooms" baseboard covers: every indoor room type; a balcony or terrace has no baseboard
OUTDOOR = {"balcony"}


def _all_types() -> list[str]:
    from .takeoff import TYPE_NAMES
    return [t for t in TYPE_NAMES if t not in OUTDOOR and t != "room"]   # a generic label is kept by select_rooms


def parse_baseboard(question: str) -> dict | None:
    """Takeoff dict for a baseboard question ({types, surface: 'baseboard', all_rooms, waste_pct ...}) or None."""
    from .takeoff import EXCLUDE, GROUPS, QUOTED, _Q_TYPE_RE, ABBR_RE, ABBR
    raw = QUOTED.sub(" ", question)
    f = fold(raw)
    if not BASEBOARD_RE.search(f):
        return None
    f_incl = EXCLUDE.sub(" ", f)
    types: list[str] = []
    for t, rx in _Q_TYPE_RE.items():
        if rx.search(f_incl):
            types += list(GROUPS.get(t, (t,)))
    for m in ABBR_RE.finditer(EXCLUDE.sub(" ", raw)):
        types.append(ABBR[m.group(1)])
    types = list(dict.fromkeys(types))
    excluded: list[str] = []
    for t, rx in _Q_TYPE_RE.items():
        if t != "wet" and any(rx.search(m.group(0)) for m in EXCLUDE.finditer(f)):
            excluded.append(t)
    all_rooms = not types or bool(ALL_ROOMS_RE.search(f))
    out = {"types": types, "group": None, "material": "baseboard", "surface": "baseboard", "aggregation": "sum",
           "floor_explicit": False, "baseboard": True}
    if all_rooms:
        out["all_rooms"] = True
        out["all_rooms_why"] = ("all rooms asked" if ALL_ROOMS_RE.search(f) else "no room type named, so all rooms")
        out["types"] = [t for t in _all_types() if t not in excluded]
        if excluded:
            out["excluded_types"] = excluded
    for m in (re.search(_NO_WASTE, f),):
        if m:
            out["waste_pct"], out["waste_src"] = 0.0, "given in the question"
    if "waste_pct" not in out:
        m = re.search(_WASTE, f) or re.search(_WASTE_BEFORE, f)
        if m:
            out["waste_pct"], out["waste_src"] = float(m.group(1).replace(",", ".")), "given in the question"
    return out


def is_baseboard(tk: dict | None) -> bool:
    return bool(tk) and tk.get("surface") == "baseboard"


def select_rooms(rooms: list[dict], tk: dict, name_key: str = "name") -> list[dict]:
    """All-rooms selection for the routes that hold labelled room records: every room except outdoor types
    (balcony, terrace) and the excluded types. A room with no recognisable label stays in."""
    from .takeoff import room_types
    skip = set(OUTDOOR) | set(tk.get("excluded_types") or ())
    out = []
    for r in rooms:
        ts = room_types(r.get(name_key))
        if ts and ts <= skip:
            continue
        out.append(r)
    return out


def room_baseboard(room: dict) -> dict:
    """room: walls.py record {label, perimeter, openings: [{kind, id, width, src ...}]}. Only doors are deducted."""
    doors = [o for o in room.get("openings", []) if o.get("kind") == "door"]
    deds = [dict(o, area=max(o["width"], 0.0)) for o in doors]
    ded = sum(d["area"] for d in deds)
    return {"height": 0.0, "height_src": "", "band": 0.0, "band_src": "", "gross": room["perimeter"], "deductions": deds,
            "openings_area": ded, "net": max(room["perimeter"] - ded, 0.0)}


def summary(rooms: list[dict], tk: dict, question: str) -> tuple[float, list, str, dict]:
    """Same contract as walls.summary: (value, items, explanation, totals). Windows are dropped from each room
    record's openings in place (they do not interrupt a baseboard), so the evidence drawing shows doors only."""
    from .takeoff import interpretation
    waste = tk.get("waste_pct") or 0.0
    waste_given = "waste_pct" in tk
    items, per = [], []
    perim_t = doors_t = net_t = 0.0
    n_windows = 0
    unknown = any(r.get("openings_unknown") for r in rooms)
    for i, r in enumerate(rooms):
        n_windows += sum(1 for o in r.get("openings", []) if o.get("kind") != "door")
        r["openings"] = [o for o in r.get("openings", []) if o.get("kind") == "door"]
        b = room_baseboard(r)
        it = {"n": i + 1, "room": r["label"]}
        if r.get("number"):
            it["number"] = r["number"]
        it.update({"perimeter_m": round(r["perimeter"], 2), "perimeter_source": r.get("perimeter_src", ""),
                   "doors": [{"id": d.get("id"), "width_m": round(d["width"], 2), "source": d.get("src", "")} for d in b["deductions"]],
                   "doors_m": round(b["openings_area"], 2), "net_m": round(b["net"], 2)})
        if unknown:
            it["openings_not_deducted"] = True
        items.append(it)
        perim_t += r["perimeter"]
        doors_t += b["openings_area"]
        net_t += b["net"]
        nd = len(b["deductions"])
        per.append(f"{r['label']}: {r['perimeter']:.2f} m" + ("" if unknown else
                   f" - {nd} door{'s' if nd != 1 else ''} {b['openings_area']:.2f} m = {b['net']:.2f} m"))
    order = net_t * (1 + waste / 100.0)
    value = round(order if waste_given and waste else net_t, 2)
    totals = {"unit": "m", "perimeter_m": round(perim_t, 2), "doors_m": round(doors_t, 2), "net_m": round(net_t, 2),
              "waste_pct": waste, "waste_m": round(order - net_t, 2), "order_m": round(order, 2),
              "windows_ignored": n_windows, "rooms": len(rooms),
              "value_is": "length to order incl. waste" if waste_given and waste else "net length (no waste added)",
              "surfaces": "baseboard length: room perimeter minus door openings"}
    lines = [f"perimeter {perim_t:.2f} m" + ("" if unknown else f" - doors {doors_t:.2f} m"), f"net {net_t:.2f} m"]
    if waste_given and waste:
        lines.append(f"waste {waste:g}% = {order - net_t:.2f} m ({tk.get('waste_src', '')}); order {order:.2f} m")
    shown = " | ".join(per[:15]) + (" | ..." if len(per) > 15 else "")
    scope = ""
    if tk.get("all_rooms"):
        ex = tk.get("excluded_types")
        scope = (f" Rooms: {tk.get('all_rooms_why', 'all rooms')}; balconies and terraces are left out (no baseboard outdoors)"
                 + (f"; also left out as asked: {', '.join(ex)}" if ex else "") + ".")
    method = (" Door openings are not deducted (a scan shows no door geometry), so this is the whole perimeter, an upper bound."
              if unknown else
              (f" Only door openings interrupt a baseboard; {n_windows} window opening(s) on the room outlines are not deducted." if n_windows else
               " Only door openings interrupt a baseboard."))
    expl = (f"{value:.2f} m of baseboard = {'; '.join(lines)}. Value is the {totals['value_is']}. Per room (outline length minus "
            f"door widths on that outline): {shown}.{scope}{method} No deduction for built-ins, thresholds or fixed furniture.")
    head = interpretation(tk, [((f"{r['label']} {r['number']}" if r.get("number") and str(r["number"]) not in str(r["label"])
                                 else r["label"]), r["floor_area"]) for r in rooms])
    return value, items, (head + " " + expl) if head else expl, totals


def answer_line(value: float, totals: dict, route: str, estimate: str = "") -> str:
    tag = f" [ESTIMATE: {estimate}]" if estimate else ""
    w = f" + {totals['waste_pct']:g}% waste {totals['waste_m']:.2f}" if totals.get("waste_pct") else ""
    return (f"Answer{tag}: {value:.2f} m of baseboard ({totals['perimeter_m']:.2f} perimeter - {totals['doors_m']:.2f} doors{w}; "
            f"{totals['value_is']})   route: {route} (cost €0)")


def room_caption(i: int, r: dict, b: dict) -> str:
    return f"{i + 1}. {r['label'][:22]}: {r['perimeter']:.2f} - doors {b['openings_area']:.2f} = {b['net']:.2f} m"
