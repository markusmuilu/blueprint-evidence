"""QA box round 2 (14 Nov): wrong proven answers and gaps on IFC-backed drawings, read by the rule parser and the guard."""
from bpqa.constraints import detect
from bpqa.query import RuleParser, defiller, fi_slab, fold


def parse(q):
    return RuleParser().parse(q)


def kinds(q):
    return sorted((c["kind"], c["value"]) for c in detect(q))


def test_finnish_slab_words_with_slab_context():
    q = parse("Mikä on laattojen pinta-ala paksuuksittain?")
    assert (q.measure, q.obj, q.variant) == ("area", "slab", "slab_report")
    assert q.extra["slab"]["group"] is True
    assert fi_slab(fold("betonilaatan paksuus")) or fi_slab(fold("kantavan laatan pinta-ala"))
    # laatta alone is the tile of a tiling takeoff
    assert not fi_slab(fold("kuinka monta neliota laattaa kylpyhuoneeseen"))
    assert parse("Kuinka monta neliötä laattaa tarvitaan kylpyhuoneeseen?").variant == "type_sum"


def test_steps_are_their_own_object():
    for text in ("Kuinka monta porras askelta on talossa?", "How many steps does the staircase have?",
                 "How many risers does the stair have?"):
        q = parse(text)
        assert (q.measure, q.obj) == ("count", "stair_step"), text
    assert parse("How many stairs are there?").obj == "stair"
    assert ("steps", "steps") in kinds("How many steps does the staircase have?")


def test_size_filter_is_a_comparison_not_a_room():
    for text in ("Kuinka moni ovi on leveämpi kuin 0.9 metriä?", "How many doors are wider than 0.9 m?",
                 "How many doors are wider than 0.9?"):
        assert not [c for c in detect(text) if c["kind"] == "room_no"], text
        assert [c for c in detect(text) if c["kind"] == "compare"], text
        q = parse(text)
        assert (q.measure, q.obj) == ("count", "door")
        assert q.extra["size_filter"]["dim"] == "width" and q.extra["size_filter"]["value_m"] == 0.9
    q = parse("How wide are the doors?")
    assert (q.measure, q.variant, q.extra["size_dim"]) == ("text", "size_list", "width")


def test_how_much_concrete_is_a_volume():
    for text in ("Paljonko betonia on laatoissa?", "Kuinka monta kuutiota betonia laatoissa on?"):
        q = parse(text)
        assert (q.measure, q.obj) == ("volume", "slab"), text


def test_colloquial_fillers():
    assert defiller("Kuinkas monta ovea sitten kerroksessa 3 on?") == "Kuinka monta ovea kerroksessa 3 on?"
    q = parse("Kuinkas monta ovea sitten kerroksessa 3 on?")
    assert (q.measure, q.obj) == ("count", "door")
    assert q.raw == "Kuinkas monta ovea sitten kerroksessa 3 on?"


def test_guard_withdraws_a_stair_count_for_steps():
    from types import SimpleNamespace as N
    from bpqa import constraints as C
    from bpqa.answer import Answer
    from bpqa.query import Query
    q = Query(raw="x", measure="count", obj="stair")
    a = Answer(value=1, unit="count", route="ifc", confidence=0.9, explanation="1 = 1 x IfcStair on storey Erdgeschoss")
    for text in ("How many steps does the staircase have?", "Kuinka monta porras askelta on talossa?"):
        assert [m for m in C.unused(N(q=N(question=text)), q, a) if m["kind"] == "steps"], text
    assert not C.unused(N(q=N(question="How many stairs are there?")), q, a)
