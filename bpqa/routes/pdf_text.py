"""PDF text-layer route: read schedules straight from the vector text of a drawing sheet.

Implemented: door schedules (rows keyed by door numbers like 118.A1), and any other schedule table through
the generic reader in bpqa/schedules.py (columns from header words or the drawn grid; counts sum a Count
column when the table has one, lookups read the column the question names). Each row is rebuilt from
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

from .. import schedules
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
    return finish(cv.img, out, ctx.q.question, f"Answer: {value} {unit}   route: {ROUTE} (cost €0)", note=note)


def answer(ctx, q) -> Answer:
    a = door_answer(ctx, q)
    if a.answered:
        return a
    g = generic_schedule(ctx, q)
    if g.answered:
        return g
    return abstain(ROUTE, f"{a.abstain}; generic schedules: {g.abstain}")


def door_answer(ctx, q) -> Answer:
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


# ------------------------------------------------------------------ generic schedules (bpqa/schedules.py)

_STOP = set("""the a an of in on for to and or is are was be by with what which how many much does do this that these
those sheet drawings drawing listed list scheduled schedule schedules there their its it as at from type types total
all any each every have has kind door doors window windows room rooms item items number counted count area combined
whose description starts start begins begin if consists consist needed need units unit""".split())


def _num(s):
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", s or "")
    return float(m.group(0).replace(",", "")) if m else None


def _pick_table(tabs, text):
    f = text.lower()
    best, best_s = None, -1
    for t in tabs:
        s = 0
        title = t.title.lower()
        for w in re.findall(r"[a-z]{4,}", title):
            if w in f and w != "schedule":
                s += 3
        if "type" in title and re.search(r"\btypes?\b|\btype mark", f):
            s += 1
        if "number" in title and re.search(r"door number", f):
            s += 2
        cells = {v for r in t.rows for v in r.values() if v}
        for tok in re.findall(r"\b[A-Z]{1,3}\d{0,3}(?:[-.][A-Z0-9]+)?\b|\b\d{3}(?:\.[A-Z]\d?)?\b", text):
            if tok in cells:
                s += 4
        s += 0.01 * len(t.rows)
        if s > best_s:
            best, best_s = t, s
    return best


@lru_cache(maxsize=8)
def legend(pdf_path: str) -> dict:
    """Finish / type legends printed on the sheet: a code alone on a text line ('W2') followed by a
    descriptive line ('3/4" PREFINISHED STAINED/SEALED PLYWOOD ...'). Schedule cells are followed by
    other codes, not by descriptions, so they do not produce entries."""
    out = {}
    with pymupdf.open(pdf_path) as doc:
        for page in doc:
            L = [x.strip() for x in page.get_text("text").splitlines()]
            for i in range(len(L) - 1):
                if re.fullmatch(r"[A-Z]{1,2}\d{1,2}", L[i]) and len(re.findall(r"[A-Za-z]{3,}", L[i + 1])) >= 2 \
                        and not re.fullmatch(r"[A-Z]{1,2}\d{1,2}", L[i + 1]):
                    if len(L[i + 1]) > len(out.get(L[i], "")):   # the longest description wins over 'SEE ELEV.'
                        out[L[i]] = L[i + 1]
    return out


def generic_schedule(ctx, q):
    text = ctx.q.question
    f = text.lower()
    tabs = schedules.tables(str(ctx.file))
    if not tabs:
        return abstain(ROUTE, "no schedule table found in the text layer")
    if not re.search(r"schedul", f) and not any("schedule" in t.title.lower() for t in tabs):
        return abstain(ROUTE, "no schedule named in the question or titled on the sheet")
    t = _pick_table(tabs, text)
    rows = list(range(len(t.rows)))
    notes = [f"table '{t.title or 'untitled'}' ({len(t.rows)} rows, columns {', '.join(t.columns)})"]
    body = re.sub(r"^.*?drawings?(\s*\([^)]*\))?\s*,\s*", "", text)          # drop "On sheet X of the Y drawings,"
    # 1) exact codes printed in the table (type marks, room / door numbers, finish codes)
    codes = re.findall(r"\b[A-Z]{1,3}\d{0,3}(?:[-.][A-Z0-9]+)?\b|\b\d{3}(?:\.[A-Z]\d?)?\b", body)
    code_cols = {}
    for c in codes:
        for col in t.columns:
            if any(t.rows[i].get(col, "").strip() == c for i in rows):
                code_cols.setdefault(col, []).append(c)
                break
    if code_cols:
        col, vals = max(code_cols.items(), key=lambda kv: len(kv[1]))
        allw = re.search(r"all (four )?walls", f)
        if allw:
            wcols = [c for c in t.columns if "wall" in c.lower()]
            rows = [i for i in rows if all(t.rows[i].get(c, "").strip() == vals[0] for c in wcols)]
            notes.append(f"rows with {vals[0]} in every wall column ({', '.join(wcols)})")
        else:
            rows = [i for i in rows if t.rows[i].get(col, "").strip() in vals]
            notes.append(f"rows with {col} = {' or '.join(vals)}")
    # 2) quoted text with 'starts with'
    m = re.search(r"start\w* with '([^']+)'", text)
    if m:
        pre = m.group(1).upper()
        rows = [i for i in rows if any(v.upper().startswith(pre) for v in t.rows[i].values())]
        notes.append(f"rows with a cell starting '{pre}'")
    # 3) descriptive words printed in the rows ("Marvin Essential sliding", "3rd floor", "locker")
    if not code_cols:
        words = [w for w in re.findall(r"[a-z0-9][a-z0-9'\-]+", body.lower()) if w not in _STOP and len(w) >= 3]
        rowtext = {i: " ".join(t.rows[i].values()).lower() for i in rows}
        # longest run of consecutive question words found in some row
        phrases = []
        toks = re.findall(r"[a-z0-9][a-z0-9'\-]+", body.lower())
        for n in (3, 2):
            for k in range(len(toks) - n + 1):
                ph = " ".join(toks[k:k + n])
                if all(x not in _STOP for x in toks[k:k + n][:1]) and any(ph in rowtext[i] for i in rows):
                    phrases.append(ph)
        used = []
        for ph in phrases:
            if any(ph in u for u in used):
                continue
            used.append(ph)
        singles = [w for w in words if not any(w in u for u in used) and len(w) >= 5 and
                   any(re.search(rf"\b{re.escape(w.rstrip('s'))}", rowtext[i]) for i in rows) and
                   not all(re.search(rf"\b{re.escape(w.rstrip('s'))}", rowtext[i]) for i in rows)]
        terms = used + singles[:2]
        if terms:
            if re.search(r"women.s and men.s|men.s and women.s", f):
                terms = [x for x in terms if "women" not in x and "men's" not in x] or terms
            rows = [i for i in rows if all(re.search(rf"\b{re.escape(x.rstrip('s'))}", rowtext[i]) for x in terms)]
            notes.append(f"rows containing {', '.join(repr(x) for x in terms)}")
    if not rows:
        return abstain(ROUTE, "no schedule row matches the question; " + "; ".join(notes))
    cnt_col = t.col("count") or t.col("qty") or t.col("quantity")
    area_col = t.col("area")
    sel = [t.rows[i] for i in rows]
    unit = ""
    if re.search(r"\bhow many\b", f) and (not re.search(r"what is|what are", f) or re.search(r"their total area", f)):
        if cnt_col and not re.search(r"how many rooms", f):
            vals = [_num(r.get(cnt_col)) for r in sel]
            value = int(sum(v for v in vals if v is not None))
            notes.append(f"sum of column {cnt_col}")
        else:
            value = len(sel)
            notes.append("number of rows")
        unit = "count"
        if re.search(r"total area|and what is their total area|their total area", f) and area_col:
            a = sum(v for v in (_num(r.get(area_col)) for r in sel) if v is not None)
            value = f"{value} rooms, {a:g} SF"
            unit = ""
    elif re.search(r"largest|biggest|smallest", f) and area_col:
        key = (lambda i: _num(t.rows[i].get(area_col)) or 0)
        i = (max if re.search(r"largest|biggest", f) else min)(rows, key=key)
        rows, sel = [i], [t.rows[i]]
        r = t.rows[i]
        value = " ".join(v for c, v in r.items() if v and c != area_col and ("room" in c.lower() or "name" in c.lower() or "number" in c.lower())) + f" ({r.get(area_col)})"
        notes.append(f"largest {area_col}")
    elif re.search(r"\barea\b", f) and area_col and not re.search(r"what is the (floor|ceiling|wall|base)", f):
        vals = [_num(r.get(area_col)) for r in sel]
        value = round(sum(v for v in vals if v is not None), 2)
        unit = "SF" if any("SF" in (r.get(area_col) or "") for r in sel) else ""
        notes.append(f"sum of column {area_col}")
    else:
        # lookup: the column(s) whose label words appear in the question
        cols = []
        for c in t.columns:
            words_c = [w for w in re.findall(r"[a-z]+", c.lower()) if w not in ("type",) or c.lower() == "type"]
            if words_c and all(w in f for w in words_c):
                cols.append(c)
        for c in list(cols):
            if any(c != d and c.lower() in d.lower() for d in cols):
                cols.remove(c)
        if not cols:
            return abstain(ROUTE, "schedule lookup: no column named in the question; " + "; ".join(notes))
        vals = []
        for c in cols:
            vs = list(dict.fromkeys(r.get(c, "") for r in sel if r.get(c, "")))
            vals.append(f"{c}: {', '.join(vs) if vs else '-'}")
        value = "; ".join(vals)
        notes.append(f"column {', '.join(cols)} of {len(sel)} row(s)")
        leg = legend(str(ctx.file))
        meant = [f"{c} = {leg[c]}" for c in dict.fromkeys(re.findall(r"\b[A-Z]{1,2}\d{1,2}\b", value)) if c in leg]
        if meant:
            value += " (legend on the sheet: " + "; ".join(meant) + ")"
            notes.append("code meanings from the legend printed on the sheet")
    ans = Answer(value=value, unit=unit, route=ROUTE, confidence=0.75,
                 items=[{"row": r} for r in sel][:200],
                 explanation=f"{value}: " + "; ".join(notes) + f" (generic schedule reader, {ctx.file.name})")
    if ctx.evidence:
        boxes = [t.row_boxes[i] for i in rows]
        ans.evidence = _render(str(ctx.file), boxes, ctx, value, unit, "; ".join(notes)[:300])
    return ans
