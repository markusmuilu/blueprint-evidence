"""Upload handling: what a visitor may drop on the app, turned into drawings the routes can read.

    ingest(files, root, allowed, max_bytes) -> {"drawings": [...], "errors": [...], "notes": [...]}

Every drawing gets its own folder `root/<uid>/data/upload/` (so the 15 minute purge, the caches keyed by
`u<uid>` and the per-drawing twin listing all keep working) and a `label.json` beside `data/` with the name
shown to the visitor and an optional note.

  - several unrelated files become several drawings; files that share a stem (plan.pdf + plan.dxf) stay
    together as twins of one drawing, as before
  - a PDF with more than one page becomes one drawing per page ("file.pdf, page 2"): each page is written
    out as a single-page PDF, because the PDF routes read page 1 of the file they are given
  - TIFF (all frames) and SVG are converted to PNG; an image whose content is not what its extension says
    is converted too
  - DWG is converted to DXF when `dwg2dxf` (LibreDWG) is on the PATH, otherwise refused with a clear message
"""
from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

SUPPORTED_TEXT = "PDF, DXF, IFC, PNG, JPEG, TIFF or SVG"
DWG_MESSAGE = "DWG is not supported yet. Please export as PDF or DXF from your CAD program."
MAX_PAGES = 30                 # pages (PDF) or frames (TIFF) turned into drawings from one file
SVG_LONG_SIDE = 4000           # px: a plan SVG is drawn this large; the raster routes tile larger images
MAX_PIXELS = 400_000_000       # decoded pixels allowed for a TIFF frame (decompression bomb guard)
RASTER = {"png", "jpg", "jpeg", "tif", "tiff"}
ORDER = {"pdf": 0, "png": 1, "jpg": 1, "jpeg": 1, "ifc": 2, "dxf": 3}


class UploadError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def safe_name(name: str) -> str:
    name = Path((name or "").replace("\\", "/")).name
    name = re.sub(r"[^\w.\- ]+", "_", name).strip(" .")
    return name[:120] or "drawing"


def _split(name: str) -> tuple[str, str]:
    stem, dot, ext = name.rpartition(".")
    return (stem, ext.lower()) if dot and stem else (name, "")


# ---------------------------------------------------------------- converters (bytes in, bytes out)

def _png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _to_rgb_or_gray(img):
    """A PIL image in a mode PNG and the routes handle: L or RGB (16-bit, CMYK, palette and alpha flattened)."""
    from PIL import Image
    if img.mode in ("I;16", "I;16L", "I;16B", "I", "F"):
        import numpy as np
        a = np.asarray(img, dtype="float64")
        lo, hi = float(a.min()), float(a.max())
        a = (a - lo) / (hi - lo) * 255 if hi > lo else a * 0
        return Image.fromarray(a.astype("uint8"), "L")
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    if img.mode in ("1", "L", "RGB"):
        return img.convert("L" if img.mode in ("1", "L") else "RGB")
    return img.convert("RGB")


def raster_pages(data: bytes, name: str) -> list[tuple[str, bytes | None, str | None]]:
    """[(suffix, bytes or None, ext)] for an image file: None bytes means store the original as is."""
    from PIL import Image, UnidentifiedImageError
    # PIL's own bomb limit (89 MP) would refuse a large plan scan; MAX_PIXELS is the limit here
    limit, Image.MAX_IMAGE_PIXELS = Image.MAX_IMAGE_PIXELS, None
    try:
        return _raster_pages(Image, UnidentifiedImageError, data, name)
    finally:
        Image.MAX_IMAGE_PIXELS = limit


def _raster_pages(Image, UnidentifiedImageError, data, name):
    try:
        img = Image.open(io.BytesIO(data))
        fmt, (w, h) = img.format, img.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as ex:
        raise UploadError(400, f"{name}: could not be read as an image ({type(ex).__name__}). "
                               f"Is the file damaged, or named with the wrong extension?")
    if w * h > MAX_PIXELS:
        raise UploadError(400, f"{name}: the image is {w} x {h} px, which is more than this demo reads.")
    ext = _split(name)[1]
    frames = getattr(img, "n_frames", 1) if fmt == "TIFF" else 1
    if fmt == "PNG" and ext == "png" or fmt == "JPEG" and ext in ("jpg", "jpeg"):
        return [("", None, ext)]
    out = []
    try:
        for i in range(min(frames, MAX_PAGES)):
            img.seek(i)
            out.append((f"_f{i + 1}" if frames > 1 else "", _png_bytes(_to_rgb_or_gray(img)), "png"))
    except (OSError, ValueError, Image.DecompressionBombError) as ex:
        if not out:
            raise UploadError(400, f"{name}: could not be decoded ({type(ex).__name__}). "
                                   f"Please re-save it as PNG, JPEG or PDF.")
    return out


def svg_to_png(data: bytes, name: str) -> bytes:
    """Draw an SVG on white at about SVG_LONG_SIDE px on its long side, with PyMuPDF (pure pip, no cairo)."""
    import pymupdf
    try:
        if b"<svg" not in data[:200_000]:
            raise ValueError("no svg element")
        with pymupdf.open(stream=data, filetype="svg") as doc:
            r = doc[0].rect
            if r.width <= 0 or r.height <= 0:
                raise ValueError("no size")
            z = max(0.05, min(8.0, SVG_LONG_SIDE / max(r.width, r.height)))
            pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
            return pix.tobytes("png")
    except Exception as ex:
        raise UploadError(400, f"{name}: could not be drawn as an SVG ({type(ex).__name__}). "
                               f"Please export it as PDF or PNG.")


def pdf_pages(data: bytes, name: str) -> tuple[list[bytes | None], int]:
    """(per-page bytes, total pages). A one-page PDF is [None]: store the original unchanged."""
    import pymupdf
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise UploadError(400, f"{name}: could not be read as a PDF. The file may be damaged.")
    with doc:
        if doc.needs_pass or doc.is_encrypted:
            raise UploadError(400, f"{name}: the PDF is password protected. Please remove the password and upload it again.")
        n = doc.page_count
        if n == 0:
            raise UploadError(400, f"{name}: the PDF has no pages.")
        if n == 1:
            return [None], 1
        out = []
        for i in range(min(n, MAX_PAGES)):
            with pymupdf.open() as one:
                one.insert_pdf(doc, from_page=i, to_page=i)
                out.append(one.tobytes(garbage=3, deflate=True))
        return out, n


def dwg_to_dxf(data: bytes, name: str) -> bytes:
    exe = shutil.which("dwg2dxf")
    if not exe:
        raise UploadError(400, DWG_MESSAGE)
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in.dwg", Path(td) / "out.dxf"
        src.write_bytes(data)
        try:
            subprocess.run([exe, "-y", "-o", str(dst), str(src)], capture_output=True, timeout=90)
        except (subprocess.TimeoutExpired, OSError):
            pass
        if not dst.is_file() or dst.stat().st_size < 200:
            raise UploadError(400, f"{name}: this DWG could not be converted. Please export it as PDF or DXF from your CAD program.")
        out = dst.read_bytes()
    if b"SECTION" not in out[:4096]:
        raise UploadError(400, f"{name}: this DWG could not be converted. Please export it as PDF or DXF from your CAD program.")
    return out


def _check_text_format(data: bytes, ext: str, name: str) -> None:
    head = data[:4096]
    if ext == "dxf" and not (b"SECTION" in head or head.startswith(b"AutoCAD Binary DXF")):
        raise UploadError(400, f"{name}: this does not look like a DXF file. Please export it again as DXF.")
    if ext == "ifc" and b"ISO-10303-21" not in head[:1024]:
        raise UploadError(400, f"{name}: this does not look like an IFC file (zipped .ifczip files are not supported).")


# ---------------------------------------------------------------- ingest

def _units(name: str, data: bytes, allowed: set[str], notes: list[str]) -> list[dict]:
    """One uploaded file -> drawing candidates: [{label, stored, ext, data, note}]."""
    stem, ext = _split(name)
    if not ext:
        raise UploadError(400, f"{name}: the file has no extension. Supported types: {SUPPORTED_TEXT}.")
    if ext == "dwg" and "dwg" not in allowed:
        raise UploadError(400, DWG_MESSAGE)
    if ext not in allowed:
        raise UploadError(400, f"{name}: .{ext} files are not supported. Supported types: {SUPPORTED_TEXT}.")
    if not data:
        raise UploadError(400, f"{name}: the file is empty.")
    if ext == "dwg":
        return [{"label": name, "stored": f"{stem}.dxf", "ext": "dxf", "data": dwg_to_dxf(data, name),
                 "note": "Converted from DWG to DXF on upload."}]
    if ext == "svg":
        return [{"label": name, "stored": f"{stem}.png", "ext": "png", "data": svg_to_png(data, name),
                 "note": "Drawn from an SVG as a picture, so it is read as a scan: counts are estimates."}]
    if ext == "pdf":
        pages, total = pdf_pages(data, name)
        if total == 1:
            return [{"label": name, "stored": name, "ext": "pdf", "data": data, "note": None}]
        notes.append(f"{name} has {total} pages. Each page is its own drawing.")
        if total > MAX_PAGES:
            notes.append(f"Only the first {MAX_PAGES} pages of {name} were added.")
        return [{"label": f"{name}, page {i + 1}", "stored": f"{stem}_p{i + 1}.pdf", "ext": "pdf", "data": b,
                 "note": f"Page {i + 1} of {total}."} for i, b in enumerate(pages)]
    if ext in RASTER:
        parts = raster_pages(data, name)
        out = []
        for k, (suffix, b, e) in enumerate(parts):
            label = name if len(parts) == 1 else f"{name}, frame {k + 1}"
            stored = name if b is None else f"{stem}{suffix}.png"
            note = None if b is None else ("Converted to PNG on upload." if len(parts) == 1 else f"Frame {k + 1} of {len(parts)}.")
            out.append({"label": label, "stored": stored, "ext": e, "data": data if b is None else b, "note": note})
        if len(parts) > 1:
            notes.append(f"{name} has {len(parts)} frames. Each frame is its own drawing.")
        return out
    _check_text_format(data, ext, name)
    return [{"label": name, "stored": name, "ext": ext, "data": data, "note": None}]


def _joins(drawing: dict, unit: dict) -> bool:
    """A file joins an existing drawing as its twin: a different kind of file, never a second image."""
    exts = {f["ext"] for f in drawing["files"]}
    if unit["ext"] in exts:
        return False
    return not (unit["ext"] in RASTER and exts & RASTER)


def ingest(files: list[tuple[str, bytes]], root: Path, allowed: set[str], max_bytes: int) -> dict:
    """Store uploaded files under `root` as drawings. Raises UploadError(status, message) when nothing usable came in."""
    errors: list[str] = []
    notes: list[str] = []
    groups: dict[str, list[dict]] = {}          # lowercase stem -> drawings still open for twins
    order: list[dict] = []
    for raw_name, data in files:
        name = safe_name(raw_name)
        try:
            if len(data) > max_bytes:
                raise UploadError(413, f"{name}: larger than {max_bytes // (1024 * 1024)} MB.")
            units = _units(name, data, allowed, notes)
        except UploadError as ex:
            errors.append(ex.message)
            continue
        key = _split(name)[0].lower()
        for k, u in enumerate(units):
            target = None
            if k == 0:
                target = next((d for d in groups.get(key, []) if _joins(d, u)), None)
            if target is None:
                target = {"files": [], "label": u["label"], "note": u["note"]}
                groups.setdefault(key, []).append(target)
                order.append(target)
            target["files"].append(u)
    if not order:
        status = 413 if errors and all("larger than" in e for e in errors) else 400
        raise UploadError(status, " ".join(errors) or "No file received.")

    drawings = []
    try:
        for d in order:
            uid = uuid.uuid4().hex[:12]
            folder = root / uid / "data" / "upload"
            folder.mkdir(parents=True, exist_ok=True)
            saved = []
            for u in d["files"]:
                # the OCR and detector caches are keyed by file stem, so every drawing gets a unique stem;
                # twins stored together keep a shared stem
                stored = f"u{uid}-{u['stored']}"
                (folder / stored).write_bytes(u["data"])
                saved.append({"name": u["stored"], "stored": stored, "ext": u["ext"], "bytes": len(u["data"])})
            main_u = sorted(d["files"], key=lambda f: ORDER.get(f["ext"], 9))[0]
            main = next(s for s in saved if s["name"] == main_u["stored"] and s["ext"] == main_u["ext"])
            label = main_u["label"]
            note = main_u["note"]
            if len(d["files"]) > 1 and any(f is not main_u and f["note"] for f in d["files"]):
                note = " ".join(x for x in (note, *[f["note"] for f in d["files"] if f is not main_u and f["note"]]) if x)
            (root / uid / "label.json").write_text(json.dumps({"name": label, "note": note}), encoding="utf-8")
            drawings.append({"path": f"upload:{uid}/{main['stored']}", "name": label, "ext": main["ext"],
                             "files": saved, "note": note})
    except OSError:
        for dr in drawings:
            shutil.rmtree(root / dr["path"].split(":")[1].split("/")[0], ignore_errors=True)
        raise UploadError(500, "The upload could not be stored. Please try again.")
    return {"drawings": drawings, "errors": errors, "notes": notes}


def read_label(root: Path, uid: str) -> dict:
    try:
        return json.loads((root / uid / "label.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
