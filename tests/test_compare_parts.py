"""Final judge review 15 Nov, fix 5: two apartments compared, and the area of each apartment."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bpqa import compose  # noqa: E402


def test_comparison_is_read():
    for q, by in (("Is apartment A or B bigger, and by how much?", True), ("Which apartment is bigger, A or B?", False),
                  ("How much bigger is apartment A than apartment B?", True), ("Kumpi on isompi, asunto A vai B?", False),
                  ("Is flat 1 or flat 2 larger?", False)):
        p = compose.plan(q)
        assert p and p["kind"] == "compare_apartments" and p["by"] is by, q
    p = compose.plan("What is the difference in area between apartment A and apartment B?")
    assert p["kind"] == "compare_apartments" and p["number"] and (p["a"], p["b"]) == ("A", "B")


def test_not_a_comparison():
    for q in ("How many more rooms does apartment A have than B?", "What is the area of apartment A?",
              "How many m2 of tiles should I order for apartment A or B?"):
        p = compose.plan(q)
        assert not p or p["kind"] != "compare_apartments", q


def test_area_of_each_apartment_is_per_apartment():
    for q in ("What is the area of each apartment?", "How big is each apartment?"):
        assert compose.plan(q)["kind"] == "per_apartment", q
