"""Night QA 15 Nov: a whole-flat total summed from labelled rooms abstains when the room tracing closes a big region
with no room label in it (FOCSA listing plan: LR + DR, about 53 m2, left out of a 66.23 m2 total)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bpqa.routes import raster  # noqa: E402
from bpqa.routes import raster_area as RA  # noqa: E402


def tok(text, x0, y0, x1, y1, conf=0.99):
    return {"text": text, "conf": conf, "quad": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], "rot": 0}


def items(*pairs):
    return [{"room": r, "area_m2": a} for r, a in pairs]


def _with_regions(monkeypatch, regions):
    RA.unlabelled_regions.cache_clear()
    monkeypatch.setattr(RA, "unlabelled_regions", lambda path: {"regions": regions, "interior_m2": 0})
    return SimpleNamespace(file="x.png")


def test_region_larger_than_every_measured_room_abstains(monkeypatch):
    ctx = _with_regions(monkeypatch, [(52.9, (75, 57, 238, 469)), (4.6, (0, 0, 1, 1))])
    why = raster._unlabelled_left_out(ctx, items(("BR1", 21.19), ("BR2", 20.91), ("BR 3", 11.41), ("K", 12.72)), 66.23)
    assert why and "52.9 m2" in why and "66.23 m2" in why


def test_unlabelled_share_over_a_quarter_abstains(monkeypatch):
    ctx = _with_regions(monkeypatch, [(7.0, (0, 0, 1, 1)), (6.5, (0, 0, 1, 1)), (5.0, (0, 0, 1, 1))])
    why = raster._unlabelled_left_out(ctx, items(("MH", 12.0), ("OH", 20.0), ("K", 8.0)), 40.0)
    assert why and "%" in why


def test_small_unlabelled_closets_and_halls_keep_the_sum(monkeypatch):
    ctx = _with_regions(monkeypatch, [(5.5, (0, 0, 1, 1)), (1.8, (0, 0, 1, 1)), (1.2, (0, 0, 1, 1))])
    assert raster._unlabelled_left_out(ctx, items(("MH", 12.0), ("OH", 22.0), ("K", 9.0), ("KH", 4.0)), 47.0) is None


def test_no_regions_or_no_scale_keeps_the_sum(monkeypatch):
    ctx = _with_regions(monkeypatch, [])
    assert raster._unlabelled_left_out(ctx, items(("MH", 12.0)), 12.0) is None
    monkeypatch.setattr(RA, "unlabelled_regions", lambda path: None)
    assert raster._unlabelled_left_out(ctx, items(("MH", 12.0)), 12.0) is None


def test_unlabelled_regions_finds_the_room_without_a_label(monkeypatch, tmp_path):
    # a flat of three closed rooms, 100 px = 1 m: MH (3 x 3 m) and K (3 x 3 m) labelled, the 3 x 5 m room not
    img = np.full((700, 900, 3), 255, np.uint8)
    cv2.rectangle(img, (100, 100), (700, 600), (0, 0, 0), 8)
    cv2.line(img, (400, 100), (400, 600), (0, 0, 0), 8)
    cv2.line(img, (100, 400), (400, 400), (0, 0, 0), 8)
    f = tmp_path / "flat.png"
    cv2.imwrite(str(f), img)
    toks = [tok("MH", 230, 240, 270, 260), tok("K", 240, 490, 260, 510)]
    rooms = [RA.Room("MH", RA.quad_box(toks[0]["quad"]), None, None, (250, 250)),
             RA.Room("K", RA.quad_box(toks[1]["quad"]), None, None, (250, 500))]
    pa = RA.PlanAreas(rooms, [], RA.Scale(1e-4, [0, 1], 0.0, "test", ""), "lines")
    RA.unlabelled_regions.cache_clear()
    monkeypatch.setattr(RA, "analyse", lambda path, *a, **k: pa)
    monkeypatch.setattr(RA, "plan_tokens", lambda path, allow_run=True, min_conf=0.5: (RA.UPRIGHT, toks, []))
    u = RA.unlabelled_regions(str(f))
    RA.unlabelled_regions.cache_clear()
    assert len(u["regions"]) == 1
    m2, box = u["regions"][0]
    assert 12 < m2 < 16 and box[0] > 400
