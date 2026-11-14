"""Whole-model phrasing (keeps whole-model scope on a plan sheet) and fixture kinds. python tests/test_sheet_scope.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.query import parse, whole_model_asked  # noqa: E402
from bpqa.routes.ifc_route import FIXTURE_KINDS  # noqa: E402
import re  # noqa: E402

WHOLE = ["How many doors are there in the model 'X'?", "How many doors are in the building?",
         "How many windows are in the whole building?", "Hvor mange døre er der i modellen 'X'?",
         "Hoeveel deuren bevat het model 'X'?", "¿Cuál es el número de puertas en el modelo 'X'?",
         "Mitu ust on mudelis 'X'?"]
SHEET = ["How many doors are shown on this floor plan?", "How many rooms are on this floor plan?",
         "How many doors are shown on the floor plan of '1. Sal'?", "How many windows are there on this sheet?"]


def kind(txt):
    for k, rx in FIXTURE_KINDS:
        if re.search(rx, txt.lower()):
            return k


if __name__ == "__main__":
    for q in WHOLE:
        assert whole_model_asked(q) and parse(q).extra.get("whole_model"), q
    for q in SHEET:
        assert not whole_model_asked(q), q
    assert kind("Shower tray 90") == "shower" and kind("Bathtub 170") == "bath" and kind("WC pan") == "wc"
    assert kind("Washbasin 60") == "basin" and kind("Door") is None
    print("ok")
