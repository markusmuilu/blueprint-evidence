"""PDF text-layer route: read schedules straight from the vector text of a drawing sheet.

Implemented: door schedules (rows keyed by door numbers like 118.A1). Each row is rebuilt from
words that share the row's baseline (sheets are often rotated 90 degrees, so a row can run
vertically in page space), then parsed into room, width, height, type, door material, frame,
hardware group and notes. Counting and lookups are then plain filters over parsed rows, and
the evidence image boxes exactly the rows that matched.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pymupdf
from PIL import Image

from ..answer import Answer, abstain
from ..evidence import PALETTE, ImageCanvas, evidence_path, finish

ROUTE = "pdf-text"
DOOR_NO = re.compile(r"^\d{3}\.[A-Z]\d?$")
DIM = r"(\d+)' - (\d+)(?: (\d+/\d+))?\"?"


def _dim(m, i):
    ft, inch, frac = m.group(i), m.group(i + 1), m.group(i + 2)
    return f"{ft}'-{inch}{(' ' + frac) if frac else ''}\""


@lru_cache(maxsize=8)
def door_schedule(pdf_path: str):
    doc = pymupdf.open(pdf_path)
    page = doc[0]
    words = page.get_text("words")
    heads = [w for w in words if DOOR_NO.match(w[4])]
    if len(heads) < 3:
        return page.rect, page.rotation, []
    rotated = page.rotation in (90, 270)
    rows = []
    for h in heads:
        if rotated:   # row runs along page y; words share the head's x centre
            xc = (h[0] + h[2]) / 2
            row = [w for w in words if abs((w[0] + w[2]) / 2 - xc) < 4 and w[1] <= h[3] + 1]
            row.sort(key=lambda w: -w[1])
        else:
            yc = (h[1] + h[3]) / 2
            row = [w for w in words if abs((w[1] + w[3]) / 2 - yc) < 3 and w[0] >= h[0] - 1]
            row.sort(key=lambda w: w[0])
        text = " ".join(w[4] for w in row)
        m = re.match(r"^(\S+) (.+?) (\d{3}) " + DIM + " " + DIM + r" (\S+)(?: (\S+))?(?: (\S+))?(.*)$", text)
        if not m:
            continue
        rest = (m.group(13) or "").split()
        rec = {"door": m.group(1), "room_name": m.group(2), "room": m.group(3),
               "width": _dim(m, 4), "height": _dim(m, 7), "type": m.group(10),
               "door_mat": m.group(11), "frame": m.group(12), "hardware": None, "notes": [], "words": row}
        if rec["type"] == "-":   # no type: following tokens shift
            rec["door_mat"] = rec["frame"] = None
            rest = [t for t in [m.group(11), m.group(12)] if t] + rest
        toks = list(rest)
        # optional single-letter column (e.g. "A") then the two-digit hardware group, then notes
        while toks and not re.match(r"^\d{2}$", toks[0]) and len(toks[0]) <= 2:
            toks.pop(0)
        if toks and re.match(r"^\d{2}$", toks[0]):
            rec["hardware"] = toks.pop(0)
        for t in toks:
            t2 = t.strip(",")
            if re.match(r"^[A-E]$", t2):
                rec["notes"].append(t2)
            else:
                break
        if rec["frame"] is not None and not re.match(r"^(STL|AL|WD|HM|-)", rec["frame"]):
            rec["frame"] = None
        rows.append(rec)
    # rows are also cut off by the legend; notes after a gap belong to the legend, so keep as parsed
    return page.rect, page.rotation, rows


def _render(pdf_path, boxes, ctx, value, unit, note):
    doc = pymupdf.open(pdf_path)
    page = doc[0]
    zoom = min(1.0, 2000 / max(page.rect.width, page.rect.height))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    cv = ImageCanvas(img)
    rot = page.rotation_matrix * pymupdf.Matrix(zoom, zoom)
    for i, b in enumerate(boxes):
        r = pymupdf.Rect(b) * rot
        r.normalize()
        cv.box((r.x0 - 2, r.y0 - 2, r.x1 + 2, r.y1 + 2), n=i + 1, color=PALETTE[i % len(PALETTE)], width=2)
    out = evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question)
    return finish(cv.img, out, ctx.q.question, f"Answer: {value} {unit}   route: {ROUTE} (cost $0)", note=note)


def answer(ctx, q) -> Answer:
    text = ctx.q.question
    if not re.search(r"door schedule|door number", text, re.I) and not re.search(r"\bdoor \d{3}\.[A-Z]", text):
        return abstain(ROUTE, "no text-layer rule for this question")
    pdf = str(ctx.file)
    _, _, rows = door_schedule(pdf)
    if not rows:
        return abstain(ROUTE, "no door schedule found in text layer")

    value, unit, sel, note = None, "count", rows, ""
    m_door = re.search(r"door (\d{3}\.[A-Z]\d?)\b", text)
    if q.measure == "count" or re.match(r"(?i).*how many", text):
        if (m := re.search(r"door type ([A-C]\d)", text)):
            sel = [r for r in rows if r["type"] == m.group(1)]
            note = f"rows with type {m.group(1)}"
        elif (m := re.search(r"material ([A-Z]{2,3}-\d)", text)):
            sel = [r for r in rows if r["door_mat"] and r["door_mat"].split("/")[0] == m.group(1)]
            note = f"rows with door material {m.group(1)}"
        elif (ms := re.findall(r"\b((?:STL|AL)-\d)\b", text)) and "frame" in text:
            sel = [r for r in rows if r["frame"] in ms]
            note = f"rows with frame {' or '.join(ms)}"
        elif (m := re.search(r"note ([A-E])\b", text)):
            sel = [r for r in rows if m.group(1) in r["notes"]]
            note = f"rows carrying note {m.group(1)}"
        elif (m := re.search(r"(\d+'-\d+\")\s+wide", text)):
            sel = [r for r in rows if r["width"] == m.group(1)]
            note = f"rows {m.group(1)} wide"
        elif (m := re.search(r"(\d+'-\d+\")\s+high", text)):
            sel = [r for r in rows if r["height"] == m.group(1)]
            note = f"rows {m.group(1)} high"
        elif (m := re.search(r"for room (\d{3})", text)):
            sel = [r for r in rows if r["room"] == m.group(1)]
            note = f"rows for room {m.group(1)}"
        elif re.search(r"how many doors \(door numbers\)|how many doors are listed", text, re.I):
            note = "all door numbers in the schedule"
        else:
            return abstain(ROUTE, "door-schedule count not understood")
        value = len(sel)
    elif m_door:
        sel = [r for r in rows if r["door"] == m_door.group(1)]
        if not sel:
            return abstain(ROUTE, f"door {m_door.group(1)} not in schedule")
        r = sel[0]
        unit = ""
        if re.search(r"width and height", text):
            value = f"{r['width']} wide x {r['height']} high"
        elif "width" in text:
            value = r["width"]
        elif "height" in text:
            value = r["height"]
        elif "hardware" in text:
            value = r["hardware"]
        elif "material" in text:
            value = r["door_mat"]
        elif "type" in text:
            value = r["type"]
        else:
            return abstain(ROUTE, "door lookup not understood")
        note = f"door schedule row {r['door']}"
    else:
        return abstain(ROUTE, "door-schedule question not understood")

    boxes = []
    for r in sel:
        ws = r["words"][:14]
        boxes.append((min(w[0] for w in ws), min(w[1] for w in ws), max(w[2] for w in ws), max(w[3] for w in ws)))
    ans = Answer(value=value, unit=unit, route=ROUTE, confidence=0.8,
                 items=[{k: v for k, v in r.items() if k != "words"} for r in sel],
                 explanation=f"{value}: {note} ({len(rows)} rows parsed from the text layer)")
    if ctx.evidence:
        ans.evidence = _render(pdf, boxes, ctx, value, unit, note + f"; {len(rows)} schedule rows parsed")
    return ans
