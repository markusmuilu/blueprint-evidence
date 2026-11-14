"""Room regions bounded by walls (raster_area stroke walls) on a synthetic plan at about 50 px per m.

A room with thick walls holds a dashed double outline in its top-right corner (a bed head and wardrobe drawn
against the walls), a door in its left wall with a thin swing arc, and a 2 m window in its top wall drawn as
thin lines. The region must be the whole room inside the walls: the dashed outline is not a wall, the door
closes at the wall line, and the window does not leak. A plan drawn only in thin lines keeps the legacy mask.
python -m pytest tests/test_room_regions.py   (or python tests/test_room_regions.py)"""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.routes import raster_area as RA  # noqa: E402

PPM = 50
X0, Y0, X1, Y1 = 100, 100, 420, 360      # outer corners of the room's walls
T = 8                                     # wall thickness px (16 cm)


def dashed_rect(img, x0, y0, x1, y1, dash=6, gap=4):
    for x in range(x0, x1, dash + gap):
        cv2.line(img, (x, y0), (min(x + dash, x1), y0), 0, 1)
        cv2.line(img, (x, y1), (min(x + dash, x1), y1), 0, 1)
    for y in range(y0, y1, dash + gap):
        cv2.line(img, (x0, y), (x0, min(y + dash, y1)), 0, 1)
        cv2.line(img, (x1, y), (x1, min(y + dash, y1)), 0, 1)


def plan():
    g = np.full((480, 560), 255, np.uint8)
    cv2.rectangle(g, (X0, Y0), (X1, Y1), 0, -1)
    cv2.rectangle(g, (X0 + T, Y0 + T), (X1 - T, Y1 - T), 255, -1)
    # door 0.9 m in the left wall, leaf and swing arc drawn thin into the room
    d0, d1 = 200, 245
    g[d0:d1, X0:X0 + T] = 255
    cv2.line(g, (X0 + T, d0), (X0 + T + 45, d0), 0, 1)
    cv2.ellipse(g, (X0 + T, d0), (45, 45), 0, 0, 90, 0, 1)
    # window 2 m in the top wall: three thin lines along the wall
    w0, w1 = 160, 260
    g[Y0:Y0 + T, w0:w1] = 255
    for y in (Y0, Y0 + T // 2, Y0 + T - 1):
        cv2.line(g, (w0, y), (w1, y), 0, 1)
    # bed head and wardrobe: dashed double outline against the top-right corner
    dashed_rect(g, X1 - T - 110, Y0 + T, X1 - T - 1, Y0 + T + 70)
    dashed_rect(g, X1 - T - 106, Y0 + T + 3, X1 - T - 4, Y0 + T + 67)
    dashed_rect(g, X1 - T - 60, Y0 + T + 70, X1 - T - 1, Y0 + T + 200)
    # a thin furniture line (a counter front) crossing the corner of the room
    cv2.line(g, (X0 + T, Y1 - T - 40), (X0 + T + 120, Y1 - T - 40), 0, 1)
    cv2.line(g, (X0 + T + 120, Y1 - T - 40), (X0 + T + 120, Y1 - T - 1), 0, 1)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def region(img, mode):
    old = RA.WALL_MODE
    RA.WALL_MODE = mode
    try:
        r = RA.Room("MH1", None, None, None, (300, 300))
        base = RA.walls_base(img, [])
        RA.measure_regions(img, [r], [], [], door_px=PPM, base=base)
        return r, base[1]
    finally:
        RA.WALL_MODE = old


def test_wall_width_from_strokes():
    img = plan()
    dark = RA._dark_textless(img, [])
    w = RA.wall_width(dark)
    assert w is not None and abs(w - T) <= 2, w


def test_region_is_the_room_inside_the_walls():
    img = plan()
    r, kind = region(img, "stroke")
    assert kind == "stroke"
    assert r.region_ok, r.why
    inside = (X1 - X0 - 2 * T + 1) * (Y1 - Y0 - 2 * T + 1)
    assert abs(r.pixels - inside) / inside < 0.02, (r.pixels, inside)
    x, y, w, h = cv2.boundingRect(r.contour)
    assert x >= X0 + T - 1 and y >= Y0 + T - 1, (x, y)                 # no bulge out through the door or window
    assert x + w <= X1 - T + 2 and y + h <= Y1 - T + 2, (x + w, y + h)
    # the dashed corner is part of the room
    assert RA._inside(r.contour, (X1 - T - 20, Y0 + T + 20))


def test_legacy_mask_cuts_the_dashed_corner():
    """The bug this fixes: the old mask closes the dashed double outline into a wall."""
    img = plan()
    r, kind = region(img, "legacy")
    assert kind == "legacy"
    inside = (X1 - X0 - 2 * T + 1) * (Y1 - Y0 - 2 * T + 1)
    assert not r.region_ok or not RA._inside(r.contour, (X1 - T - 20, Y0 + T + 20)) or abs(r.pixels - inside) / inside > 0.02


def test_thin_line_plan_keeps_legacy():
    g = np.full((300, 300), 255, np.uint8)
    cv2.rectangle(g, (50, 50), (250, 250), 0, 1)
    cv2.rectangle(g, (53, 53), (247, 247), 0, 1)
    img = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
    assert RA.wall_width(RA._dark_textless(img, [])) is None
    assert RA.walls_base(img, [])[1] == "legacy"


def test_gap_bridge_does_not_slice_a_room():
    """A narrow room between thick walls is not filled by the wall-line bridge (its stack is as deep as the room)."""
    g = np.full((300, 300), 255, np.uint8)
    cv2.rectangle(g, (50, 50), (130, 250), 0, -1)
    cv2.rectangle(g, (58, 58), (122, 242), 255, -1)       # a 64 px (1.3 m) wide room
    walls = (g < RA.DARK).astype(np.uint8)
    out = RA.bridge_lines(walls, RA.wall_line_gaps(walls, 4), int(2.6 * PPM), int(0.55 * PPM))
    assert out[58:243, 58:123].sum() == 0


def test_door_bridge_keeps_a_narrow_room_open():
    """Legacy doorway bridge: a WC narrower than the doorway width is not filled wall to wall (QA,
    high_quality_architectural-10372: the WC was filled on one half), and its doorway still closes.
    Known limit: a doorway in a wall shorter than the doorway width is lost with the fill."""
    g = np.full((300, 300), 255, np.uint8)
    cv2.rectangle(g, (50, 50), (150, 200), 0, -1)
    cv2.rectangle(g, (56, 56), (144, 194), 255, -1)       # inside 89 x 139 px; doorway width 100 px
    g[100:140, 144:151] = 255                              # a 40 px doorway in the long right wall
    walls = (g < RA.DARK).astype(np.uint8)
    old = RA.bridge(walls, 100)
    new = RA.bridge(walls, 100, int(0.55 * PPM))
    assert old[60:190, 60:140].mean() > 0.5                # the old bridge fills most of the room
    assert new[60:190, 60:140].mean() < 0.02               # at most a one-row sliver at the doorway
    assert new[100:140, 144:151].sum(axis=1).min() >= 3     # the doorway is bridged along the wall line


def main():
    bad = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
            except AssertionError as e:
                bad += 1
                print("FAIL", name, e)
    print("room region tests:", "FAIL" if bad else "ok")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
