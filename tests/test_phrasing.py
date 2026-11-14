"""Question understanding: the LLM reading is normalised into the same Query the rules build (no API calls)."""
import json
from pathlib import Path

import pytest

from bpqa import phrasing as P
from bpqa.query import RuleParser


def llm(question: str, reply: dict):
    reading, why = P.normalise(reply, question)
    assert reading is not None, why
    return P.build(reading, question)


def same_shape(a, b):
    ta, tb = a.extra.get("takeoff"), b.extra.get("takeoff")
    assert (a.measure, a.obj, a.variant, a.label, a.room_no, a.apartment, a.level, a.scope) == \
           (b.measure, b.obj, b.variant, b.label, b.room_no, b.apartment, b.level, b.scope)
    assert (ta is None) == (tb is None)
    if ta:
        for k in ("types", "group", "material", "surface", "aggregation", "floor_explicit"):
            assert ta[k] == tb[k], k
    for k in ("single_room", "toilet_ambiguous", "unresolved_scope", "compound", "whole_model", "external"):
        assert a.extra.get(k) == b.extra.get(k), k


def test_loose_shape_becomes_the_takeoff_shape():
    """{measure: area, obj: bedroom, variant: total} is what a model writes; the routes want the takeoff."""
    q = "How big are the bedrooms in total?"
    same_shape(llm(q, {"measure": "area", "obj": "bedroom", "variant": "total"}), RuleParser().parse(q))
    same_shape(llm(q, {"intent": "area_rooms_of_type", "types": ["bedrooms"]}), RuleParser().parse(q))


@pytest.mark.parametrize("q,reply", [
    ("How many doors are there in total?", {"intent": "count_doors"}),
    ("How many windows does it have?", {"intent": "count_window"}),
    ("How many bedrooms are on this floor plan?", {"intent": "count_bedroom"}),
    ("What is the total net floor area of all rooms (spaces)?", {"intent": "area_room_total"}),
    ("What is the surface of apartment B in m2?", {"intent": "area_apartment", "apartment": "B"}),
    ("What is the scale of this drawing?", {"intent": "scale"}),
    ("What is the total length of all walls, in metres?", {"intent": "length_wall"}),
])
def test_canonical_intents_equal_the_rule_parse(q, reply):
    same_shape(llm(q, reply), RuleParser().parse(q))


def test_takeoff_inputs_reach_the_route():
    q = "tiles for the bathroom walls up to 1.2 m, add 15 percent waste"
    p = llm(q, {"intent": "area_rooms_of_type", "types": ["bathroom"], "material": "tiling", "surface": "walls",
                "waste_pct": 15, "tile_m": 1.2})
    tk = p.extra["takeoff"]
    assert tk["material"] == "tiles" and tk["surface"] == "walls"
    from bpqa.walls import parse_inputs
    inp = parse_inputs(q, tk)
    assert inp["waste_pct"] == 15 and inp["tile_m"] == 1.2


def test_invented_values_are_dropped_or_rejected():
    q = "how much paint for the living room walls"
    # a height and a waste the question never states are dropped, the parse stands
    reading, _ = P.normalise({"intent": "area_rooms_of_type", "types": ["living"], "material": "paint",
                              "surface": "walls", "ceiling_m": 2.5, "waste_pct": 10}, q)
    assert "inputs" not in reading
    # a name the question does not contain rejects the parse
    assert P.normalise({"intent": "area_room_named", "name": "Kitchen"}, q)[0] is None
    assert P.normalise({"intent": "area_apartment", "apartment": "Q"}, q)[0] is None
    # a surface the question does not name is not read in
    reading, _ = P.normalise({"intent": "area_rooms_of_type", "types": ["bathroom"], "material": "tiles",
                              "surface": "floor"}, "tiles for the bathrooms")
    assert "surface" not in reading


@pytest.mark.parametrize("reply", [
    "nope", [], {"intent": "fly_to_moon"}, {"intent": "area_rooms_of_type"},
    {"intent": "area_rooms_of_type", "types": ["garage"]},
    {"intent": "area_rooms_of_type", "types": ["bedroom"], "material": "gold"},
    {"measure": "area", "obj": "spaceship"},
])
def test_invalid_replies_are_rejected(reply):
    assert P.normalise(reply, "area of the bedrooms")[0] is None


def test_other_is_not_a_measurable_question():
    q = P.build(P.normalise({"intent": "other"}, "what is the fire rating of the doors")[0], "x")
    assert not q.ok()


def test_reading_says_what_the_model_read():
    q = llm("makuuhuoneiden koko yhteensa", {"intent": "area_rooms_of_type", "types": ["bedroom"]})
    assert q.parser == "llm"
    assert q.extra["reading"] == "area of rooms typed bedroom, summed"


def test_wet_group_expands_like_the_rules():
    q = "area of the wet rooms"
    same_shape(llm(q, {"intent": "area_rooms_of_type", "types": ["wet"]}), RuleParser().parse(q))


def test_policy_rules_first():
    """A routable rule parse is never sent to the model; quoted dataset templates keep the rule parse."""
    for q in ["How many doors are shown on this floor plan?",
              "What is the net floor area of the room 'Kitchen' on 'Level 1', in m2?",
              "How many doors are there in the model 'BIM-Speed Barlad apartment block (architecture + MEP)'?"]:
        assert P.weak(RuleParser().parse(q), q) is None


def test_policy_weak_parses():
    p = RuleParser()
    assert P.weak(p.parse("tell me about the sinks"), "tell me about the sinks")      # no measure: no parse
    s = p.parse("sinks please")                         # a fragment that starts with a plural noun: a count (QA 14 Nov)
    assert (s.measure, s.obj) == ("count", "sink") and P.weak(s, "sinks please") is None
    q = "how many cubic metres of walls are there"
    assert "units" in P.weak(p.parse(q), q)                                           # a count read for a volume
    q = "wall tiles for the bathroom up to 1.2 m"
    assert P.weak(p.parse(q), q) is None                                              # the rules read the height
    q = "tiles for the bathroom, the lower 3 m"
    assert "height" in P.weak(p.parse(q), q)                                          # a length the rules did not read
    q = "Fliesen für die Wände von Bad und WC mit 10 % Verschnitt"
    from bpqa.walls import parse_inputs
    assert parse_inputs(q)["waste_pct"] == 10
    a = p.parse("Hur stor är lägenhet A?")            # "hur stor" is a size question (integration check, 14 Nov)
    assert (a.measure, a.obj, a.apartment) == ("area", "apartment", "A") and P.weak(a, "Hur stor är lägenhet A?") is None
    q = "Wie viele Duschen und Badewannen gibt es?"
    assert "both" in P.weak(p.parse(q), q)
    assert P.weak(p.parse("area of the bedroom"), "area of the bedroom") is None       # takeoff, nothing ignored


def test_dataset_questions_keep_their_rule_parse():
    """The preview question list: of the questions the rules parse, only a handful free-text ones are weak (and so
    could be sent to the model); every templated question keeps its rule parse."""
    data = Path(__file__).resolve().parents[2] / "data" / "stoa-preview" / "junction-dataset-preview" / "questions.jsonl"
    if not data.exists():
        pytest.skip("dataset not available")
    seen, flagged = set(), []
    for line in data.read_text(encoding="utf-8").splitlines():
        qs = json.loads(line)["question"]
        if qs in seen:
            continue
        seen.add(qs)
        q = RuleParser().parse(qs)
        if q.ok() and P.weak(q, qs):
            flagged.append(qs)
    assert len(flagged) <= 8, flagged


def test_read_policy_with_a_fake_model(monkeypatch):
    """read(): rules first; the model's reading replaces a missing or weak rule parse, and says so."""
    calls = []

    def fake(question):
        calls.append(question)
        return {"intent": "area_rooms_of_type", "types": ["bedroom"]}, "", 0.0, 1.0

    monkeypatch.setattr(P, "_llm_reading", fake)
    monkeypatch.setattr(P.N, "enabled", lambda: True)
    # a routable rule parse: the model is never asked
    q = P.read("How many doors are shown on this floor plan?")
    assert q.parser == "rules" and not calls
    # no rule parse: the reading is the takeoff the rules would build, labelled as read by the model
    q = P.read("sovrummens storlek, tack")
    assert q.parser == "llm" and q.extra["takeoff"]["types"] == ["bedroom"]
    assert q.extra["reading"] == "area of rooms typed bedroom, summed" and q.extra["llm_reason"]
    # the model switched off: the rule parse stands, marked weak
    q = P.read("sovrummens storlek, tack", use_llm=False)
    assert q.parser == "rules" and not q.ok() and q.extra["weak"]
    # a reading that is not a measurable question leaves the (empty) rule parse in place
    monkeypatch.setattr(P, "_llm_reading", lambda question: ({"intent": "other"}, "", 0.0, 1.0))
    q = P.read("tell me about the fire rating")
    assert not q.ok()


def test_parse_call_has_its_own_deadline_and_failures_are_not_cached(monkeypatch, tmp_path):
    seen = []

    def fake_chat(prompt, png=None, **kw):
        seen.append(kw.get("deadline_s"))
        if len(seen) == 1:
            return {"content": None, "error": "HTTP 429"}
        return {"content": '{"intent": "count_door"}', "prompt_tokens": 10, "completion_tokens": 5, "latency_s": 0.5}

    monkeypatch.setattr(P.N, "chat", fake_chat)
    monkeypatch.setattr(P.N, "enabled", lambda: True)
    monkeypatch.setattr(P, "PARSE_CACHE", tmp_path)
    monkeypatch.setattr(P, "PARSE_DEADLINE_S", 4.0)
    P._mem.clear()
    q = P.llm_parse("doors pls?")
    assert not q.ok() and "no reply" in q.extra["llm_rejected"]          # a 429 falls back at once
    q = P.llm_parse("doors  pls")                                         # not cached; whitespace and ? normalised
    assert q.ok() and q.obj == "door"
    q = P.llm_parse("doors pls?")                                         # served from the cache, no third call
    assert q.ok() and len(seen) == 2 and seen == [4.0, 4.0]
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_room_type_never_dropped_qa_nov14():
    # an LLM reading that drops the named room type is typed from the question, or rejected
    r, _ = P.normalise({"intent": "count_room"}, "how many offices")
    assert r["intent"] == "count_office"
    assert P.normalise({"intent": "count_room"}, "how many hallways")[0] is None          # no count route for halls
    assert P.normalise({"intent": "count_room"}, "how many server rooms")[0] is None      # unknown room type
    r, _ = P.normalise({"intent": "area_room_total"}, "area of the meeting rooms")
    assert r["intent"] == "area_rooms_of_type" and r["types"] == ["meeting"]
    r, _ = P.normalise({"intent": "area_room_named", "name": "kylpyhuone"}, "kuinka iso kylpyhuone on")
    assert r["intent"] == "area_rooms_of_type" and r["types"] == ["bathroom"]
    # a rule parse that would sum all rooms for an unknown room type is blocked (the router abstains)
    q = P.read("area of lab rooms", use_llm=False)
    assert q.extra.get("blocked")
    assert not P.read("total area of all rooms", use_llm=False).extra.get("blocked")


def test_casual_phrasings_qa_nov14():
    assert P.casual("how big r the bedrooms") == "how big are the bedrooms"
    assert P.casual("how many dooors in the ground floor") == "how many doors in the ground floor"
    assert P.casual("What is the total area of all rooms?") == "What is the total area of all rooms?"
    for text, obj in [("bedrooms?", "bedroom"), ("doors?", "door"), ("ulko-ovet?", "door"),
                      ("paljonko ikkunoita", "window"), ("windows whole house", "window")]:
        q = P.read(text, use_llm=False)
        assert (q.measure, q.obj) == ("count", obj), text
    assert P.read("windows whole house", use_llm=False).extra.get("whole_model")
    assert P.read("hur många fönster i hela huset", use_llm=False).extra.get("whole_model")
    q = P.read("biggest room?", use_llm=False)
    assert (q.measure, q.obj, q.variant) == ("area", "room", "max")
    q = P.read("how many rooms in apartment A", use_llm=False)
    assert (q.obj, q.apartment, q.extra.get("apartment_scope")) == ("room", "A", True)


def test_count_word_typos_and_storey_phrases():
    assert P.casual("Kuinka motna ikkunaa koko talossa on?") == "Kuinka monta ikkunaa koko talossa on?"
    q = P.read("Kuinka motna ikkunaa koko talossa on?", use_llm=False)
    assert (q.measure, q.obj, bool(q.extra.get("whole_model"))) == ("count", "window", True)
    from bpqa.router import STOREY_NAMED
    for s in ("how many doors are on the 3rd floor?", "doors on 3.korrus", "kuinka monta ovea 5. kerroksessa on",
              "doors on the second floor", "doors on level 2"):
        assert STOREY_NAMED.search(s), s
    assert not STOREY_NAMED.search("how many doors are on this floor plan?")


def test_swedish_area_words_reach_the_rules():
    # "sovrummens yta", "koksytan": Swedish area words are rewritten for the rule parse (bpqa/understand.py)
    for text, t in [("Vad är sovrummens yta?", "bedroom"), ("Vad är köksytan?", "kitchen"),
                    ("Vad är badrummets yta?", "bathroom"), ("sovrummens golvyta, tack", "bedroom")]:
        q = P.read(text, use_llm=False)
        assert q.ok() and q.extra["takeoff"]["types"] == [t], text
