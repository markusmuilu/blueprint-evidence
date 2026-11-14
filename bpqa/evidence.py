"""Evidence overlays: a PNG per answer showing exactly what was counted or measured.

Counted items are boxed and numbered, measured rooms are filled and labelled with the value
that went into the sum. A header strip repeats the question, the answer and the route so the
image can be checked on its own.
"""
from __future__ import annotations

import hashlib
import json
import textwrap
from pathlib import Path

import matplotlib
from PIL import Image, ImageDraw, ImageFont

FONT_PATH = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"
# Colours follow DESIGN.md (hue 235-245 blueprint blue for what was proven, amber for what to check), darkened
# from the app's dark-theme values so they hold on white paper: BLUE is oklch(0.50 0.13 240), 5.8:1 on white.
BLUE = (0, 106, 165)                  # counted / measured items of a proven answer
AMBER = (231, 140, 8)                 # uncertain items, estimates: oklch(0.72 0.16 65)
GREY = (150, 150, 150)                # read but not counted, context
INK = (28, 48, 65)                    # header text, numbers on amber tabs
PASS = (0, 135, 75)
FAIL = (200, 45, 50)
# several items that must be told apart (rooms side by side): tones of the same blueprint blue
PALETTE = [BLUE, (18, 146, 192), (0, 79, 130), (70, 130, 170), (0, 120, 140), (40, 95, 150)]


def _ink_on(color) -> tuple:
    """Number-tab text colour: white on dark tabs, ink on light ones (amber)."""
    r, g, b = color[:3]
    return INK if 0.299 * r + 0.587 * g + 0.114 * b > 150 else (255, 255, 255)


# ---------------------------------------------------------------- marks sidecar
# Every numbered box and every filled room polygon a route draws is also recorded, in the pixel frame of the
# image it is drawn on, and saved next to the PNG as `<stem>.marks.json` by finish(). The app pins, crops
# and numbers items from this file, so every route (raster, PDF linework, DXF world coordinates, IFC
# geometry) gets pins without each route converting its own coordinates.

def _tone(color) -> str:
    rgb = tuple(color[:3]) if color is not None else None
    if rgb == AMBER or rgb == (170, 100, 0):
        return "amber"
    if rgb == GREY:
        return "grey"
    return "blue"


def _marks(img: Image.Image) -> list:
    return img.info.setdefault("bp_marks", [])


def _record_box(img, rect, n, color):
    if n is None or (color is not None and _tone(color) == "grey"):
        return
    x0, y0, x1, y1 = (float(v) for v in rect)
    _marks(img).append({"kind": "box", "n": n, "tone": _tone(color),
                        "rect": [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]})


def _record_poly(img, pts, fill):
    """Filled, translucent polygons are the measured rooms (context outlines are opaque or unfilled)."""
    if fill is None or len(fill) < 4 or fill[3] >= 255 or len(pts) < 3:
        return
    pts = [(float(x), float(y)) for x, y in pts]
    step = max(1, len(pts) // 64)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    polys = [m for m in _marks(img) if m["kind"] == "poly"]
    _marks(img).append({"kind": "poly", "i": len(polys), "tone": _tone(fill),
                        "pts": [[round(x, 1), round(y, 1)] for x, y in pts[::step]],
                        "rect": [min(xs), min(ys), max(xs), max(ys)]})


class _RecordingDraw:
    """ImageDraw wrapper: routes that draw polygons straight on `cv.d` are recorded too."""

    def __init__(self, img, d):
        self._img, self._d = img, d

    def polygon(self, xy, fill=None, outline=None, width=1):
        _record_poly(self._img, list(xy), fill)
        return self._d.polygon(xy, fill=fill, outline=outline, width=width)

    def __getattr__(self, name):
        return getattr(self._d, name)


def font(size: int):
    try:
        return ImageFont.truetype(str(FONT_PATH), size)
    except OSError:
        return ImageFont.load_default()


def evidence_path(out_dir: Path, path: str, question: str) -> Path:
    h = hashlib.sha1((path + "|" + question).encode()).hexdigest()[:10]
    stem = Path(path).stem[:40].replace(" ", "_")
    return out_dir / f"{stem}__{h}.png"


class VectorCanvas:
    """Renders 2D vector geometry (world units, y up) into a PIL image."""

    def __init__(self, bounds, width=1400, margin=30):
        x0, y0, x1, y1 = bounds
        self.x0, self.y1 = x0, y1
        self.s = (width - 2 * margin) / max(x1 - x0, 1e-9)
        self.m = margin
        h = int((y1 - y0) * self.s + 2 * margin)
        self.img = Image.new("RGB", (width, max(h, 200)), "white")
        self.d = ImageDraw.Draw(self.img, "RGBA")
        self.img.info["bp_marks"] = []
        # world (x right, y up) -> image pixels: px = m + (x - x0) * s, py = m + (y1 - y) * s
        self.img.info["bp_frame"] = {"kind": "world", "x0": x0, "y1": y1, "s": self.s, "m": margin}

    def px(self, x, y):
        return (self.m + (x - self.x0) * self.s, self.m + (self.y1 - y) * self.s)

    def line(self, pts, color=(150, 150, 150), width=1):
        if len(pts) >= 2:
            self.d.line([self.px(*p) for p in pts], fill=color, width=width)

    def poly(self, pts, fill=None, outline=None, width=2):
        if len(pts) >= 3:
            _record_poly(self.img, [self.px(*p) for p in pts], fill)
            self.d.polygon([self.px(*p) for p in pts], fill=fill, outline=outline, width=width)

    def circle(self, c, r, color=(150, 150, 150)):
        x, y = self.px(*c)
        rr = max(r * self.s, 1)
        self.d.ellipse([x - rr, y - rr, x + rr, y + rr], outline=color)

    def text(self, xy, s, size=12, color=(0, 0, 0), anchor="mm", bg=True):
        x, y = self.px(*xy)
        f = font(size)
        if bg:
            box = self.d.textbbox((x, y), s, font=f, anchor=anchor)
            self.d.rectangle([box[0] - 2, box[1] - 1, box[2] + 2, box[3] + 1], fill=(255, 255, 255, 210))
        self.d.text((x, y), s, fill=color, font=f, anchor=anchor)

    def box(self, bounds, n=None, color=BLUE, pad=0.08):
        x0, y0, x1, y1 = bounds
        a, b = self.px(x0 - pad, y1 + pad), self.px(x1 + pad, y0 - pad)
        self.d.rectangle([a, b], outline=color, width=3)
        _record_box(self.img, (a[0], a[1], b[0], b[1]), n, color)
        if n is not None:
            f = font(14)
            self.d.rectangle([a[0], a[1] - 18, a[0] + 9 * len(str(n)) + 8, a[1]], fill=color)
            self.d.text((a[0] + 4, a[1] - 17), str(n), fill=_ink_on(color), font=f)


class ImageCanvas:
    """Draws on top of a raster image (rendered PDF page or a scan), pixel coordinates."""

    def __init__(self, img: Image.Image):
        self.img = img.convert("RGB")
        self.img.info["bp_marks"] = []
        self.img.info["bp_frame"] = {"kind": "pixel"}
        self.d = _RecordingDraw(self.img, ImageDraw.Draw(self.img, "RGBA"))
        # evidence images are shown at most ~1600 px wide: scale strokes and numbers so they stay readable
        self.k = max(1.0, max(self.img.size) / 1600)

    def box(self, bounds, n=None, color=BLUE, width=3):
        x0, y0, x1, y1 = bounds
        w = max(1, round(width * self.k)) if width > 1 else max(1, round(self.k))
        self.d.rectangle([x0, y0, x1, y1], outline=color, width=w)
        _record_box(self.img, bounds, n, color)
        if n is not None:
            fs = round(14 * self.k)
            f = font(fs)
            th = fs + 4
            tw = round(0.62 * fs * len(str(n))) + round(8 * self.k)
            self.d.rectangle([x0, y0 - th, x0 + tw, y0], fill=color)
            self.d.text((x0 + round(4 * self.k), y0 - th + 1), str(n), fill=_ink_on(color), font=f)

    def fill(self, bounds, color=BLUE + (60,)):
        self.d.rectangle(bounds, fill=color)


_STASH: dict = {}      # evidence path -> (overlay without header, question, answer line, note)


def finish(img: Image.Image, out: Path, question: str, answer_line: str, note: str = "", max_w=1600,
           checks=None) -> str:
    """Add the header strip and save. Returns the path as a string.

    `checks` (from bpqa/checks.py) are listed under the note, green for pass and red for fail.
    """
    if len(_STASH) > 8:
        _STASH.clear()
    _STASH[str(out)] = (img, question, answer_line, note)
    marks, frame = list(img.info.get("bp_marks") or []), img.info.get("bp_frame")
    src_w = img.width
    if img.width > max_w:
        img = img.resize((max_w, int(img.height * max_w / img.width)), Image.LANCZOS)
    f, fb = font(15), font(19)
    cw = max(img.width, 900)                      # narrow scans: keep the header readable
    lines = textwrap.wrap(question, max(40, cw // 9))[:3]
    note_lines = textwrap.wrap(note, max(50, cw // 7))[:3] if note else []
    check_lines = []
    for c in checks or []:
        st = c["status"] if isinstance(c, dict) else c.status
        nm = c["name"] if isinstance(c, dict) else c.name
        dt = c["detail"] if isinstance(c, dict) else c.detail
        mark = {"pass": "PASS", "fail": "FAIL"}.get(st, st.upper())
        for i, ln in enumerate(textwrap.wrap(f"check {mark}  {nm}: {dt}", max(50, cw // 7))[:2]):
            check_lines.append((ln if i == 0 else "      " + ln, st))
    head_h = 14 + 20 * len(lines) + 26 + 18 * len(note_lines) + 18 * len(check_lines) + 8
    canvas = Image.new("RGB", (cw, img.height + head_h), "white")
    d = ImageDraw.Draw(canvas)
    y = 8
    for ln in lines:
        d.text((12, y), ln, fill=INK, font=f)
        y += 20
    d.text((12, y + 2), answer_line, fill=(170, 100, 0) if "ESTIMATE" in answer_line.upper() else
           FAIL if "ABSTAIN" in answer_line.upper() else BLUE, font=fb)
    y += 28
    for ln in note_lines:
        d.text((12, y), ln, fill=(90, 90, 90), font=font(13))
        y += 18
    for ln, st in check_lines:
        d.text((12, y), ln, fill=PASS if st == "pass" else FAIL, font=font(13))
        y += 18
    d.line([(0, head_h - 2), (cw, head_h - 2)], fill=(200, 200, 200), width=2)
    canvas.paste(img, (0, head_h))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    write_marks(out, marks, frame, img.width / src_w, head_h, img.width, img.height)
    return str(out)


def marks_path(png: Path) -> Path:
    return png.with_name(png.stem + ".marks.json")


def write_marks(out: Path, marks: list, frame: dict | None, k: float, head_h: int, w: int, h: int) -> None:
    """Sidecar for the app: marks in body pixels of the saved PNG (below the header strip)."""
    def sc(v):
        return round(v * k, 1)
    rows = []
    for m in marks:
        r = dict(m)
        r["rect"] = [sc(v) for v in m["rect"]]
        if "pts" in m:
            r["pts"] = [[sc(x), sc(y)] for x, y in m["pts"]]
        rows.append(r)
    fr = dict(frame) if frame else None
    if fr and fr.get("kind") == "world":
        fr.update(s=fr["s"] * k, m=fr["m"] * k)
    elif fr:
        fr["scale"] = k                 # source pixel -> body pixel
    try:
        marks_path(out).write_text(json.dumps({"v": 1, "w": w, "h": h, "header": head_h, "frame": fr, "marks": rows}),
                                   encoding="utf-8")
    except OSError:
        pass


def add_checks(path: str, checks, abstained: bool = False, value=None, estimate: bool = False,
               changed: str = "") -> str:
    """Redraw an evidence image's header with the checks that fired (and the abstention or estimate, if any).
    changed: set when a check replaced the route's count; the header then says so before the route's note,
    which describes what the route itself read."""
    if path not in _STASH:
        return path
    img, question, answer_line, note = _STASH[path]
    if estimate:
        answer_line = f"Answer: {value}   (ESTIMATE: a consistency check failed, see below)"
        if changed:
            answer_line = f"Answer: {value}   (ESTIMATE: {changed}, see below)"
            note = f"The boxes show what the route read on its own. {note}"
    elif abstained:
        answer_line = f"ABSTAINED: would answer {value}, a consistency check failed"
        note = (note + "; " if note else "") + "the boxes show what the route read; the failed check is below"
    return finish(img, Path(path), question, answer_line, note, checks=checks)
