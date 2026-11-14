"""Generic schedule tables from a PDF text layer: columns from header words, rows from baselines.

    tables(pdf) -> [Table(title, columns, rows, bbox)]

1. Words are mapped into the displayed page (sheets are often rotated 90 degrees).
2. Lines are words sharing a baseline. A header line holds 3+ schedule header words (MARK, TYPE, COUNT,
   WIDTH, HEIGHT, FIRE, RATING, MATERIAL, FRAME, ROOM, NAME, AREA, FLOOR, BASE, CEILING ...). Header words
   stacked on the line above or below ("Type / Mark", "Door / Type") join the column they overlap.
   Words closer than half a character height form one column label ("Fire Rating").
3. Column boundaries are midway between neighbouring column labels; data lines below the header are cut
   into cells at those boundaries until the first empty band (2.5 line heights) or the next header.
4. The title is the nearest line above the header that contains SCHEDULE.

Nothing is specific to one sheet: the door schedule of the Sports Complex and the door / window type
schedules of the Cannery Trail set are read by the same code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

import pymupdf

DEBUG = False
HEADER_WORDS = {"count", "qty", "quantity", "type", "mark", "width", "height", "fire", "rating", "material", "frame",
                "door", "window", "comments", "remarks", "notes", "hardware", "level", "room", "name", "no", "no.",
                "number", "area", "floor", "base", "walls", "wall", "north", "south", "east", "west", "ceiling",
                "size", "description", "finish", "set", "glazing", "manufacturer", "model", "location", "from",
                "to", "sill", "head", "jamb", "thickness", "operation", "elevation", "detail", "label", "unit"}


@dataclass
class Table:
    title: str
    columns: list                       # labels, left to right
    rows: list = field(default_factory=list)       # [{label: text}, ...]
    row_boxes: list = field(default_factory=list)  # page-space rects (unrotated) per row, for evidence
    header_box: tuple = None

    def col(self, *names):
        """The column whose label contains every given word (case-insensitive), or None."""
        for c in self.columns:
            lc = c.lower()
            if all(n.lower() in lc for n in names):
                return c
        return None


def _norm(t):
    return re.sub(r"[^\w.#/'\"-]", "", t).lower()


def _lines(words):
    words = sorted(words, key=lambda w: ((w["y0"] + w["y1"]) / 2, w["x0"]))
    lines = []
    for w in words:
        yc = (w["y0"] + w["y1"]) / 2
        h = w["y1"] - w["y0"]
        if lines and abs(lines[-1]["yc"] - yc) < 0.45 * max(h, lines[-1]["h"]):
            ln = lines[-1]
            ln["words"].append(w)
        else:
            lines.append({"yc": yc, "h": h, "words": [w]})
    out = []
    for ln in lines:      # split a baseline at wide gaps: tables side by side are separate lines
        ws = sorted(ln["words"], key=lambda w: w["x0"])
        cur = [ws[0]]
        for w in ws[1:]:
            if w["x0"] - cur[-1]["x1"] > 35 * max(ln["h"], 1):
                out.append({"yc": ln["yc"], "h": ln["h"], "words": cur})
                cur = []
            cur.append(w)
        out.append({"yc": ln["yc"], "h": ln["h"], "words": cur})
    return out


def _labels(ws, h):
    """Join header words into column labels: words closer than 0.6 x text height are one label."""
    out = []
    for w in sorted(ws, key=lambda w: w["x0"]):
        if out and w["x0"] - out[-1]["x1"] < 0.6 * h:
            out[-1]["t"] += " " + w["t"]
            out[-1]["x1"] = max(out[-1]["x1"], w["x1"])
        else:
            out.append({"t": w["t"], "x0": w["x0"], "x1": w["x1"]})
    return out


def _is_header(ln):
    toks = [_norm(w["t"]) for w in ln["words"]]
    hits = {t for t in toks if t in HEADER_WORDS}
    return len(hits) >= 3 and len(toks) <= 40 and sum(t in HEADER_WORDS for t in toks) >= 0.6 * len(toks)


def _vertical_segments(page, rot):
    """Vertical rules of the page in displayed coordinates: (x, y0, y1)."""
    out = []
    try:
        drawings = page.get_drawings()
    except Exception:
        return out
    for d in drawings:
        for it in d.get("items", []):
            pts = []
            if it[0] == "l":
                pts = [(it[1], it[2])]
            elif it[0] == "re":
                r = it[1]
                pts = [(pymupdf.Point(r.x0, r.y0), pymupdf.Point(r.x0, r.y1)), (pymupdf.Point(r.x1, r.y0), pymupdf.Point(r.x1, r.y1)),
                       (pymupdf.Point(r.x0, r.y0), pymupdf.Point(r.x1, r.y0)), (pymupdf.Point(r.x0, r.y1), pymupdf.Point(r.x1, r.y1))]
            for a, b in pts:
                a, b = a * rot, b * rot
                if abs(a.x - b.x) < 0.6 and abs(a.y - b.y) > 3:
                    out.append(((a.x + b.x) / 2, min(a.y, b.y), max(a.y, b.y)))
    return out


@lru_cache(maxsize=8)
def tables(pdf_path: str) -> tuple:
    doc = pymupdf.open(pdf_path)
    out = []
    for page in doc:
        rot = page.rotation_matrix
        words = []
        for x0, y0, x1, y1, t, *_ in page.get_text("words"):
            r = pymupdf.Rect(x0, y0, x1, y1) * rot
            r.normalize()
            words.append({"x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1, "t": t, "raw": (x0, y0, x1, y1)})
        lines = _lines(words)
        vsegs = _vertical_segments(page, rot)
        for i, ln in enumerate(lines):
            if not _is_header(ln):
                continue
            h = ln["h"]
            hx0 = min(w["x0"] for w in ln["words"]) - 2 * h
            hx1 = max(w["x1"] for w in ln["words"]) + 2 * h
            labels = _labels(ln["words"], h)
            # stacked header words just above / below join the column they overlap ("Type" over "Mark")
            def headerish(L2):
                ws2 = [_norm(w["t"]) for w in L2["words"] if hx0 <= w["x0"] <= hx1]
                return ws2 and sum(t in HEADER_WORDS for t in ws2) >= 0.6 * len(ws2)

            stacked = []
            for j in (i - 1, i + 1):
                if 0 <= j < len(lines) and abs(lines[j]["yc"] - ln["yc"]) < 1.6 * h and headerish(lines[j]):
                    stacked.append(lines[j])
                    for w in lines[j]["words"]:
                        if not (hx0 <= w["x0"] <= hx1) or _norm(w["t"]) not in HEADER_WORDS:
                            continue
                        c = (w["x0"] + w["x1"]) / 2
                        best = min(labels, key=lambda L: abs((L["x0"] + L["x1"]) / 2 - c))
                        if best["x0"] - h <= c <= best["x1"] + h:
                            best["t"] = (w["t"] + " " + best["t"]) if j < i else (best["t"] + " " + w["t"])
                        else:
                            labels.append({"t": w["t"], "x0": w["x0"], "x1": w["x1"]})
            labels.sort(key=lambda L: L["x0"])
            if len(labels) < 3:
                continue
            cuts = [(labels[k]["x1"] + labels[k + 1]["x0"]) / 2 for k in range(len(labels) - 1)]
            # the table's own grid, when drawn: vertical rules crossing the header line are the column edges
            xs = sorted({round(x, 0) for x, y0, y1 in vsegs if y0 - 1 <= ln["yc"] <= y1 + 1 and hx0 - 4 * h <= x <= hx1 + 4 * h})
            grid = []
            for x in xs:
                if not grid or x - grid[-1] > 1.5:
                    grid.append(x)
            if len(grid) >= 4:
                hws = list(ln["words"])
                for j in (i - 2, i - 1, i + 1):
                    if 0 <= j < len(lines) and abs(lines[j]["yc"] - ln["yc"]) < 2.6 * h and (j < i or headerish(lines[j])):
                        hws += [w for w in lines[j]["words"] if _norm(w["t"]) in HEADER_WORDS and grid[0] <= w["x0"] <= grid[-1]]
                cells = [[] for _ in range(len(grid) - 1)]
                for w in sorted(hws, key=lambda w: (w["y0"], w["x0"])):
                    c = (w["x0"] + w["x1"]) / 2
                    k = sum(c > x for x in grid) - 1
                    if 0 <= k < len(cells):
                        cells[k].append(w["t"])
                keep = [k for k in range(len(cells)) if cells[k]]
                if len(keep) >= 3:
                    labels = [{"t": " ".join(dict.fromkeys(cells[k])), "x0": grid[k], "x1": grid[k + 1]} for k in keep]
                    cuts = [grid[k + 1] for k in keep[:-1]]
                    hx0, hx1 = grid[keep[0]] - 1, grid[keep[-1] + 1] + 1
            seen = {}
            for L in labels:
                if L["t"] in seen:
                    seen[L["t"]] += 1
                    L["t"] = f"{L['t']} ({seen[L['t']]})"
                else:
                    seen[L["t"]] = 1
            # title: nearest line above with SCHEDULE, within 6 line heights
            title = ""
            for j in range(i - 1, max(i - 8, -1), -1):
                txt = " ".join(w["t"] for w in lines[j]["words"] if hx0 <= w["x0"] <= hx1)
                if re.search(r"schedule", txt, re.I):
                    title = txt
                    break
            tab = Table(title=title, columns=[L["t"] for L in labels],
                        header_box=(hx0, ln["yc"] - h, hx1, ln["yc"] + h))
            start = i + 1
            last_y = ln["yc"]
            while start < len(lines) and abs(lines[start]["yc"] - ln["yc"]) < 1.6 * h and headerish(lines[start]):
                last_y = lines[start]["yc"]
                start += 1
            merged = []
            for L2 in lines[start:]:
                if merged and abs(L2["yc"] - merged[-1]["yc"]) < 0.45 * max(L2["h"], merged[-1]["h"]):
                    merged[-1] = {"yc": merged[-1]["yc"], "h": merged[-1]["h"], "words": merged[-1]["words"] + L2["words"]}
                else:
                    merged.append(L2)
            for L2 in merged:
                ws = [w for w in L2["words"] if hx0 <= w["x0"] <= hx1]
                if not ws:
                    continue
                gap = (4.0 if not tab.rows else 2.5) * max(h, L2["h"])
                if L2["yc"] - last_y > gap or (_is_header(L2) and headerish(L2)):
                    break
                last_y = L2["yc"]
                cells = [[] for _ in labels]
                for w in ws:
                    c = (w["x0"] + w["x1"]) / 2
                    idx = sum(c > x for x in cuts)
                    cells[idx].append(w["t"])
                row = {lab["t"]: " ".join(c) for lab, c in zip(labels, cells)}
                if sum(1 for v in row.values() if v) < 2:
                    continue
                tab.rows.append(row)
                raws = [w["raw"] for w in ws]
                tab.row_boxes.append((min(r[0] for r in raws), min(r[1] for r in raws), max(r[2] for r in raws), max(r[3] for r in raws)))
            if DEBUG:
                print("table", title, len(labels), len(tab.rows))
            if len(tab.rows) >= 2:
                out.append(tab)
    return tuple(out)
