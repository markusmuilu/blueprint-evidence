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
PALETTE = [(230, 57, 70), (29, 120, 196), (46, 160, 67), (240, 140, 0), (142, 68, 173), (0, 150, 150)]


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

    def box(self, bounds, n=None, color=PALETTE[0], pad=0.08):
        x0, y0, x1, y1 = bounds
        a, b = self.px(x0 - pad, y1 + pad), self.px(x1 + pad, y0 - pad)
        self.d.rectangle([a, b], outline=color, width=3)
        if n is not None:
            f = font(13)
            self.d.rectangle([a[0], a[1] - 16, a[0] + 9 * len(str(n)) + 6, a[1]], fill=color)
            self.d.text((a[0] + 3, a[1] - 15), str(n), fill="white", font=f)


class ImageCanvas:
    """Draws on top of a raster image (rendered PDF page or a scan), pixel coordinates."""

    def __init__(self, img: Image.Image):
        self.img = img.convert("RGB")
        self.d = ImageDraw.Draw(self.img, "RGBA")

    def box(self, bounds, n=None, color=PALETTE[0], width=3):
        x0, y0, x1, y1 = bounds
        self.d.rectangle([x0, y0, x1, y1], outline=color, width=width)
        if n is not None:
            f = font(14)
            self.d.rectangle([x0, y0 - 17, x0 + 9 * len(str(n)) + 6, y0], fill=color)
            self.d.text((x0 + 3, y0 - 16), str(n), fill="white", font=f)

    def fill(self, bounds, color=(46, 160, 67, 70)):
        self.d.rectangle(bounds, fill=color)


def finish(img: Image.Image, out: Path, question: str, answer_line: str, note: str = "", max_w=1600) -> str:
    """Add the header strip and save. Returns the path as a string."""
    if img.width > max_w:
        img = img.resize((max_w, int(img.height * max_w / img.width)), Image.LANCZOS)
    f, fb = font(15), font(19)
    lines = textwrap.wrap(question, 150)[:3]
    note_lines = textwrap.wrap(note, 170)[:3] if note else []
    head_h = 14 + 20 * len(lines) + 26 + 18 * len(note_lines) + 8
    canvas = Image.new("RGB", (img.width, img.height + head_h), "white")
    d = ImageDraw.Draw(canvas)
    y = 8
    for ln in lines:
        d.text((12, y), ln, fill=(40, 40, 40), font=f)
        y += 20
    d.text((12, y + 2), answer_line, fill=(200, 30, 50), font=fb)
    y += 28
    for ln in note_lines:
        d.text((12, y), ln, fill=(90, 90, 90), font=font(13))
        y += 18
    d.line([(0, head_h - 2), (img.width, head_h - 2)], fill=(200, 200, 200), width=2)
    canvas.paste(img, (0, head_h))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, optimize=True)
    return str(out)
