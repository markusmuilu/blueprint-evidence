"""Generic DXF route on synthetic files with other naming schemes: python tests/test_dxf_generic.py

Each case writes a small DXF with ezdxf (AIA layer names with blocks, German layer names with
polyline swings, ISO 13567 element codes, a file without any door layer) and checks the count,
or that the route abstains instead of returning 0.
"""
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ezdxf  # noqa: E402

from bpqa.data import Question  # noqa: E402
from bpqa.query import parse  # noqa: E402
from bpqa.routes import dxf_generic, dxf_swiss  # noqa: E402
from bpqa.routes.dxf_generic import name_classes  # noqa: E402


class Ctx:
    def __init__(self, path, question):
        self.file = Path(path)
        self.q = Question(0, "x/" + Path(path).name, question)
        self.evidence = False
        self.out_dir = Path(tempfile.gettempdir())

    def sibling(self, ext):
        p = self.file.with_suffix("." + ext)
        return p if p.exists() else None


def ask(path, question):
    q = parse(question)
    dxf_generic.load.cache_clear()
    return dxf_swiss.answer(Ctx(path, question), q)


def rect(msp, x, y, w, h, layer):
    msp.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], close=True, dxfattribs={"layer": layer})


def aia_file(path):
    """mm, AIA/NCS names, doors/windows/fixtures as blocks, rooms as closed polylines on A-AREA."""
    doc = ezdxf.new(setup=True)
    doc.header["$INSUNITS"] = 4
    for name in ("A-DOOR", "A-DOOR-IDEN", "A-GLAZ", "A-WALL", "A-AREA", "A-AREA-IDEN", "P-FIXT", "A-ANNO-TTLB"):
        doc.layers.add(name)
    d = doc.blocks.new("DOOR-SGL-900")
    d.add_arc((0, 0), 900, 0, 90)
    d.add_line((0, 0), (900, 0))
    w = doc.blocks.new("WIN-1200")
    w.add_line((0, 0), (1200, 0))
    w.add_line((0, 100), (1200, 100))
    for nm in ("WC-01", "LAV-STD"):
        doc.blocks.new(nm).add_circle((0, 0), 200)
    msp = doc.modelspace()
    for x in (1000, 5000, 9000):
        msp.add_blockref("DOOR-SGL-900", (x, 0), dxfattribs={"layer": "A-DOOR"})
        msp.add_circle((x, -500), 150, dxfattribs={"layer": "A-DOOR-IDEN"})      # door tags, not doors
        msp.add_text("D1", dxfattribs={"layer": "A-DOOR-IDEN", "insert": (x, -500)})
    for x in (500, 3000, 6000, 8000):
        msp.add_blockref("WIN-1200", (x, 6000), dxfattribs={"layer": "A-GLAZ"})
    msp.add_blockref("WC-01", (2000, 2000), dxfattribs={"layer": "P-FIXT"})
    msp.add_blockref("WC-01", (7000, 2000), dxfattribs={"layer": "P-FIXT"})
    msp.add_blockref("LAV-STD", (2500, 2000), dxfattribs={"layer": "P-FIXT"})
    rooms = [((0, 0, 4000, 3000), "Kitchen"), ((4000, 0, 4000, 3000), "Bedroom 1"), ((8000, 0, 3000, 3000), "Bedroom 2"),
             ((0, 3000, 11000, 3000), "Living Room")]
    for (x, y, ww, hh), lab in rooms:
        rect(msp, x, y, ww, hh, "A-AREA")
        msp.add_text(lab, dxfattribs={"layer": "A-AREA-IDEN", "insert": (x + ww / 2, y + hh / 2)})
    msp.add_text("Kitchen", dxfattribs={"layer": "A-ANNO-TTLB", "insert": (20000, 0)})   # title block, ignored
    doc.saveas(path)


def german_file(path):
    """$INSUNITS left at the template default (m) while the drawing is in cm: the door radius
    overrules it. German layer names, swings as bulge polylines,
    windows as closed frames with glazing lines, one double door."""
    doc = ezdxf.new()
    for name in ("Türen", "Fenster", "Wände", "Raumstempel"):
        doc.layers.add(name)
    msp = doc.modelspace()
    b = math.tan(math.radians(90) / 4)
    for x in (0, 400):
        msp.add_lwpolyline([(x + 90, 0, b), (x, 90, 0)], format="xyb", dxfattribs={"layer": "Türen"})
        msp.add_line((x, 0), (x, 90), dxfattribs={"layer": "Türen"})
    # double door: hinges at 1000 and 1160 (r = 80), free ends meet at 1080
    msp.add_arc((1000, 0), 80, 0, 90, dxfattribs={"layer": "Türen"})
    msp.add_arc((1160, 0), 80, 90, 180, dxfattribs={"layer": "Türen"})
    for x in (0, 300, 600):
        rect(msp, x, 500, 120, 20, "Fenster")
        msp.add_line((x, 507), (x + 120, 507), dxfattribs={"layer": "Fenster"})
        msp.add_line((x, 513), (x + 120, 513), dxfattribs={"layer": "Fenster"})
    rect(msp, -50, -50, 1400, 600, "Wände")
    doc.saveas(path)


def iso_file(path):
    """ISO 13567 style names with CI/SfB element codes: 32 internal openings (doors), 31 windows."""
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6
    for name in ("A--32---E", "A--31---E", "A--22---E"):
        doc.layers.add(name)
    msp = doc.modelspace()
    for x in (0, 3, 6, 9, 12):
        msp.add_arc((x, 0), 0.85, 0, 90, dxfattribs={"layer": "A--32---E"})
    msp.add_line((0, 5), (1.2, 5), dxfattribs={"layer": "A--31---E"})
    msp.add_line((0, 5.15), (1.2, 5.15), dxfattribs={"layer": "A--31---E"})
    doc.saveas(path)


def bare_file(path):
    """Only walls and text: no door or window class anywhere."""
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    doc.layers.add("WALLS")
    msp = doc.modelspace()
    rect(msp, 0, 0, 5000, 4000, "WALLS")
    msp.add_text("OFFICE", dxfattribs={"layer": "0", "insert": (2500, 2000)})
    doc.saveas(path)


CASES = [
    ("aia", "How many doors are drawn on this floor plan?", 3),
    ("aia", "How many windows are drawn on this floor plan?", 4),
    ("aia", "How many toilet bowls (WC seats) are drawn on this floor plan?", 2),
    ("aia", "How many bedrooms are on this floor plan?", 2),
    ("aia", "How many rooms labelled 'Kitchen' are there on this floor?", 1),
    ("aia", "What is the total net area in m² of all rooms labelled 'Kitchen' on this floor?", 12.0),
    ("aia", "How many square meters of flooring do I need for the bedrooms?", 21.0),
    ("aia", "How many showers and bathtubs are drawn on this floor plan in total?", None),
    ("de", "Wie viele Türen gibt es auf diesem Grundriss?", 3),
    ("de", "How many windows are drawn on this floor plan?", 3),
    ("iso", "How many doors are drawn on this floor plan?", 5),
    ("iso", "How many windows are drawn on this floor plan?", 1),
    ("bare", "How many doors are drawn on this floor plan?", None),
    ("bare", "How many windows are drawn on this floor plan?", None),
    ("bare", "How many rooms labelled 'MEETING' are there on this floor?", None),
]
NAMES = {"A-DOOR": {"door"}, "A-DOOR-IDEN": {"annotation"}, "A-GLAZ": {"window"}, "A-WALL": {"wall"},
         "Fenster": {"window"}, "Türen": {"door"}, "P-FIXT": {"sanitary"}, "A-AREA-IDEN": {"room", "annotation"},
         "A--32---E": {"door"}, "DIMS": {"annotation"}, "MOBILIER": {"furniture"}}


def main():
    bad = 0
    for name, want in NAMES.items():
        if name_classes(name) != want:
            bad += 1
            print("FAIL name", name, name_classes(name), want)
    with tempfile.TemporaryDirectory() as td:
        files = {k: str(Path(td) / f"{k}.dxf") for k in ("aia", "de", "iso", "bare")}
        aia_file(files["aia"]); german_file(files["de"]); iso_file(files["iso"]); bare_file(files["bare"])
        for k, f in files.items():
            if dxf_swiss.swiss_scheme(f):
                bad += 1
                print("FAIL", k, "taken for the Swiss scheme")
        for k, question, want in CASES:
            a = ask(files[k], question)
            got = a.value if a.answered else None
            ok = (got is None) if want is None else (got is not None and abs(float(got) - want) < 1e-6 * max(1, want))
            if a.answered and float(a.value) == 0:
                ok = False      # the generic route must never return a 0
            if not ok:
                bad += 1
            print("ok  " if ok else "FAIL", k, question[:60], "->", got, "|", (a.abstain or a.explanation)[:110])
    print("ok" if not bad else f"{bad} failures")
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
