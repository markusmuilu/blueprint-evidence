"""Takeoff parser and room-type classifier: python tests/test_takeoff.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.query import parse
from bpqa.takeoff import room_types

CASES = [
    ("How many square meters of tiling should I order for the toilets?", ["wc"], "tiles", "floor"),
    ("How many m2 of flooring do I need for the bedrooms?", ["bedroom"], "flooring", "floor"),
    ("What is the total floor area of the wet rooms?", ["bathroom", "wc", "sauna"], None, "floor"),
    ("Area of all bedrooms", ["bedroom"], None, "floor"),
    ("How many m² of paint for the kitchen?", ["kitchen"], "paint", "floor"),
    ("How many m² of wall tiles for the bathroom?", ["bathroom"], "tiles", "walls"),
    ("Kuinka monta neliötä laattaa kylpyhuoneisiin?", ["bathroom"], "tiles", "floor"),
    ("Kuinka monta neliötä laattaa KH:hon ja WC:hen?", ["wc", "bathroom"], "tiles", "floor"),
    ("Mikä on märkätilojen yhteenlaskettu pinta-ala?", ["bathroom", "wc", "sauna"], None, "floor"),
    ("Paljonko lattiapäällystettä tarvitaan makuuhuoneisiin?", ["bedroom"], "flooring", "floor"),
    ("Laske eteisen ja OH:n lattiapinta-ala", ["hall", "living"], None, "floor"),
    ("Wie viele Quadratmeter Fliesen brauche ich für die Badezimmer?", ["bathroom"], "tiles", "floor"),
]
NOT_TAKEOFF = [
    "How many bedrooms are there?",
    "What is the total net area in m² of all rooms labelled 'Schlafzimmer' on this floor?",
    "What is the net floor area of the room 'Kitchen (A103)' on 'Level 1', in m²?",
    "What is the net floor area of apartment B in m², summing all its rooms except balconies, loggias, terraces and other outdoor spaces?",
    "On sheet A-801, what is the total area of the rooms named RESTROOM in the finish room schedule?",
]
LABELS = {"Bad": {"bathroom"}, "Küche": {"kitchen"}, "Wohnen/Essen": {"living", "dining"}, "MH2": {"bedroom"},
          "KH": {"bathroom"}, "WC": {"wc"}, "Bathroom 1": {"bathroom"}, "TUALETTRUUM": {"wc"},
          "1IZQ-BAÑ": {"bathroom"}, "Chambre à coucher": {"bedroom"}, "Zimmer": {"room"},
          "doucheruimte": {"bathroom"}, "PH/KHH": {"bathroom", "utility"}, "Bad - Dame": {"bathroom"}}


def main():
    bad = 0
    for text, types, mat, surf in CASES:
        q = parse(text)
        tk = q.extra.get("takeoff")
        got = (q.variant, tk and sorted(tk["types"]), tk and tk["material"], tk and tk["surface"])
        want = ("type_sum", sorted(types), mat, surf)
        if got != want:
            bad += 1
            print("FAIL", text, got, want)
    for text in NOT_TAKEOFF:
        if parse(text).variant == "type_sum":
            bad += 1
            print("FAIL (takeoff fired)", text)
    for name, want in LABELS.items():
        if room_types(name) != want:
            bad += 1
            print("FAIL label", name, room_types(name), want)
    print("ok" if not bad else f"{bad} failures")
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
