"""Night QA 15 Nov: wrong proven answers (room number summed over the floor, apartment scope in Finnish, litres)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bpqa import constraints  # noqa: E402
from bpqa.answer import Answer  # noqa: E402
from bpqa.query import Query  # noqa: E402
from bpqa.router import LITRES_Q  # noqa: E402
from bpqa.witnesses import query_from_dict  # noqa: E402


def test_multi_room_sum_does_not_apply_a_room_number():
    many = Answer(value=870.29, unit="m2", route="ifc", items=[{"guid": "a", "name": "X"}, {"guid": "b", "name": "Y"}],
                  explanation="870.288 = sum of 50 IfcSpace areas")
    one = Answer(value=78.4, unit="m2", route="ifc", items=[{"guid": "a", "name": "KORIDOR (105)"}],
                 explanation="78.40 m2 = IfcSpace KORIDOR (105)")
    assert constraints._multi_room(many) and not constraints._multi_room(one)


def test_finnish_swedish_german_apartment_scope_is_detected():
    for q, v in (("Montako makuuhuonetta on asunnossa B?", "B"), ("Kuinka monta kylpyhuonetta asunnossa A on?", "A"),
                 ("Mikä on huoneiston A pinta-ala?", "A"), ("Hur många sovrum finns i lägenheten B?", "B"),
                 ("Wie viele Schlafzimmer hat die Wohnung B?", "B")):
        groups = [c["value"] for c in constraints.detect(q) if c["kind"] == "group"]
        assert groups == [v], q


def test_litres_question_is_recognised():
    assert LITRES_Q.search("How many litres of paint do I need?")
    assert LITRES_Q.search("Kuinka monta litraa maalia tarvitaan?")
    assert not LITRES_Q.search("How many m2 of paint area?")


def test_witness_reading_from_the_main_answer_record():
    q = query_from_dict({"measure": "area", "obj": "room", "variant": "type_sum", "parser": "llm",
                         "extra": {"takeoff": {"types": ["wc"]}}, "unknown_key": 1})
    assert isinstance(q, Query) and q.variant == "type_sum" and q.extra["takeoff"]["types"] == ["wc"]
    assert query_from_dict(None) is None
