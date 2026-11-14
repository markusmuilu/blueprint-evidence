"""Piping (IFC): question parse and the model-side answer. pytest tests/test_piping.py"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa import piping as P  # noqa: E402

DUPLEX = Path(r"C:\Users\Markus\Coding\junction-2026\data\stoa-full\junction-dataset\data\ifc_models\bsc-duplex_duplex-plumbing-20121113.ifc")


def test_parse():
    assert P.parse_piping("What is the total pipe length?") == {"kind": "length", "by": None}
    assert P.parse_piping("Pipe length by system")["by"] == "system"
    assert P.parse_piping("Total piping in metres per diameter")["by"] == "diameter"
    assert P.parse_piping("How many pipe fittings are there?")["kind"] == "fittings"
    assert P.parse_piping("Wie viele Rohre gibt es?")["kind"] == "segments"


def test_not_piping():
    assert P.parse_piping("How many m2 of tiles for the bathrooms?") is None
    assert P.parse_piping("How many doors are there?") is None
    # type lists and broad analyses stay with the property route
    assert P.parse_piping("What pipe types are in the model?") is None
    assert P.parse_piping("What pipe fitting types and quantities are installed?") is None
    assert P.parse_piping("Give a comprehensive analysis of the piping") is None


def test_no_ifc_is_cannot_prove():
    ctx = SimpleNamespace(q=SimpleNamespace(question="What is the total pipe length?"), model_ifc=lambda: None)
    a = P.text_answer(ctx, SimpleNamespace(extra={}))
    assert not a.answered and "cannot prove" in a.abstain and "no IFC model" in a.abstain


def test_duplex_plumbing():
    """Sum of Dimensions.Length of the 231 pipe segments = 151442.44 mm, 227 IfcFlowFitting, both checked by hand against the IFC."""
    import pytest
    if not DUPLEX.exists():
        pytest.skip("dataset not available")
    from bpqa.routes import ifc_route as R
    M = R.load(str(DUPLEX))
    ctx = SimpleNamespace(evidence=False, q=SimpleNamespace(question="What is the total pipe length?"))
    q = SimpleNamespace(extra={"piping": P.parse_piping(ctx.q.question)})
    a = P.answer(ctx, q, M, DUPLEX)
    assert a.answered and abs(a.value - 151.44) < 0.01 and a.unit == "m" and a.tier is None
    assert a.checks and a.checks[0]["status"] == "pass"
    ctx.q.question = "How many pipe fittings?"
    q = SimpleNamespace(extra={"piping": P.parse_piping(ctx.q.question)})
    a = P.answer(ctx, q, M, DUPLEX)
    assert a.value == 227
