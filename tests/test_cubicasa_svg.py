"""Unit test of the CubiCasa5K SVG converter on a small hand-made SVG in the published structure.

    python -m pytest tests -q        (or: python tests/test_cubicasa_svg.py)
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.cubicasa_svg import convert, counts, parse_transform, to_yolo  # noqa: E402

SVG = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="800" viewBox="0 0 1000 800">
 <g id="Model" class="Model">
  <g class="Floor">
   <g class="Space Bathroom" id="s1"><polygon points="100,100 300,100 300,300 100,300 "/></g>
   <g id="Wall" class="Wall External">
    <polygon points="0,0 1000,0 1000,10 0,10 "/>
    <g id="Window" class="Window Regular">
     <polygon points="400,0 500,0 500,10 400,10 "/>
     <g class="Panel"><line x1="400" y1="5" x2="500" y2="5"/></g>
    </g>
    <g id="Door" class="Door Swing Beside">
     <polygon points="600,0 680,0 680,10 600,10 "/>
     <g class="Threshold"><line x1="600" y1="10" x2="680" y2="90"/></g>
    </g>
   </g>
   <g class="FixedFurniture Toilet" transform="matrix(1,0,0,1,150,120)">
    <g class="BoundaryPolygon"><polygon points="0,0 40,0 40,70 0,70 "/></g>
    <g class="InnerPolygon"><ellipse cx="20" cy="40" rx="15" ry="20"/></g>
   </g>
   <g class="FixedFurnitureSet" transform="matrix(0,1,-1,0,500,300)">
    <g class="FixedFurniture Sink" transform="matrix(1,0,0,1,10,20)">
     <g class="BoundaryPolygon"><rect x="0" y="0" width="50" height="40"/></g>
    </g>
    <g class="FixedFurniture DoubleSink" transform="translate(100 0)">
     <g class="BoundaryPolygon"><polygon points="0,0 80,0 80,40 0,40 "/></g>
    </g>
   </g>
   <g class="FixedFurniture Bathtub" transform="matrix(1,0,0,1,200,200)">
    <g class="BoundaryPolygon"><polygon points="0,0 170,0 170,75 0,75 "/></g>
   </g>
   <g class="FixedFurniture Shower" transform="rotate(90 50 50) translate(700 0)">
    <g class="BoundaryPolygon"><polygon points="0,0 90,0 90,90 0,90 "/></g>
   </g>
   <g class="FixedFurniture ShowerCab" transform="matrix(1,0,0,1,0,0)">
    <g class="BoundaryPolygon"><polygon points="0,0 60,0 60,60 0,60 "/></g>
   </g>
  </g>
 </g>
</svg>
"""


def close(a, b, tol=1e-6):
    return np.allclose(np.array(a, float), np.array(b, float), atol=tol)


def test_transforms():
    assert close(parse_transform("matrix(1,0,0,1,5,6)") @ [1, 1, 1], [6, 7, 1])
    assert close(parse_transform("translate(3)") @ [0, 0, 1], [3, 0, 1])
    assert close(parse_transform("rotate(90)") @ [1, 0, 1], [0, 1, 1])
    assert close(parse_transform("rotate(90 50 50)") @ [50, 50, 1], [50, 50, 1])
    # composition left to right: translate then scale -> scale applied first to the point
    assert close(parse_transform("translate(10,0) scale(2)") @ [1, 1, 1], [12, 2, 1])


def test_convert():
    boxes, size, unknown = convert(SVG)
    assert size == (1000.0, 800.0)
    assert dict(unknown) == {"ShowerCab": 1}          # not counted by Stoa
    by = {}
    for k, *b in boxes:
        by.setdefault(k, []).append(b)
    assert counts(boxes) == {"toilet": 1, "sink": 2, "shower": 1, "bathtub": 1, "door": 1, "window": 1,
                             "shower_or_bath": 2}
    assert close(by["window"][0], [400, 0, 500, 10])          # opening polygon only, not the panel line
    assert close(by["door"][0], [600, 0, 680, 10])            # not the swing line
    assert close(by["toilet"][0], [150, 120, 190, 190])       # boundary polygon only, not the inner ellipse
    assert close(by["bathtub"][0], [200, 200, 370, 275])
    # set transform (rotate 90 about origin, then translate 500,300) composed with the sink's own
    # translate (10,20): x' = 500 - y, y' = 300 + x
    sinks = sorted(by["sink"])
    assert close(sinks[0], [500 - 60, 310, 500 - 20, 360])    # rect 0..50 x 0..40 at (10,20)
    assert close(sinks[1], [500 - 40, 400, 500, 480])         # polygon 0..80 x 0..40 at (100,0)
    # shower: translate(700,0) first, then rotate 90 about (50,50): (x,y) -> (100 - y, x)
    assert close(by["shower"][0], [100 - 90, 700, 100, 790])


def test_yolo_lines_scale_to_image():
    boxes, size, _ = convert(SVG)
    lines = to_yolo(boxes, size, (500, 400))                  # image at half the SVG size
    win = [l for l in lines if l.startswith("5 ")][0].split()
    assert close([float(v) for v in win[1:]], [450 / 1000, 5 / 800, 100 / 1000, 10 / 800])


def test_refuses_entities():
    bad = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><svg>&a;</svg>'
    try:
        convert(bad)
    except ValueError:
        return
    raise AssertionError("DTD not refused")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
