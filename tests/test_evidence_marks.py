"""Evidence marks sidecar: numbered boxes and room polygons land in evidence-body pixels."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from bpqa.evidence import AMBER, BLUE, GREY, ImageCanvas, VectorCanvas, finish, marks_path  # noqa: E402


def test_vector_canvas_world_to_pixels():
    cv = VectorCanvas((0, 0, 10, 5), width=1000, margin=0)
    cv.box((1, 1, 2, 2), n=1, pad=0)
    cv.poly([(5, 0), (10, 0), (10, 5), (5, 5)], fill=BLUE + (70,), outline=BLUE)
    cv.poly([(0, 0), (1, 0), (1, 1)], fill=(235, 235, 235, 255))        # opaque context: not a mark
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "a.png"
        finish(cv.img, out, "q", "Answer: 1")
        m = json.loads(marks_path(out).read_text())
    boxes = [x for x in m["marks"] if x["kind"] == "box"]
    polys = [x for x in m["marks"] if x["kind"] == "poly"]
    assert len(boxes) == 1 and len(polys) == 1
    assert boxes[0]["rect"] == [100.0, 300.0, 200.0, 400.0], boxes[0]["rect"]     # y flipped: world y=2 -> px 300
    assert polys[0]["rect"] == [500.0, 0.0, 1000.0, 500.0]
    assert m["frame"]["kind"] == "world" and m["header"] > 0
    print("ok test_vector_canvas_world_to_pixels")


def test_image_canvas_resized_and_tones():
    img = Image.new("RGB", (3200, 1600), "white")
    cv = ImageCanvas(img)
    cv.box((100, 100, 300, 200), n=1, color=BLUE)
    cv.box((400, 100, 500, 200), n=2, color=AMBER)
    cv.box((600, 100, 700, 200), color=GREY)                          # unnumbered context: not a mark
    cv.d.polygon([(0, 0), (800, 0), (800, 800)], fill=BLUE + (80,), outline=BLUE)   # drawn straight on cv.d
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "b.png"
        finish(cv.img, out, "q", "Answer: 2")
        m = json.loads(marks_path(out).read_text())
    assert m["w"] == 1600 and m["frame"]["scale"] == 0.5
    boxes = [x for x in m["marks"] if x["kind"] == "box"]
    assert [b["tone"] for b in boxes] == ["blue", "amber"]
    assert boxes[0]["rect"] == [50.0, 50.0, 150.0, 100.0]
    assert any(x["kind"] == "poly" for x in m["marks"])
    print("ok test_image_canvas_resized_and_tones")


if __name__ == "__main__":
    test_vector_canvas_world_to_pixels()
    test_image_canvas_resized_and_tones()
