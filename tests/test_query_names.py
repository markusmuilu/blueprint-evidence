"""Parser: quoted names are not objects; one named room is not a total. python tests/test_query_names.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.query import parse  # noqa: E402

# (question, expected fields; "single" is extra["single_room"])
CASES = [
    ("How many doors are there in the model '10 Appartementen Schependomlaan'?", {"obj": "door", "single": False}),
    ("How many rooms (spaces) are there in the model 'Duplex Apartment - architectural'?", {"obj": "room"}),
    ('How many windows are there in the building "Wohnung Nord"?', {"obj": "window"}),
    ("How many apartments are there on this floor?", {"obj": "apartment"}),
    ("What is the net floor area of the room 'living room' in the model, in m²?",
     {"obj": "room", "label": "living room", "variant": None, "single": True}),
    ("What is the net floor area of the room 'Kitchen (A103)' on 'Level 1', in m²?",
     {"label": "Kitchen (A103)", "level": "Level 1", "single": True}),
    ("What is the net floor area of the room 'doucheruimte (12)' on '00 Begane grond', in m²?",
     {"obj": "room", "label": "doucheruimte (12)"}),
    ("Wie groß ist die Netto-Grundfläche des Raums „RLT-Raum (0-10)“ im Geschoss „B01_OKRD“ in m²?",
     {"obj": "room", "label": "RLT-Raum (0-10)"}),
    ("What is the total net area in m² of all rooms labelled 'Schlafzimmer' on this floor?",
     {"label": "Schlafzimmer", "single": False}),
    ("What is the net floor area of the living room in m²?", {"variant": "type_sum", "single": True}),
    ("How many square meters of tiling should I order for the bathrooms?", {"variant": "type_sum", "single": False}),
    ("How many m² of paint for the kitchen?", {"variant": "type_sum", "single": True}),
    ("What is the total floor area of all kitchens?", {"variant": "type_sum", "single": False}),
]


def main():
    bad = 0
    for text, want in CASES:
        q = parse(text)
        got = {"obj": q.obj, "label": q.label, "variant": q.variant, "level": q.level,
               "single": bool(q.extra.get("single_room") or (q.extra.get("takeoff") or {}).get("single_type"))}
        diff = {k: (got[k], v) for k, v in want.items() if got[k] != v}
        if diff:
            bad += 1
            print("FAIL", text, diff)
    print("ok" if not bad else f"{bad} failures")
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
