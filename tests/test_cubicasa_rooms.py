"""Room converter on a hand-made CubiCasa-style SVG: class, name label and fixtures decide the type.

    python tests/test_cubicasa_rooms.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.cubicasa_rooms import counts, typed_rooms  # noqa: E402


def space(cls, x, y, w, h, name="UNDEFINED", inner=""):
    pts = f"{x},{y} {x + w},{y} {x + w},{y + h} {x},{y + h}"
    return (f'<g class="{cls}"><polygon points="{pts}"/>'
            f'<g class="SpaceDimensionsLabel"><g class="TextLabel NameLabel"><text>{name}</text></g></g>{inner}</g>')


def fixture(kind, x, y, extra=""):
    return (f'<g class="FixedFurniture {extra}{kind}" transform="matrix(1,0,0,1,{x},{y})">'
            f'<g class="BoundaryPolygon"><polygon points="0,0 10,0 10,10 0,10"/></g></g>')


SVG = "<svg xmlns='http://www.w3.org/2000/svg' width='1000' height='1000'>" + "".join([
    space("Space Bath", 0, 0, 100, 100, "WC"),
    space("Space Bath", 100, 0, 100, 100, "KH"),
    space("Space Bath Shower", 200, 0, 100, 100, "PH"),
    space("Space Undefined", 300, 0, 100, 100),
    space("Space Undefined", 400, 0, 100, 100),
    space("Space Kitchen Kitchenette", 0, 100, 100, 100, "KK"),
    space("Space Bedroom", 100, 100, 100, 100, "MH"),
    space("Space Outdoor Balcony Glazed", 200, 100, 100, 100, "LASIT. PARVEKE"),
    space("Space Outdoor", 300, 100, 100, 100, "ULKOTILA"),
]) + fixture("SaunaStove", 340, 40, "ElectricalAppliance ") + fixture("Toilet", 440, 40) + "</svg>"


def main():
    c = counts(typed_rooms(SVG))
    want = {"kitchen": 1, "bathroom": 2, "wc_room": 1, "sauna": 1, "bedroom": 1, "balcony": 1}
    assert c == want, c
    print("ok", c)


if __name__ == "__main__":
    main()
