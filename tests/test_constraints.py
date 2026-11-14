"""bpqa/constraints.py: what a question carries that an answer must apply (judge review 14 Nov)."""
from bpqa.constraints import detect, _cog_match


def kinds(q):
    return sorted((c["kind"], c["value"]) for c in detect(q))


def test_room_numbers():
    assert ("room_no", "3.05") in kinds("What is the area of room 3.05?")
    assert ("room_no", "3.14") in kinds("How big is kitchen 3.14?")
    assert ("room_no", "213") in kinds("What is the area of Kontor 213?")
    assert ("room_no", "205") in kinds("How many doors in room 205?")
    assert ("room_no", "MH 1") in kinds("How big is MH 1?")


def test_sizes_are_filters_not_room_numbers():
    k = kinds("How many doors are wider than 0.9 m?")
    assert ("compare", "0.9") in k and not any(x[0] == "room_no" for x in k)
    assert not any(x[0] == "room_no" for x in kinds("tiling for the bathrooms with 5% waste and 2.5 m ceilings"))


def test_groups_storeys_fire_who():
    assert ("group", "A") in kinds("How many metres of baseboard are needed for the bedrooms in apartment A?")
    assert ("group", "3") in kinds("How many toilets are in flat 3?")
    assert any(x[0] == "storey" for x in kinds("What is the total slab area on this floor?"))
    assert ("fire_class", "EI60") in kinds("How many doors on the 1st floor have fire rating EI60?")
    assert any(x[0] == "who" for x in kinds("How many toilets for men?"))
    assert ("named", "KORIDOR") in kinds("What is the total floor area of rooms named KORIDOR?")


def test_plain_questions_carry_nothing():
    for q in ["How many doors are there?", "What is the total area of all rooms?",
              "How many square meters of tiling should I order for the toilets?"]:
        assert [k for k in kinds(q) if k[0] != "measure"] == [], q


def test_measure_words():
    assert ("measure", "area") in kinds("What is the glazing area of all windows?")
    assert ("measure", "volume") in kinds("How many cubic meters of concrete are in the slabs?")
    assert not any(k[0] == "measure" for k in kinds("How many doors are there?"))


def test_label_cognates():
    assert _cog_match("corridors", "KORIDOR")
    assert not _cog_match("bedrooms", "KORIDOR")


def test_several_asks_and_which_questions():
    # ifc_bench dev, 14 Nov: one number marked proven for a question that asks several things, or asks for names
    assert ("parts", "") in kinds("What is the total number of building openings, and how many are doors versus windows?")
    assert ("parts", "") in kinds("What are the number of levels, building footprint area, total floor area, and total number of rooms?")
    assert ("parts", "") in kinds("What is the total tiling needed in all bathrooms, and what is the breakdown by bathroom?")
    assert ("parts", "") in kinds("How many meeting rooms are available, and where are they?")
    assert ("which", "") in kinds("Which sanitary furniture is present in room 110?")
    # a leading clause is not a second ask; a which-question with a measure word is answered by a number
    assert not any(k[0] == "parts" for k in kinds("On sheet A-801 of the drawings, how many doors are listed in the door schedule?"))
    assert not any(k[0] == "which" for k in kinds("Which room has the largest area?"))
