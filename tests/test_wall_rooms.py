"""Rooms from walls on generic drawings, and plain reasons in the app (pytest, no data files needed).

A 3 x 2 grid of rooms is written as DXF (walls with door gaps and swing arcs, names as text on ANNO) and as a PDF
(single-line walls, printed names). The count, the area of a named room and the proven/estimate line are checked,
and so is the wording of reasons that reach the app.
"""
import math
import sys
import tempfile
from pathlib import Path

import ezdxf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bpqa.answer import public_reason  # noqa: E402
from bpqa.data import Question  # noqa: E402
from bpqa.query import parse  # noqa: E402
from bpqa.routes import dxf_generic, pdf_vector  # noqa: E402

NAMES = ["LAB 01", "LAB 02", "CAFE", "MEETING 01", "STUDIO", "OPEN OFFICE"]
W, H = 4000, 3000          # mm, room size; walls on the room edges


class Ctx:
    def __init__(self, path, question):
        self.file = Path(path)
        self.q = Question(0, "x/" + Path(path).name, question)
        self.evidence = False
        self.out_dir = Path(tempfile.gettempdir())

    def sibling(self, ext):
        p = self.file.with_suffix("." + ext)
        return p if p.exists() else None


def grid_dxf(path, break_wall=False, doors=True, labels=NAMES):
    doc = ezdxf.new(setup=True)
    doc.header["$INSUNITS"] = 4
    for n in ("A-WALL", "A-DOOR", "ANNO", "DIMS"):
        doc.layers.add(n)
    msp = doc.modelspace()
    # outer box and inner walls, each as plain lines on A-WALL; every room gets a 900 mm door gap in its lower wall
    def wall(p, q):
        msp.add_line(p, q, dxfattribs={"layer": "A-WALL"})
    for r in range(2):
        for c in range(3):
            x0, y0 = c * W, r * H
            gap = (x0 + 1000, x0 + 1900)
            if r == 0:
                wall((x0, y0), (x0 + W, y0))
            else:
                wall((x0, y0), (gap[0], y0))
                wall((gap[1], y0), (x0 + W, y0))
                if doors:
                    msp.add_arc((gap[0], y0), 900, 270, 0, dxfattribs={"layer": "A-DOOR"})
            wall((x0, y0), (x0, y0 + H))
            if r == 1:
                wall((x0, y0 + H), (x0 + W, y0 + H))
    wall((3 * W, 0), (3 * W, 2 * H))
    if break_wall:     # a wall removed over most of its length: two rooms merge
        msp.delete_entity([e for e in msp if e.dxftype() == "LINE" and e.dxf.start.x == W and e.dxf.end.x == W][0])
    for i, name in enumerate(labels):
        r, c = divmod(i, 3)
        msp.add_text(name, dxfattribs={"layer": "ANNO", "height": 150, "insert": (c * W + 1500, r * H + 1500)})
    msp.add_text("1:100", dxfattribs={"layer": "DIMS", "height": 150, "insert": (500, -800)})
    doc.saveas(str(path))


def query_of(question, **fields):
    """The rule parser's reading, with the fields the router's own reading fills in (a room label, the object)."""
    q = parse(question)
    for k, v in fields.items():
        setattr(q, k, v)
    return q


def ask(path, question, **fields):
    dxf_generic.load.cache_clear()
    return dxf_generic.answer(Ctx(path, question), query_of(question, **fields))


@pytest.fixture()
def dxf(tmp_path):
    p = tmp_path / "grid.dxf"
    grid_dxf(p)
    return p


def test_count_rooms_is_proven_when_regions_close(dxf):
    a = ask(dxf, "How many rooms?", measure="count", obj="room")
    assert a.answered and a.value == 6
    assert a.tier == "proven"
    assert {it["label"] for it in a.items} == set(NAMES)
    assert all(abs(it["area_m2"] - 12.0) < 0.1 for it in a.items)


def test_area_of_a_named_room_in_the_drawings_units(dxf):
    a = ask(dxf, "area of the cafe", measure="area", obj="room", label="cafe")
    assert a.answered and abs(a.value - 12.0) < 0.1 and a.unit == "m2"
    assert a.tier == "proven"
    assert "$INSUNITS=4" in a.explanation


def test_label_named_only_in_the_question(dxf):
    a = ask(dxf, "how big is MEETING 01", measure="area", obj="room", label=None)
    assert a.answered and abs(a.value - 12.0) < 0.1


def test_count_by_word_in_the_label(dxf):
    a = ask(dxf, "how many labs are there", measure="count", obj="lab", label=None)
    assert a.answered and a.value == 2


def test_open_walls_are_an_estimate_not_a_proof(tmp_path):
    p = tmp_path / "open.dxf"
    grid_dxf(p, break_wall=True)
    a = ask(p, "How many rooms?", measure="count", obj="room")
    assert a.tier != "proven"
    assert a.answered is False or "more than one label" in a.tier_reason


def test_without_door_swings_gaps_are_bridged_and_reported(tmp_path):
    p = tmp_path / "nodoors.dxf"
    grid_dxf(p, doors=False)
    a = ask(p, "How many rooms?", measure="count", obj="room")
    assert a.answered and a.value == 6
    assert "bridged" in a.explanation


def test_no_walls_no_rooms(tmp_path):
    p = tmp_path / "nowalls.dxf"
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    doc.modelspace().add_text("CAFE", dxfattribs={"height": 100, "insert": (0, 0)})
    doc.saveas(str(p))
    a = ask(p, "How many rooms?", measure="count", obj="room")
    assert not a.answered


# ---------------------------------------------------------------- the PDF twin
def grid_pdf(path):
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=700, height=420)
    k = 0.05                                    # pt per mm: rooms 200 x 150 pt
    ox, oy = 50, 60
    sh = page.new_shape()

    def seg(p, q):
        sh.draw_line((ox + p[0] * k, oy + p[1] * k), (ox + q[0] * k, oy + q[1] * k))
    for r in range(2):
        for c in range(3):
            x0, y0 = c * W, r * H
            if r == 0:
                seg((x0, y0), (x0 + W, y0))
            else:
                seg((x0, y0), (x0 + 1000, y0))
                seg((x0 + 1400, y0), (x0 + W, y0))
            seg((x0, y0), (x0, y0 + H))
            if r == 1:
                seg((x0, y0 + H), (x0 + W, y0 + H))
    seg((3 * W, 0), (3 * W, 2 * H))
    for i in range(20):                         # dimension ticks along the bottom: short pieces, not walls
        seg((i * 600, 2 * H + 900), (i * 600, 2 * H + 1000))
    sh.finish(color=(0, 0, 0), width=1.5)
    sh.commit()
    for i, name in enumerate(NAMES):
        r, c = divmod(i, 3)
        page.insert_text((ox + (c * W + 1200) * k, oy + (r * H + 900 + 300 * c) * k), name, fontsize=11)
    page.insert_text((60, 405), "SCALE 1:100", fontsize=8)
    doc.save(str(path))


def ask_pdf(path, question):
    pdf_vector.load.cache_clear()
    return pdf_vector.answer(Ctx(path, question), query_of(question, measure="count", obj="room"))


def test_pdf_counts_rooms_from_walls(tmp_path):
    p = tmp_path / "grid.pdf"
    grid_pdf(p)
    a = ask_pdf(p, "How many rooms?")
    assert a.answered and a.value == 6
    assert a.route == "pdf-generic"


def test_pdf_without_text_does_not_answer(tmp_path):
    import pymupdf
    p = tmp_path / "notext.pdf"
    grid_pdf(p)
    doc = pymupdf.open(str(p))
    for page in doc:
        for b in page.get_text("blocks"):
            page.add_redact_annot(pymupdf.Rect(b[:4]))
        page.apply_redactions()
    doc.save(str(tmp_path / "notext2.pdf"))
    a = ask_pdf(tmp_path / "notext2.pdf", "How many rooms?")
    assert not a.answered and "text" in a.abstain


# ---------------------------------------------------------------- what the app shows
@pytest.mark.parametrize("raw,gone", [
    ("nemotron-estimate: image estimate switched off (NEMO_IMAGE_ESTIMATE=0)", "NEMO"),
    ("raster-detector: error: AssertionError: Invalid device id", "AssertionError"),
    ("dxf-generic: error: KeyError: 'layer'; nemotron-estimate: error: RuntimeError: CUDA out of memory", "Error"),
    ("raster-cv-fixtures: fixture model not trained (tools/train_mil.py final)", "train_mil"),
])
def test_internal_text_is_not_shown(raw, gone):
    out = public_reason(raw)
    assert out and gone not in out and "NEMO_" not in out


def test_drawing_facts_survive():
    raw = ("dxf-generic: no wall layer in this DXF to build rooms from (layers: ROOM_TEXT, A-DOOR); "
           "nemotron-estimate: image estimate switched off (NEMO_IMAGE_ESTIMATE=0)")
    out = public_reason(raw)
    assert "no wall layer" in out and "ROOM_TEXT" in out and "NEMO" not in out


def test_empty_reasons_stay_empty_and_nothing_left_is_a_sentence():
    assert public_reason(None) is None
    assert public_reason("nemotron-estimate: image estimate switched off (NEMO_IMAGE_ESTIMATE=0)").startswith("No route")
