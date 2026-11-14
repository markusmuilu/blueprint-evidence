"""Qualifiers that narrow the counted object ("fire doors"): pytest tests/test_qualifiers.py

Parser and router-gate tests need no dataset. The end-to-end cases run when the dataset is found
(STOA_DATA, or the --data folder used for development) and are skipped otherwise.
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa import qualifiers
from bpqa.query import parse
from bpqa.routes import dxf_swiss, ifc_route, pdf_text, raster_rooms


def kinds(question):
    return [x["kind"] for x in parse(question).extra.get("qualifiers", [])]


@pytest.mark.parametrize("question,kind", [
    ("How many fire doors are on this floor?", "fire"),
    ("How many fire-rated doors are there?", "fire"),
    ("How many doors have a fire rating?", "fire"),
    ("How many EI30 doors are there?", "fire"),
    ("Wie viele Brandschutztüren gibt es?", "fire"),
    ("Combien de portes coupe-feu y a-t-il ?", "fire"),
    ("Kuinka monta paloovea on?", "fire"),
    ("Hur många branddörrar finns det?", "fire"),
    ("How many exterior doors are there?", "external"),
    ("Wie viele Aussentüren gibt es?", "external"),
    ("Kuinka monta ulko-ovea on?", "external"),
    ("Combien de portes extérieures y a-t-il ?", "external"),
    ("How many interior doors are there?", "internal"),
    ("Kuinka monta sisäovea on?", "internal"),
    ("How many sliding doors are there?", "sliding"),
    ("Kuinka monta liukuovea on?", "sliding"),
    ("Wie viele Schiebetüren gibt es?", "sliding"),
    ("How many double doors are there?", "double"),
    ("How many glazed doors are there?", "glazed"),
    ("How many accessible doors are there?", "accessible"),
    ("How many emergency exit doors are there?", "emergency"),
    ("How many steel beams are there?", "material"),
    ("How many load-bearing walls are there?", "loadbearing"),
    ("How many north-facing windows are there?", "orientation"),
])
def test_qualifier_is_detected(question, kind):
    q = parse(question)
    assert q.ok()
    assert kind in kinds(question)
    assert q.extra["qualifier"]["kind"] == kinds(question)[0]
    assert q.extra["qualifier"]["raw"]


@pytest.mark.parametrize("question", [
    "How many doors are on this floor?",
    "How many windows are drawn on this floor plan?",
    "What is the fire rating of the doors?",                       # asks for the property, not a filtered count
    "How many doors are there in the model 'Smiley West (terraced houses)'?",
    "How many doors in the schedule are door type A1 (swing door)?",
    "On sheet A-801, how many doors in the door schedule are sliding?",
    "Wie viele Türen (Innentüren und Eingangstüren zusammen) sind auf diesem Grundriss eingezeichnet?",
    "Combien de portes (portes intérieures et portes d'entrée confondues) sont dessinées sur ce plan ?",
    "What is the total combined length of the exterior and interior walls?",
])
def test_no_qualifier_where_none_narrows(question):
    assert "qualifier" not in parse(question).extra


def test_exterior_still_sets_the_isexternal_filter():
    q = parse("What is the total length of exterior walls?")
    assert q.extra["external"] is True
    assert kinds("What is the total length of exterior walls?") == ["external"]
    assert parse("How many interior doors are there?").extra["external"] is False


def test_routes_that_cannot_filter_are_refused():
    q = parse("How many fire doors are on this floor?")
    for fn in (dxf_swiss.answer, pdf_text.answer, raster_rooms.answer):
        why = qualifiers.cannot_honour(fn, q)
        assert why and "fire" in why and "cannot prove" in why
    assert qualifiers.cannot_honour(ifc_route.answer, q) is None


def test_ifc_route_does_not_honour_what_it_cannot_filter():
    assert qualifiers.cannot_honour(ifc_route.answer, parse("How many glazed doors are there?"))
    assert qualifiers.cannot_honour(ifc_route.answer, parse("How many steel beams are there?"))


def test_unqualified_question_is_never_refused():
    q = parse("How many doors are on this floor?")
    for fn in (dxf_swiss.answer, pdf_text.answer, raster_rooms.answer, ifc_route.answer):
        assert qualifiers.cannot_honour(fn, q) is None


def test_external_may_fall_back_to_the_typed_lookups_but_fire_may_not():
    assert not qualifiers.blocks_fallback(parse("How many exterior doors are there?"))
    assert qualifiers.blocks_fallback(parse("How many fire doors are there?"))
    assert qualifiers.blocks_fallback(parse("How many exterior fire doors are there?"))


def test_final_abstention_is_final_and_says_why():
    q = parse("How many fire doors are on this floor?")
    a = qualifiers.final_abstention("", [], q)
    assert a.final and not a.answered and "fire" in a.abstain


class FakeDoor:
    def __init__(self, i, rating=None, op=None):
        self.id_ = i
        self.GlobalId = f"g{i}"
        self.Name = f"Door {i}"
        self.rating = rating
        self.OperationType = op


class FakeModel:
    def __init__(self, doors):
        self.doors = doors

    def entities(self, obj):
        return self.doors


def patch_props(monkeypatch):
    fake = NS(FIRE=__import__("re").compile("fire", __import__("re").I), placeholder=lambda k, v: False,
              flat_props=lambda e: [("Pset_DoorCommon", "FireRating", e.rating)] if e.rating else [],
              type_name=lambda e: e.Name, LOADBEAR_KEY=None)
    monkeypatch.setattr(qualifiers, "_props", lambda: fake)


def test_fire_filter_counts_only_rated_doors_and_states_the_filter(monkeypatch):
    patch_props(monkeypatch)
    doors = [FakeDoor(1, "EI30"), FakeDoor(2), FakeDoor(3, "EI30"), FakeDoor(4, "-"), FakeDoor(5, "EI60")]
    ents, note, tier_reason, why = qualifiers.filter_ifc(FakeModel(doors), doors, parse("How many fire doors?"), None)
    assert why is None and not tier_reason
    assert [d.id_ for d in ents] == [1, 3, 5]                 # "-" is a placeholder, not a rating
    assert "3 of 5 doors with a FireRating property" in note and "EI30" in note


def test_fire_filter_abstains_when_no_door_records_a_rating(monkeypatch):
    patch_props(monkeypatch)
    doors = [FakeDoor(1), FakeDoor(2)]
    ents, note, tier_reason, why = qualifiers.filter_ifc(FakeModel(doors), doors, parse("How many fire doors?"), None)
    assert why.startswith("cannot prove: no door in this model carries a fire rating")
    assert "2 doors" in why


def test_operation_filter_reads_operation_type(monkeypatch):
    patch_props(monkeypatch)
    doors = [FakeDoor(1, op="SLIDING_TO_LEFT"), FakeDoor(2, op="SINGLE_SWING_LEFT"), FakeDoor(3, op="DOUBLE_DOOR_SLIDING")]
    monkeypatch.setattr(qualifiers, "operation_values", lambda e: [e.OperationType])
    ents, note, _, why = qualifiers.filter_ifc(FakeModel(doors), doors, parse("How many sliding doors?"), None)
    assert why is None and [d.id_ for d in ents] == [1, 3]
    ents, note, _, why = qualifiers.filter_ifc(FakeModel(doors), doors, parse("How many double doors?"), None)
    assert [d.id_ for d in ents] == [3]


def test_unfilterable_qualifier_abstains_in_the_ifc_filter(monkeypatch):
    patch_props(monkeypatch)
    doors = [FakeDoor(1), FakeDoor(2)]
    ents, note, _, why = qualifiers.filter_ifc(FakeModel(doors), doors, parse("How many glazed doors?"), None)
    assert why.startswith("cannot prove") and "glazed" in why


# ---- end to end -------------------------------------------------------------------------------------------
DATA = Path(os.environ.get("STOA_DATA") or r"C:\Users\Markus\Coding\junction-2026\data\stoa-full\junction-dataset")
ESPLANADES = "data/ifc_models/bsc-esplanades_1807-ep-ar-v18_plan-01-1-korrus.pdf"
needs_data = pytest.mark.skipif(not (DATA / ESPLANADES).exists(), reason="dataset not found")


def ask(path, question):
    from bpqa.data import Question
    from bpqa.router import Context, solve
    ctx = Context(root=DATA, q=Question(qid=-1, path=path, question=question), out_dir=Path("out/evidence"),
                  evidence=False)
    return solve(ctx)[1]


@needs_data
def test_esplanades_fire_doors_are_27_not_46():
    a = ask(ESPLANADES, "How many fire doors are on this floor?")
    assert a.value == 27 and a.tier == "proven" and a.route == "ifc"
    assert "27 of 46 doors with a FireRating property" in a.explanation and "EI30" in a.explanation
    assert len(a.items) == 27                                   # only the rated doors are listed (and boxed)
    assert ask(ESPLANADES, "How many doors are on this floor?").value == 46


@needs_data
@pytest.mark.parametrize("path", ["data/ifc_models/kit-fzk-haus_ac20-fzk-haus_plan-01-erdgeschoss.pdf",
                                  "data/ifc_models/bsc-molio_molio-with-uris_plan-04-1-sal.pdf"])
def test_models_without_fire_ratings_cannot_prove_it(path):
    a = ask(path, "How many fire doors are on this floor?")
    assert not a.answered and a.final and "no door in this model carries a fire rating" in a.abstain


@needs_data
@pytest.mark.parametrize("path", ["data/swiss_dwellings/site1000_bldg2082_floor9385.pdf",
                                  "data/cubicasa5k/colorful-10052.png"])
def test_drawings_that_do_not_record_fire_ratings_abstain(path):
    a = ask(path, "How many fire doors are on this floor?")
    assert not a.answered and a.final and "fire" in a.abstain


# ---- follow-up: entrance doors, colloquial Finnish, emergency exits, IsExternal gaps -------------------------
@pytest.mark.parametrize("question,kind", [
    ("How many entrance doors are there?", "entrance"),
    ("How many front doors are there?", "entrance"),
    ("How many main doors are there?", "entrance"),
    ("Wie viele Eingangstüren gibt es?", "entrance"),
    ("Combien de portes d'entrée y a-t-il ?", "entrance"),
    ("Kuinka monta sisäänkäyntiä on?", "entrance"),
    ("Montako paloovea?", "fire"),
    ("Montako paloovea on tässä kerroksessa?", "fire"),
    ("Kuinka monta paloovea on?", "fire"),
    ("Montako ulkoovea on?", "external"),
    ("Montako liukuovea on?", "sliding"),
    ("How many emergency exits?", "emergency"),
    ("How many fire exits are there?", "emergency"),
])
def test_followup_phrasings(question, kind):
    q = parse(question)
    assert q.ok() and q.obj == "door" and q.measure == "count"
    assert q.extra["qualifier"]["kind"] == kind or kind in kinds(question)


def test_sisaankaynti_is_not_an_interior_door():
    assert parse("Kuinka monta sisäänkäyntiä on?").extra.get("external") is None


@pytest.mark.parametrize("question", [
    "How many doors (interior doors and entrance doors together) are drawn on this floor plan?",
    "How many doors, interior and entrance doors combined, are drawn?",
    "Wie viele Türen (Innentüren und Eingangstüren zusammen) sind auf diesem Grundriss eingezeichnet?",
    "Combien de portes (portes intérieures et portes d'entrée confondues) sont dessinées sur ce plan ?",
])
def test_entrance_and_interior_combined_means_all_doors(question):
    assert "qualifier" not in parse(question).extra


def test_entrance_needs_a_route_that_marks_them():
    q = parse("How many entrance doors?")
    for fn in (dxf_swiss.answer, pdf_text.answer, raster_rooms.answer):
        assert qualifiers.cannot_honour(fn, q)
    assert qualifiers.cannot_honour(ifc_route.answer, q) is None


def test_emergency_filter_abstains_without_the_property(monkeypatch):
    patch_props(monkeypatch)
    doors = [FakeDoor(1), FakeDoor(2)]
    _, _, _, why = qualifiers.filter_ifc(FakeModel(doors), doors, parse("How many emergency exits?"), None)
    assert why.startswith("cannot prove: no door in this model carries a fire-exit")


def test_emergency_filter_reads_fireexit(monkeypatch):
    fake = NS(flat_props=lambda e: [("Pset_DoorCommon", "FireExit", e.rating)] if e.rating is not None else [],
              type_name=lambda e: e.Name)
    monkeypatch.setattr(qualifiers, "_props", lambda: fake)
    doors = [FakeDoor(1, True), FakeDoor(2, False), FakeDoor(3), FakeDoor(4, True)]
    ents, note, _, why = qualifiers.filter_ifc(FakeModel(doors), doors, parse("How many emergency exits?"), None)
    assert why is None and [d.id_ for d in ents] == [1, 4] and "FireExit" in note


def test_entrance_filter_falls_back_to_isexternal_as_an_estimate(monkeypatch):
    patch_props(monkeypatch)
    doors = [FakeDoor(1), FakeDoor(2), FakeDoor(3)]
    M = FakeModel(doors)
    M.is_external = lambda e: {1: True, 2: False, 3: None}[e.id_]
    ents, note, tier_reason, why = qualifiers.filter_ifc(M, doors, parse("How many entrance doors?"), None)
    assert why is None and [d.id_ for d in ents] == [1] and "closest recorded property" in tier_reason
    M.is_external = lambda e: None
    assert qualifiers.filter_ifc(M, doors, parse("How many entrance doors?"), None)[3].startswith("cannot prove")


def test_isexternal_gap_is_said_in_both_answers():
    class M:
        is_external = staticmethod(lambda e: {1: True, 2: False, 3: None}[e.id_])
    doors = [FakeDoor(1), FakeDoor(2), FakeDoor(3)]
    for question, kept in (("How many exterior doors?", [doors[0]]), ("How many interior doors?", [doors[1]])):
        text = ifc_route.external_note(M, doors, kept, parse(question))
        assert "1 door has no IsExternal value" in text and "1 of 3 doors" in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
