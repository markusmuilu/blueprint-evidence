"""Witness agreement levels and page-frame disagreement circles."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bpqa.witnesses import agreement, same, spatial_disagreements  # noqa: E402


def row(fmt, v, unit="count", marks=None):
    return {"format": fmt, "value": v, "unit": unit, "marks": marks}


def test_levels():
    rs = [row("DXF", 36), row("PDF", 36), row("PNG", 30)]
    a = agreement(rs)
    assert a["level"] == "majority" and a["majority_value"] == 36 and a["n_agree"] == 2
    assert [r["agrees"] for r in rs] == [True, True, False]
    assert agreement([row("DXF", 4), row("PDF", 4)])["level"] == "all"
    assert agreement([row("DXF", 1), row("PNG", 2)])["level"] == "split"
    assert agreement([row("DXF", 1), row("PNG", None)])["level"] == "single"
    assert same(80.82, 80.0, "m2") and not same(80.82, 78.0, "m2") and not same(3, 4, "count")
    print("ok test_levels")


def test_spatial():
    page = lambda pts: {"w": 1000, "h": 500, "frame": {"kind": "pixel"},
                        "marks": [{"kind": "box", "n": i + 1, "rect": [x - 5, y - 5, x + 5, y + 5]} for i, (x, y) in enumerate(pts)]}
    a = row("PDF", 3, marks=page([(100, 100), (500, 250), (900, 400)]))
    b = row("PNG", 2, marks=page([(102, 101), (500, 252)]))
    c = row("DXF", 3, marks={"w": 800, "h": 900, "frame": {"kind": "world"}, "marks": []})
    n = spatial_disagreements([a, b, c])
    assert n == 1
    assert a["disputed"][0]["kind"] == "only" and b["disputed"][0]["kind"] == "missed" and c["disputed"] == []
    print("ok test_spatial")


if __name__ == "__main__":
    test_levels()
    test_spatial()
