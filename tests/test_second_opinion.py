"""Unit tests of the Nemotron second-opinion step: reply parsing, the decision rules and the off-by-default switch.

    python -m pytest tests -q        (no network: the model call is replaced)
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa import second_opinion as S  # noqa: E402
from bpqa.answer import Answer  # noqa: E402


def test_parse_reply():
    assert S.parse_reply('{"count": 3}') == (3, None)
    assert S.parse_reply('```json\n{"count": 2, "boxes": [[1, 2, 3, 4]]}\n```') == (2, [[1, 2, 3, 4]])
    assert S.parse_reply('{"count": "4"}')[0] == 4
    assert S.parse_reply('{"count": 2.5}')[0] is None
    assert S.parse_reply("no idea") == (None, None)
    assert S.parse_reply(None) == (None, None)


def test_decide():
    assert S.decide("take_A", 3, 5, None) == 5
    assert S.decide("take_A", 3, None, None) == 3
    assert S.decide("agree_else_abstain_A", 3, 3, None) == 3
    assert S.decide("agree_else_abstain_A", 3, 4, None) is None
    assert S.decide("AB_override", 3, 4, 4) == 4
    assert S.decide("AB_override", 3, 4, 5) == 3
    assert S.decide("ours", 3, 9, 9) == 3


def _ans(tier="estimate", route="raster-ocr-labels"):
    return Answer(value=2, unit="count", route=route, tier=tier, confidence=0.3)


def _ctx(nemotron=True):
    return SimpleNamespace(nemotron=nemotron, file=Path("x.png"), q=SimpleNamespace(question="How many kitchens?"))


def test_off_by_default(monkeypatch):
    monkeypatch.delenv(S.ENV, raising=False)
    monkeypatch.setitem(S.POLICY, "kitchen", {"ask": "A", "rule": "take_A"})
    monkeypatch.setattr(S, "ask", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called while off")))
    a = _ans()
    assert S.maybe(_ctx(), SimpleNamespace(obj="kitchen", measure="count"), a) is a and a.value == 2


def test_replaces_only_estimates(monkeypatch, tmp_path):
    from PIL import Image
    f = tmp_path / "x.png"
    Image.new("RGB", (50, 50), "white").save(f)
    monkeypatch.setenv(S.ENV, "1")
    monkeypatch.setattr(S.N, "enabled", lambda: True)
    monkeypatch.setitem(S.POLICY, "kitchen", {"ask": "A", "rule": "take_A"})
    monkeypatch.setattr(S, "ask", lambda *a, **k: {"count": 1, "cached": True, "prompt_tokens": 0, "completion_tokens": 0})
    ctx = SimpleNamespace(nemotron=True, file=f, q=SimpleNamespace(question="How many kitchens?"))
    q = SimpleNamespace(obj="kitchen", measure="count")
    a = S.maybe(ctx, q, _ans())
    assert a.value == 1 and a.route.endswith("+nemotron") and a.tier == "estimate"
    assert a.checks[-1]["name"] == "nemotron-second-opinion" and a.checks[-1]["status"] == "replaced"
    p = _ans(tier="proven")
    assert S.maybe(ctx, q, p).value == 2                       # proven answers are never touched
    assert S.maybe(ctx, SimpleNamespace(obj="door", measure="count"), _ans()).value == 2   # no policy for door
