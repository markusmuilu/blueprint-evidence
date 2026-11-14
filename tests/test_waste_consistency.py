"""A takeoff value and its explanation say the same number, and a stated waste reaches every parser's total (QA 14 Nov evening)."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa import takeoff, walls  # noqa: E402
from bpqa.query import parse  # noqa: E402

ROOM = {"label": "Bad", "number": "3", "floor_area": 12.0, "perimeter": 14.0, "height": 2.5, "height_src": "model",
        "openings": [{"kind": "door", "width": 0.9, "z0": 0.0, "z1": 2.0}]}


def lead(expl: str) -> float:
    return float(re.search(r"(\d+(?:\.\d+)?) m2", expl).group(1))


def wall_case(q: str):
    tk = parse(q).extra["takeoff"]
    value, _, expl, totals = walls.summary([dict(ROOM)], tk, q)
    return value, expl, totals


def test_net_wall_question_states_no_waste():
    value, expl, totals = wall_case("Paljonko seinälaattaa kylpyhuoneeseen 1,2 metrin korkeuteen?")
    assert totals["waste_in_value"] is True          # tile material: an order quantity
    value, expl, totals = wall_case("How many m2 of wall do I need in the bathroom up to 1.2 m?")
    assert not totals["waste_in_value"] and value == totals["net_m2"]
    assert lead(expl) == value
    assert not re.search(r"waste \d+%|order \d", expl), expl      # no waste figure the value does not contain


def test_order_wall_question_value_equals_stated_order():
    for q in ("Paljonko seinälaattaa kylpyhuoneeseen 1,2 metrin korkeuteen?",
              "How many m2 of wall tiles to order for the bathroom up to 1.2 m?",
              "wall tiles for the bathroom with 15% waste"):
        value, expl, totals = wall_case(q)
        assert totals["waste_in_value"] and lead(expl) == value, (q, expl)
        assert f"order {value:.2f} m2" in expl and value == totals["order_m2"] > totals["net_m2"]


def test_language_model_waste_reaches_the_floor_total():
    # the language model keeps a stated waste under "inputs"; the total must still carry it
    tk = {"types": ["bedroom"], "group": None, "material": "flooring", "surface": "floor", "aggregation": "sum",
          "floor_explicit": True, "inputs": {"waste_pct": 10.0}}
    takeoff.settle(tk, "Paljonko lattiamateriaalia tarvitaan makuuhuoneisiin 10 % hukalla?")
    rows = [{"label": "MH", "area": 11.4, "source": "printed"}, {"label": "MH", "area": 9.7, "source": "printed"},
            {"label": "MH", "area": 12.2, "source": "printed"}]
    total, _, expl = takeoff.summary(rows, tk)
    assert total == 36.63 and lead(expl) == total and "10% waste" in expl


def test_settle_from_question_text_and_default():
    tk = {"types": ["bedroom"], "material": "flooring", "surface": "floor"}
    takeoff.settle(tk, "How many m2 of flooring to order for the bedrooms?")
    assert tk["waste_pct"] == walls.DEFAULT_WASTE_PCT
    tk = {"types": ["bedroom"], "material": "flooring", "surface": "floor"}
    takeoff.settle(tk, "How many m2 of flooring do I need for the bedrooms?")
    assert tk.get("waste_pct") is None                 # "need" alone stays net


def test_floor_value_equals_explanation():
    for q in ("How many m2 of floor tiles to order for the bathrooms?", "Floor area of the bathrooms?"):
        tk = parse(q).extra["takeoff"] if parse(q).extra.get("takeoff") else {"types": ["bathroom"], "surface": "floor"}
        takeoff.settle(tk, q)
        total, _, expl = takeoff.summary([{"label": "Bad", "area": 6.0, "source": "x"}], tk)
        assert lead(expl) == total, (q, expl)
