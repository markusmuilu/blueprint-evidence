"""Slab area by type and thickness: question parse and the model-side logic on a small synthetic IFC. pytest tests/test_slabs.py"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa import slabs as S  # noqa: E402
from bpqa.query import parse  # noqa: E402


def test_parse_by_type_thickness_floor():
    p = parse("Show the slab area by type")
    assert (p.measure, p.obj, p.variant) == ("area", "slab", "slab_report") and p.extra["slab"]["group"]
    assert "compound" not in p.extra
    s = parse("How many m2 of 200 mm slab are there?").extra["slab"]
    assert s["thickness_mm"] == 200
    assert parse("slab area of the 0.25 m slabs").extra["slab"]["thickness_mm"] == 250
    assert parse("What is the total floor slab area?").extra["slab"]["predefined"] == "FLOOR"
    assert parse("Wie gross ist die Flaeche der Bodenplatte in m2?").extra["slab"]["predefined"] == "BASESLAB"


def test_rebar_question():
    s = parse("How many metres of rebar are in the slabs, and what is the slab area?").extra["slab"]
    assert s["rebar"]
    assert parse("Bewehrung der Geschossdecke").extra["slab"]["rebar"]


def test_other_slab_questions_keep_their_parse():
    p = parse("What is the total volume of all floor slabs combined?")
    assert p.variant != "slab_report"
    p = parse("What are the slab types and their thicknesses?")
    assert p.variant != "slab_report"
    p = parse("How many m2 of tiles do I need for the bathrooms?")
    assert p.variant == "type_sum"


def test_thickness_ignores_area_units():
    assert S.parse_slab("area of slabs, 12 m2 total")["thickness_mm"] is None


class _Slab(SimpleNamespace):
    pass


def test_model_logic_on_a_real_model():
    """fantasy_office_building_2: 5 FLOOR slabs of 200 mm with NetArea quantities (sum checked against the IFC by hand)."""
    import pytest
    ifc = Path(r"C:\Users\Markus\Coding\junction-2026\data\stoa-full\junction-dataset\data\ifc_bench\fantasy_office_building_2_arc.ifc")
    if not ifc.exists():
        pytest.skip("dataset not available")
    from bpqa.answer import Answer
    from bpqa.routes import ifc_route as R
    M = R.load(str(ifc))
    ctx = SimpleNamespace(evidence=False, q=SimpleNamespace(question=""))
    q = parse("How many m2 of 200 mm slab?")
    a = S.answer(ctx, q, M, ifc, None)
    assert isinstance(a, Answer) and a.answered and abs(a.value - 1732.03) < 0.01 and a.tier is None
    q = parse("How many m2 of 250 mm slab?")
    a = S.answer(ctx, q, M, ifc, None)
    assert a.value == 0.0
    q = parse("slab reinforcement bars")
    a = S.answer(ctx, q, M, ifc, None)
    assert not a.answered and a.final and "no reinforcement objects" in a.abstain and "13 IfcSlab" in a.abstain
