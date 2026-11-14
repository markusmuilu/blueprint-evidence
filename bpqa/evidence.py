"""Evidence overlays: a PNG per answer showing exactly what was counted or measured.

Counted items are boxed and numbered, measured rooms are filled and labelled with the value
that went into the sum. A header strip repeats the question, the answer and the route so the
image can be checked on its own.
"""
from __future__ import annotations

import hashlib
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

    def px(self, x, y):
        return (self.m + (x - self.x0) * self.s, self.m + (self.y1 - y) * self.s)

    def line(self, pts, color=(150, 150, 150), width=1):
        if len(pts) >= 2:
            self.d.line([self.px(*p) for p in pts], fill=color, width=width)

    def poly(self, pts, fill=None, outline=None, width=2):
        if len(pts) >= 3:
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
        if n is not None:
            f = font(14)
            self.d.rectangle([a[0], a[1] - 18, a[0] + 9 * len(str(n)) + 8, a[1]], fill=color)
            self.d.text((a[0] + 4, a[1] - 17), str(n), fill=_ink_on(color), font=f)


class ImageCanvas:
    """Draws on top of a raster image (rendered PDF page or a scan), pixel coordinates."""

    def __init__(self, img: Image.Image):
        self.img = img.convert("RGB")
        self.d = ImageDraw.Draw(self.img, "RGBA")
        # evidence images are shown at most ~1600 px wide: scale strokes and numbers so they stay readable
        self.k = max(1.0, max(self.img.size) / 1600)

    def box(self, bounds, n=None, color=BLUE, width=3):
        x0, y0, x1, y1 = bounds
        w = max(1, round(width * self.k)) if width > 1 else max(1, round(self.k))
        self.d.rectangle([x0, y0, x1, y1], outline=color, width=w)
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
    return str(out)


def add_checks(path: str, checks, abstained: bool = False, value=None, estimate: bool = False) -> str:
    """Redraw an evidence image's header with the checks that fired (and the abstention or estimate, if any)."""
    if path not in _STASH:
        return path
    img, question, answer_line, note = _STASH[path]
    if estimate:
        answer_line = f"Answer: {value}   (ESTIMATE: a consistency check failed, see below)"
    elif abstained:
        answer_line = f"ABSTAINED: would answer {value}, a consistency check failed"
        note = (note + "; " if note else "") + "the boxes show what the route read; the failed check is below"
    return finish(img, Path(path), question, answer_line, note, checks=checks)
