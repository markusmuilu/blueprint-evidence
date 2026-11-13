"""Vector-PDF reading: paths, closed shapes, text with positions, and the drawing scale.

Nothing here knows about a particular question. `VectorPage` turns page 1 of a PDF into
  - `paths`: every drawn path (PyMuPDF get_drawings) as polylines in page points (y down), with
    its stroke colour, fill colour, line width and whether it is closed;
  - `spans`: every text span with its box, size and boldness;
  - `kind`: "vector" or "scanned" (a page that is mostly one image and has little linework);
  - `scale`: metres per PDF point, with the method that set it and a confidence.

Scale methods, each independent of the others:
  text       a printed ratio ("1:100", "M 1:50", "Massstab 1:200", "Echelle", "Scala", "Scale") on a
             sheet whose page size is a real paper size, so 1 pt on the page is 1/72 inch on paper
  bar        a scale bar: tick numbers (0, 1, 2, 5 ... with an "m") on one baseline, fitted linearly
  area       room-area labels ("12.5 m2") inside closed shapes: sqrt(printed m2 / shape area in pt2)
  dimension  dimension strings ("3.45", "345") centred on a parallel line: metres per pt from the
             median ratio of the printed length to the drawn length
When two methods agree within 1.5% the scale is high-confidence. When they disagree by more than
2% the scale is reported as conflicting and area answers abstain.
"""
from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field
from functools import lru_cache

import pymupdf

PT_M = 0.0254 / 72.0          # one PDF point on paper, in metres
PAPER = {"A0": (841, 1189), "A1": (594, 841), "A2": (420, 594), "A3": (297, 420), "A4": (210, 297),
         "ANSI D": (559, 864), "ANSI E": (864, 1118), "ARCH D": (610, 914), "ARCH E": (914, 1219),
         "LETTER": (216, 279), "TABLOID": (279, 432)}
SCALE_WORDS = r"(?:massstab|maßstab|ma.stab|\bmst\b|echelle|.chelle|scala|scale|skala|mittakaava|\bm\.?\s*1\s*:)"
SCALE_RE = re.compile(r"(?<![\d.:])1\s*:\s*(\d{1,4})(?![\d.:])")


@dataclass
class Path:
    kind: str                      # 's' stroke, 'f' fill, 'fs' both
    stroke: tuple | None
    fill: tuple | None
    width: float
    subpaths: list                 # list of point lists (page pt)
    closed: bool
    rect: tuple
    ops: str                       # item kinds, e.g. 'llll', 're', 'cccc'
    seq: int = 0

    @property
    def pts(self):
        return self.subpaths[0] if self.subpaths else []

    def style(self):
        r = lambda c: None if c is None else tuple(round(v, 2) for v in c)
        return (self.kind, r(self.stroke), r(self.fill), round(self.width or 0, 2))


@dataclass
class Span:
    text: str
    bbox: tuple
    size: float
    bold: bool

    @property
    def c(self):
        return ((self.bbox[0] + self.bbox[2]) / 2, (self.bbox[1] + self.bbox[3]) / 2)


@dataclass
class Scale:
    m_per_pt: float | None = None
    method: str = "none"
    confidence: float = 0.0
    detail: str = ""
    candidates: dict = field(default_factory=dict)

    @property
    def known(self):
        return self.m_per_pt is not None and self.method != "conflict"


def _bez(p0, p1, p2, p3, n=6):
    out = []
    for i in range(1, n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t * t * (1 - t), t ** 3
        out.append((a * p0.x + b * p1.x + c * p2.x + d * p3.x, a * p0.y + b * p1.y + c * p2.y + d * p3.y))
    return out


def _paths(page) -> list[Path]:
    out = []
    for i, d in enumerate(page.get_drawings()):
        subs, cur, ops = [], [], ""
        for it in d["items"]:
            op = it[0]
            ops += op
            if op == "l":
                a, b = it[1], it[2]
                if cur and math.hypot(cur[-1][0] - a.x, cur[-1][1] - a.y) < 1e-3:
                    cur.append((b.x, b.y))
                else:
                    if len(cur) > 1:
                        subs.append(cur)
                    cur = [(a.x, a.y), (b.x, b.y)]
            elif op == "c":
                a = it[1]
                if not (cur and math.hypot(cur[-1][0] - a.x, cur[-1][1] - a.y) < 1e-3):
                    if len(cur) > 1:
                        subs.append(cur)
                    cur = [(a.x, a.y)]
                cur.extend(_bez(*it[1:5]))
            elif op in ("re", "qu"):
                if len(cur) > 1:
                    subs.append(cur)
                cur = []
                if op == "re":
                    r = it[1]
                    q = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
                else:
                    qd = it[1]
                    q = [(qd.ul.x, qd.ul.y), (qd.ur.x, qd.ur.y), (qd.lr.x, qd.lr.y), (qd.ll.x, qd.ll.y)]
                subs.append(q + q[:1])
        if len(cur) > 1:
            subs.append(cur)
        if not subs:
            continue
        closed = bool(d.get("closePath")) or d["type"] in ("f", "fs") or ops in ("re", "qu") or (
            len(subs) == 1 and len(subs[0]) > 3 and math.hypot(subs[0][0][0] - subs[0][-1][0], subs[0][0][1] - subs[0][-1][1]) < 0.05)
        r = d["rect"]
        out.append(Path(d["type"], d.get("color"), d.get("fill"), d.get("width") or 0.0, subs, closed,
                        (r.x0, r.y0, r.x1, r.y1), ops, i))
    return out


def _spans(page) -> list[Span]:
    out = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            for s in ln["spans"]:
                t = s["text"].strip()
                if t:
                    out.append(Span(t, tuple(s["bbox"]), s["size"], "bold" in s["font"].lower() or bool(s["flags"] & 16)))
    return out


def paper_size(w_pt: float, h_pt: float) -> str | None:
    w, h = sorted((w_pt * 25.4 / 72, h_pt * 25.4 / 72))
    for name, (a, b) in PAPER.items():
        if abs(w - a) <= 0.015 * a + 2 and abs(h - b) <= 0.015 * b + 2:
            return name
    return None


# -- scale -----------------------------------------------------------------------------------
def scale_from_text(spans, page_rect):
    """Printed ratio, e.g. 'Massstab 1:100 (A3)'. Only trusted on a real paper size, or when the
    text names the paper size and the page matches it."""
    hits = []
    for s in spans:
        low = s.text.lower()
        for m in SCALE_RE.finditer(s.text):
            denom = int(m.group(1))
            if not 1 <= denom <= 5000:
                continue
            kw = bool(re.search(SCALE_WORDS, low)) or bool(re.search(r"\(a\d\)", low))
            hits.append((denom, kw, s))
    if not hits:
        return None
    denoms = {d for d, _, _ in hits}
    if len(denoms) > 1:   # several scales on one sheet (detail views); prefer keyword hits
        kwd = {d for d, kw, _ in hits if kw}
        if len(kwd) != 1:
            return {"m_per_pt": None, "detail": f"several printed scales {sorted(denoms)}", "spans": [h[2] for h in hits]}
        denoms = kwd
    denom = denoms.pop()
    paper = paper_size(page_rect.width, page_rect.height)
    named = re.search(r"\((A\d)\)", " ".join(h[2].text for h in hits))
    detail = f"printed 1:{denom}" + (f" on {paper} sheet" if paper else " (page is not a standard paper size)")
    if named and paper and named.group(1) != paper:
        detail += f"; text says {named.group(1)} but page is {paper}"
        return {"m_per_pt": None, "detail": detail, "spans": [h[2] for h in hits]}
    return {"m_per_pt": denom * PT_M, "detail": detail, "paper": paper, "denom": denom,
            "spans": [h[2] for h in hits if h[0] == denom]}


NUM_TICK = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(m)?$")


def scale_from_bar(spans):
    """Scale bar: numeric tick labels on one baseline, with an 'm' label or unit next to them."""
    best = None
    ticks = []
    for s in spans:
        m = NUM_TICK.match(s.text)
        if m and s.size < 12:
            ticks.append((float(m.group(1).replace(",", ".")), s, bool(m.group(2))))
    unit_m = [s for s in spans if s.text.strip() == "m"]
    rows = {}
    for v, s, has_m in ticks:
        key = round(s.bbox[1] / 2)
        rows.setdefault(key, []).append((v, s, has_m))
    for key, row in rows.items():
        row = sorted(row, key=lambda r: r[1].c[0])
        vals = [r[0] for r in row]
        if len(row) < 2 or vals[0] != 0 or any(b <= a for a, b in zip(vals, vals[1:])):
            continue
        has_m = any(r[2] for r in row) or any(abs(u.bbox[1] - row[-1][1].bbox[1]) < 12 and 0 < u.bbox[0] - row[-1][1].bbox[2] < 25
                                              for u in unit_m)
        if not has_m:
            continue
        xs = []
        for v, s, hm in row:
            if hm:   # '5 m': the number is left-aligned in the span; use the number's own centre
                frac = len(s.text.split()[0]) / max(len(s.text), 1)
                xs.append(s.bbox[0] + (s.bbox[2] - s.bbox[0]) * frac / 2)
            else:
                xs.append(s.c[0])
        n = len(vals)
        mx, my = sum(vals) / n, sum(xs) / n
        sxx = sum((v - mx) ** 2 for v in vals)
        b = sum((v - mx) * (x - my) for v, x in zip(vals, xs)) / sxx
        if b <= 0:
            continue
        resid = max(abs(my + b * (v - mx) - x) for v, x in zip(vals, xs))
        cand = {"m_per_pt": 1.0 / b, "resid_pt": resid, "n": n, "detail": f"scale bar ticks {vals} m",
                "spans": [r[1] for r in row]}
        if resid < 2.5 and (best is None or n > best["n"]):
            best = cand
    return best


AREA_LABEL = re.compile(r"^(\d+(?:[.,]\d+)?)\s*m(?:2|²|�)$")


def scale_from_area_labels(spans, polys):
    """Room-area labels inside closed shapes: each gives sqrt(printed / shape area in pt2)."""
    from shapely.geometry import Point
    ratios = []
    for s in spans:
        m = AREA_LABEL.match(s.text.replace(" ", " "))
        if not m:
            continue
        a = float(m.group(1).replace(",", "."))
        pt = Point(s.c)
        inside = [p for p in polys if p.area > 0 and p.contains(pt)]
        if a > 0 and inside:
            p = min(inside, key=lambda p: p.area)
            ratios.append(math.sqrt(a / p.area))
    if len(ratios) < 3:
        return None
    med = statistics.median(ratios)
    good = [r for r in ratios if abs(r - med) < 0.02 * med]
    if len(good) < max(3, 0.5 * len(ratios)):
        return None
    return {"m_per_pt": statistics.median(good), "detail": f"{len(good)} of {len(ratios)} room-area labels agree",
            "n": len(good)}


DIM_TXT = re.compile(r"^(\d{1,2}[.,]\d{2}|\d{3,4})$")


def scale_from_dimensions(spans, paths):
    """Dimension strings centred on a parallel line of matching length (metres '3.45' or cm '345')."""
    segs = []
    for p in paths:
        if p.kind != "s":
            continue
        for sp in p.subpaths:
            for a, b in zip(sp, sp[1:]):
                L = math.hypot(b[0] - a[0], b[1] - a[1])
                if L > 10:
                    segs.append((a, b, L))
    ratios = []
    for s in spans:
        m = DIM_TXT.match(s.text)
        if not m:
            continue
        t = m.group(1).replace(",", ".")
        val = float(t) if "." in t else float(t) / 100.0
        cx, cy = s.c
        w, h = s.bbox[2] - s.bbox[0], s.bbox[3] - s.bbox[1]
        horiz = w >= h
        best = None
        for a, b, L in segs:
            dx, dy = b[0] - a[0], b[1] - a[1]
            if horiz and abs(dy) > 0.02 * L or (not horiz) and abs(dx) > 0.02 * L:
                continue
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            along = abs(cx - mx) if horiz else abs(cy - my)
            off = abs(cy - my) if horiz else abs(cx - mx)
            if along < 0.1 * L and off < 1.5 * max(h if horiz else w, 4):
                if best is None or off < best[0]:
                    best = (off, L)
        if best:
            ratios.append(val / best[1])
    if len(ratios) < 4:
        return None
    med = statistics.median(ratios)
    good = [r for r in ratios if abs(r - med) < 0.01 * med]
    if len(good) < max(4, 0.6 * len(ratios)):
        return None
    return {"m_per_pt": statistics.median(good), "detail": f"{len(good)} of {len(ratios)} dimension strings agree", "n": len(good)}


def resolve_scale(cands: dict) -> Scale:
    vals = {k: v for k, v in cands.items() if v and v.get("m_per_pt")}
    if not vals:
        why = "; ".join(v["detail"] for v in cands.values() if v and v.get("detail")) or "no printed scale, scale bar, area labels or dimensions"
        return Scale(None, "none", 0.0, why, cands)
    order = ["bar", "area", "dimension", "text"]       # measured on the drawing itself first
    keys = sorted(vals, key=order.index)
    ref = vals[keys[0]]["m_per_pt"]
    agree = [k for k in keys if abs(vals[k]["m_per_pt"] - ref) <= 0.015 * ref]
    clash = [k for k in keys if abs(vals[k]["m_per_pt"] - ref) > 0.02 * ref]
    detail = "; ".join(f"{k}: {vals[k]['detail']} -> {vals[k]['m_per_pt'] * 100:.4f} cm/pt" for k in keys)
    if clash:
        return Scale(ref, "conflict", 0.2, detail, cands)
    conf = {1: 0.8, 2: 0.95}.get(len(agree), 0.97)
    if len(agree) == 1 and keys[0] == "text" and not vals["text"].get("paper"):
        conf = 0.5
    return Scale(ref, "+".join(agree), conf, detail, cands)


# -- page --------------------------------------------------------------------------------------
class VectorPage:
    def __init__(self, pdf_path: str, page_no: int = 0):
        doc = pymupdf.open(pdf_path)
        page = doc[page_no]
        self.path = pdf_path
        self.rect = page.rect
        self.rotation = page.rotation
        self.paths = _paths(page)
        self.spans = _spans(page)
        img_area = 0.0
        for info in page.get_image_info():
            b = pymupdf.Rect(info["bbox"]) & page.rect
            img_area += b.width * b.height if not b.is_empty else 0
        page_area = page.rect.width * page.rect.height
        self.image_frac = img_area / page_area if page_area else 0
        n_seg = sum(len(s) - 1 for p in self.paths for s in p.subpaths)
        self.n_seg = n_seg
        if self.image_frac > 0.5 and n_seg < 200:
            self.kind = "scanned"
        elif n_seg < 30 and len(self.spans) < 5:
            self.kind = "empty"
        else:
            self.kind = "vector"
        self.ocgs = doc.get_ocgs()
        self._scale = None

    def scale(self, polys=None) -> Scale:
        if self._scale is None or polys is not None:
            cands = {"text": scale_from_text(self.spans, self.rect), "bar": scale_from_bar(self.spans)}
            if polys:
                cands["area"] = scale_from_area_labels(self.spans, polys)
            cands["dimension"] = scale_from_dimensions(self.spans, self.paths)
            self._scale = resolve_scale(cands)
        return self._scale

    def render(self, zoom=None, max_px=2200):
        doc = pymupdf.open(self.path)
        page = doc[0]
        z = zoom or min(2.5, max_px / max(page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z))
        from PIL import Image
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return img, page.rotation_matrix * pymupdf.Matrix(z, z)


@lru_cache(maxsize=16)
def load_page(pdf_path: str) -> VectorPage:
    return VectorPage(pdf_path)
