"""Upload handling: formats, several files at once, multi-page PDFs, clear errors (app/uploads.py, POST /api/upload)."""
import glob
import io

import pytest

pymupdf = pytest.importorskip("pymupdf")
from PIL import Image
from fastapi.testclient import TestClient

from app import server, uploads


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "UPLOADS", tmp_path)
    monkeypatch.setattr(server, "MAX_UPLOAD", 30 * 1024 * 1024)
    return TestClient(server.app)


def pdf(pages=1):
    doc = pymupdf.open()
    for i in range(pages):
        p = doc.new_page(width=842, height=595)
        p.insert_text((72, 72), f"Sheet {i + 1}")
        p.draw_rect(pymupdf.Rect(100, 100, 300, 300))
    return doc.tobytes()


def png(size=(200, 100), fmt="PNG", mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, size, "white").save(buf, fmt)
    return buf.getvalue()


def tiff(frames=1, mode="L"):
    imgs = [Image.new(mode, (120, 80), 200 - 30 * i) for i in range(frames)]
    buf = io.BytesIO()
    imgs[0].save(buf, "TIFF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def tiff16():
    import numpy as np
    buf = io.BytesIO()
    Image.fromarray((np.arange(60 * 40).reshape(40, 60) * 10).astype("uint16")).save(buf, "TIFF")
    return buf.getvalue()


SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="300" viewBox="0 0 600 300">'
       b'<rect x="10" y="10" width="580" height="280" fill="none" stroke="black"/><text x="40" y="60">Kitchen</text></svg>')
DXF = b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n"
IFC = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"


def post(client, *files):
    return client.post("/api/upload", files=[("files", f) for f in files])


def file_of(tmp_path, d, i=0):
    uid = d["path"].split(":")[1].split("/")[0]
    return tmp_path / uid / "data" / "upload" / d["files"][i]["stored"]


def test_tiff_becomes_png(client, tmp_path):
    r = post(client, ("scan.tif", tiff(), "image/tiff"))
    assert r.status_code == 200, r.text
    d = r.json()["drawings"][0]
    assert d["ext"] == "png" and d["path"].endswith("-scan.png")
    assert Image.open(file_of(tmp_path, d)).format == "PNG"
    assert client.get("/api/preview", params={"path": d["path"]}).status_code == 200


@pytest.mark.parametrize("mode", ["1", "L", "RGB", "RGBA", "CMYK", "P"])
def test_tiff_modes_decode(client, mode):
    r = post(client, (f"m_{mode}.tiff", tiff(mode=mode), "image/tiff"))
    assert r.status_code == 200, r.text


def test_tiff_16_bit(client):
    assert post(client, ("m16.tif", tiff16(), "image/tiff")).status_code == 200


def test_multiframe_tiff_is_one_drawing_per_frame(client):
    r = post(client, ("pages.tif", tiff(frames=3), "image/tiff"))
    assert [d["name"] for d in r.json()["drawings"]] == ["pages.tif, frame 1", "pages.tif, frame 2", "pages.tif, frame 3"]


def test_three_unrelated_files_are_three_drawings(client):
    r = post(client, ("a.pdf", pdf(), "application/pdf"), ("b.png", png(), "image/png"), ("c.dxf", DXF, "application/dxf"))
    body = r.json()
    assert [d["name"] for d in body["drawings"]] == ["a.pdf", "b.png", "c.dxf"]
    assert len({d["path"] for d in body["drawings"]}) == 3
    assert body["path"] == body["drawings"][0]["path"]            # the first one is the one to open


def test_same_stem_stays_twins(client):
    r = post(client, ("Plan.PDF", pdf(), "application/pdf"), ("plan.dxf", DXF, "application/dxf"), ("other.ifc", IFC, "x"))
    ds = r.json()["drawings"]
    assert len(ds) == 2
    assert ds[0]["ext"] == "pdf" and {f["ext"] for f in ds[0]["files"]} == {"pdf", "dxf"}
    assert client.get("/api/drawing", params={"path": ds[0]["path"]}).json()["twins"] == ["dxf"]
    a, b = (f["stored"].rsplit(".", 1)[0] for f in ds[0]["files"])
    assert a.lower() == b.lower()                                   # shared stem: the caches treat them as one drawing


def test_same_stem_two_images_are_separate(client):
    r = post(client, ("x.png", png(), "image/png"), ("x.jpg", png(fmt="JPEG"), "image/jpeg"))
    assert len(r.json()["drawings"]) == 2


def test_multipage_pdf_one_drawing_per_page(client, tmp_path):
    r = post(client, ("book.pdf", pdf(3), "application/pdf"))
    body = r.json()
    assert [d["name"] for d in body["drawings"]] == ["book.pdf, page 1", "book.pdf, page 2", "book.pdf, page 3"]
    assert any("3 pages" in n for n in body["notes"])
    for i, d in enumerate(body["drawings"]):
        with pymupdf.open(file_of(tmp_path, d)) as doc:
            assert doc.page_count == 1
            assert f"Sheet {i + 1}" in doc[0].get_text()            # the routes read page 1 of the file they get
    det = client.get("/api/drawing", params={"path": body["drawings"][1]["path"]}).json()
    assert det["name"] == "book.pdf, page 2" and det["twins"] == []
    assert client.get("/api/preview", params={"path": body["drawings"][2]["path"]}).status_code == 200


def test_multipage_pdf_with_dxf_twin_joins_page_one(client):
    r = post(client, ("p.pdf", pdf(2), "application/pdf"), ("p.dxf", DXF, "application/dxf"))
    ds = r.json()["drawings"]
    assert [d["name"] for d in ds] == ["p.pdf, page 1", "p.pdf, page 2"]
    assert len(ds[0]["files"]) == 2 and len(ds[1]["files"]) == 1


def test_page_cap(client, monkeypatch):
    monkeypatch.setattr(uploads, "MAX_PAGES", 2)
    body = post(client, ("big.pdf", pdf(4), "application/pdf")).json()
    assert len(body["drawings"]) == 2 and any("first 2 pages" in n for n in body["notes"])


def test_svg_becomes_png_at_sensible_size(client, tmp_path):
    r = post(client, ("plan.svg", SVG, "image/svg+xml"))
    assert r.status_code == 200, r.text
    d = r.json()["drawings"][0]
    assert d["ext"] == "png" and "scan" in d["note"]
    w, h = Image.open(file_of(tmp_path, d)).size
    assert abs(max(w, h) - uploads.SVG_LONG_SIDE) <= 2 and w == 2 * h


def test_dataset_svg_if_present(client):
    hits = glob.glob(r"C:\Users\Markus\Coding\junction-2026\data\stoa-full\junction-dataset\data\ifc_models\*.svg")
    if not hits:
        pytest.skip("dataset not here")
    with open(hits[0], "rb") as f:
        r = post(client, ("real.svg", f.read(), "image/svg+xml"))
    assert r.status_code == 200, r.text


def test_dwg_refused_with_clear_message(client, monkeypatch):
    monkeypatch.setattr(uploads.shutil, "which", lambda _: None)
    r = post(client, ("x.dwg", b"AC1027" + b"\0" * 400, "application/acad"))
    assert r.status_code == 400
    assert r.json()["detail"] == "DWG is not supported yet. Please export as PDF or DXF from your CAD program."


def test_dwg_converted_when_converter_exists(client, monkeypatch):
    def fake_run(cmd, **kw):
        with open(cmd[cmd.index("-o") + 1], "wb") as f:
            f.write(DXF + b"0\n" * 200)
    monkeypatch.setattr(uploads.shutil, "which", lambda _: "dwg2dxf")
    monkeypatch.setattr(uploads.subprocess, "run", fake_run)
    r = post(client, ("x.dwg", b"AC1027" + b"\0" * 400, "application/acad"))
    assert r.status_code == 200, r.text
    assert r.json()["drawings"][0]["ext"] == "dxf"


def test_unsupported_type_lists_supported(client):
    r = post(client, ("notes.docx", b"PK", "x"))
    assert r.status_code == 400
    d = r.json()["detail"]
    assert "notes.docx" in d and ".docx" in d and "PDF, DXF, IFC, PNG, JPEG, TIFF or SVG" in d


def test_one_bad_file_does_not_sink_the_others(client):
    r = post(client, ("a.png", png(), "image/png"), ("bad.txt", b"hello", "text/plain"))
    assert r.status_code == 200
    body = r.json()
    assert len(body["drawings"]) == 1 and len(body["errors"]) == 1 and "bad.txt" in body["errors"][0]


@pytest.mark.parametrize("name,data,word", [
    ("broken.pdf", b"%PDF-1.4 nonsense", "PDF"),
    ("broken.png", b"not an image", "image"),
    ("broken.tif", b"II*\x00junk", "image"),
    ("empty.pdf", b"", "empty"),
    ("noext", b"abc", "extension"),
    ("fake.dxf", b"hello world", "DXF"),
    ("fake.ifc", b"hello world", "IFC"),
    ("broken.svg", b"<html>", "SVG"),
])
def test_bad_content_gets_a_message(client, name, data, word):
    r = post(client, (name, data, "application/octet-stream"))
    assert r.status_code == 400
    assert word in r.json()["detail"] and name in r.json()["detail"]


def test_password_protected_pdf(client):
    doc = pymupdf.open()
    doc.new_page()
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    r = post(client, ("locked.pdf", data, "application/pdf"))
    assert r.status_code == 400 and "password" in r.json()["detail"]


def test_image_with_wrong_extension_is_converted(client):
    r = post(client, ("scan.png", tiff(), "image/png"))           # a TIFF named .png
    assert r.status_code == 200 and r.json()["drawings"][0]["ext"] == "png"


def test_png_stored_unchanged(client, tmp_path):
    data = png()
    d = post(client, ("keep.png", data, "image/png")).json()["drawings"][0]
    assert file_of(tmp_path, d).read_bytes() == data


def test_oversize_file_rejected(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_UPLOAD", 1000)
    r = post(client, ("big.png", png((400, 400)), "image/png"))
    assert r.status_code == 413 and "larger than" in r.json()["detail"]


def test_reference_stays_resolvable(client):
    d = post(client, ("a.pdf", pdf(), "application/pdf")).json()["drawings"][0]
    assert client.get("/api/drawing", params={"path": d["path"]}).json()["name"] == "a.pdf"


def test_very_large_png_is_accepted(client):
    """A 14000 x 14000 px scan is over PIL's 89 MP bomb limit but a normal large plan; it is stored as is."""
    data = png((14000, 14000), mode="1")
    assert len(data) < 5_000_000
    r = post(client, ("huge.png", data, "image/png"))
    assert r.status_code == 200, r.text
    assert r.json()["drawings"][0]["ext"] == "png"
