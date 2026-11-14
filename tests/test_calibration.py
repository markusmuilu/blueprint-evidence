"""Calibrated proven tier on scans: python -m pytest tests/test_calibration.py

Unit tests use a small made-up table. The last test asks the ten QA cases (proven but wrong before
calibration) end to end; it is skipped when the full dataset or the detector is not there.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa import calibration, checks, router  # noqa: E402
from bpqa.answer import Answer  # noqa: E402
from bpqa.data import Question  # noqa: E402
from bpqa.query import parse  # noqa: E402

TABLE = {
    "raster-detector|window": {"n": 300, "correct": 210, "precision": 0.7, "proven": False,
                               "counts": {"1": {"n": 120, "correct": 119, "precision": 0.992, "proven": True}}},
    "raster-cv|storey": {"n": 400, "correct": 396, "precision": 0.99, "proven": True, "counts": {}},
    "raster-detector|toilet": {"n": 600, "correct": 540, "precision": 0.9, "proven": False, "counts": {}},
    "raster-detector|toilet|clear": {"n": 480, "correct": 450, "precision": 0.94, "proven": False,
                                     "counts": {"1": {"n": 310, "correct": 305, "precision": 0.984, "proven": True}}},
}


@pytest.fixture(autouse=True)
def table(monkeypatch):
    monkeypatch.setattr(calibration, "cells", lambda: TABLE)
    monkeypatch.setattr(calibration, "ENABLED", True)


def test_unreliable_cell_is_an_estimate_with_its_measured_precision():
    why = calibration.reason("raster-detector", "window", 5)
    assert why and "70%" in why and "window" in why and "check the numbered boxes" in why


def test_reliable_cell_and_reliable_count_stay_proven():
    assert calibration.reason("raster-cv", "storey", 2) is None
    assert calibration.reason("raster-detector", "window", 1) is None      # this count measured >= 98%


def test_condition_cell_supports_only_its_measured_count():
    assert calibration.reason("raster-detector", "toilet", 1, cell="clear") is None
    assert "90%" in calibration.reason("raster-detector", "toilet", 2, cell="clear")
    assert "90%" in calibration.reason("raster-detector", "toilet", 1)          # not clear: the object's precision


def test_clear_needs_agreeing_views_and_no_box_near_the_threshold():
    from bpqa.routes import detector
    c = {"conf": 0.5}
    box = lambda s: {"cls": "toilet", "conf": s, "xyxy": [0, 0, 1, 1]}
    assert detector.clear([[box(0.9)]] * 8, [box(0.9), box(0.2)], "toilet", c, 0)
    assert not detector.clear([[box(0.9)]] * 8, [box(0.9), box(0.35)], "toilet", c, 0)   # 0.15 below t
    assert not detector.clear([[box(0.9)]] * 8, [box(0.65)], "toilet", c, 0)             # 0.15 above t
    assert not detector.clear([[box(0.9)]] * 8, [box(0.9)], "toilet", c, 1)              # views disagree
    assert detector.clear([[box(0.9)]] * 8, [box(0.9), {"cls": "sink", "conf": 0.5, "xyxy": [0, 0, 1, 1]}],
                          "toilet", c, 0)                                                 # other classes ignored


def test_unmeasured_scan_route_is_an_estimate_and_other_routes_are_untouched():
    assert "no dev measurement" in calibration.reason("raster-ocr-labels", "sauna", 1)
    assert calibration.reason("ifc", "door", 4) is None
    assert calibration.reason("pdf-vector", "window", 9) is None


def test_apply_only_lowers():
    a = Answer(value=5, route="raster-detector", tier="estimate", tier_reason="2 box(es) near the threshold")
    calibration.apply(a, "window")
    assert a.tier == "estimate" and a.tier_reason == "2 box(es) near the threshold"
    b = Answer(value=5, route="raster-detector", tier="proven")
    calibration.apply(b, "window")
    assert b.tier == "estimate" and "70%" in b.tier_reason


def test_disabled_keeps_the_route_tier(monkeypatch):
    monkeypatch.setattr(calibration, "ENABLED", False)
    assert calibration.reason("raster-detector", "window", 5) is None


def test_passing_check_never_raises_an_estimate(monkeypatch):
    """doors >= rooms-1 passing (or any check passing) leaves an estimate an estimate."""
    passing = [checks.Check("doors>=rooms-1", "pass", "6 room labels read, so at least 5 doors; answer 10", "lower", 5)]
    monkeypatch.setattr(checks, "run", lambda file, obj, value: passing)
    a = Answer(value=10, route="raster-detector", tier="estimate", tier_reason="calibrated")

    class Ctx:
        file = Path("x.png")
    out = checks.apply(Ctx(), parse("How many doors are shown on this floor plan?"), a)
    assert out.tier == "estimate" and out.checks[0]["status"] == "pass"


def test_router_never_lets_a_check_raise_a_tier(monkeypatch, tmp_path):
    """Even a (broken) checking layer that sets proven cannot raise the route's estimate."""
    (tmp_path / "data" / "cubicasa5k").mkdir(parents=True)
    q = Question(0, "data/cubicasa5k/x.png", "How many doors are shown on this floor plan?", 9, "count", "count")

    def route(ctx, query):
        return Answer(value=10, unit="count", route="raster-detector", tier="estimate", tier_reason="calibrated")

    def bad_checks(ctx, query, a):
        a.tier = "proven"
        a.checks = [{"name": "doors>=rooms-1", "status": "pass"}]
        return a
    monkeypatch.setattr(router, "candidate_routes", lambda ctx, query=None: [route])
    monkeypatch.setattr(router.checking, "apply", bad_checks)
    ctx = router.Context(root=tmp_path, q=q, out_dir=tmp_path / "ev", evidence=False, nemotron=False)
    _, a = router.solve(ctx)
    assert a.tier == "estimate"


def test_router_calibrates_a_proven_scan_answer_from_any_raster_route(monkeypatch, tmp_path):
    (tmp_path / "data" / "cubicasa5k").mkdir(parents=True)
    q = Question(0, "data/cubicasa5k/x.png", "How many windows are shown on this floor plan?", 8, "count", "count")
    monkeypatch.setattr(router, "candidate_routes",
                        lambda ctx, query=None: [lambda c, qq: Answer(value=5, unit="count", route="raster-detector", tier=None)])
    ctx = router.Context(root=tmp_path, q=q, out_dir=tmp_path / "ev", evidence=False, checks=False, nemotron=False)
    _, a = router.solve(ctx)
    assert a.tier == "estimate" and "70%" in a.tier_reason


# ---------------------------------------------------------------- the QA cases, end to end

FULL = Path(__file__).resolve().parents[2] / "data" / "stoa-full" / "junction-dataset"
QA = [("colorful-11196.png", "door"), ("colorful-11196.png", "window"),
      ("high_quality_architectural-10026.png", "toilet"), ("high_quality_architectural-10026.png", "shower_or_bath"),
      ("high_quality_architectural-10026.png", "window"), ("high_quality_architectural-10026.png", "sink")]


@pytest.mark.skipif(not (FULL / "questions.jsonl").exists() or not router.raster.detector.config("door"),
                    reason="full dataset or tuned detector not available")
def test_qa_cases_are_estimates(monkeypatch):
    monkeypatch.undo()                                  # the real table
    from bpqa.data import load_questions
    from bpqa.routes import detector
    want = {(f, o) for f, o in QA}
    qs = [q for q in load_questions(FULL) if (q.path.rsplit("/", 1)[-1], parse(q.question).obj) in want]
    assert len(qs) >= 10
    for q in qs:
        if not all(m.exists() for m in detector.model_files(detector.config(parse(q.question).obj))):
            pytest.skip("detector weights missing")
        _, a = router.solve(router.Context(root=FULL, q=q, out_dir=Path(os.environ.get("TEMP", "/tmp")) / "stoa-ev",
                                           evidence=False, nemotron=False))
        assert a.answered and a.tier == "estimate", (q.path, q.question, a.value, a.tier)
        assert a.tier_reason
