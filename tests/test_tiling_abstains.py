"""Tiling read the same everywhere, language tag, split printed areas, foreign room words on sheets: pytest."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.phrasing import read
from bpqa.query import detect_lang, fold
from bpqa.routes.raster_area import join_split_decimals
from bpqa.routes.sheet_plan import question_words
from bpqa.takeoff import parse_takeoff
from bpqa.walls import summary as wall_summary


def test_floor_words_mean_floor_only():
    assert parse_takeoff("Kuinka monta neliömetriä laattaa tarvitaan kylpyhuoneiden lattioihin?")["floor_explicit"]
    assert parse_takeoff("Wie viele Quadratmeter Fliesen für den Boden der Badezimmer?")["floor_explicit"]
    assert parse_takeoff("tiles for the bathroom walls")["surface"] == "walls"
    # the floor as a storey is not a surface
    assert not parse_takeoff("How many m2 of tiling for the bathrooms on this floor?")["floor_explicit"]
    assert not parse_takeoff("How many square meters of tiling should I order for the toilets?")["floor_explicit"]


def test_plain_tiling_states_floor_only_and_floor_plus_walls():
    q = "How many square meters of tiling should I order for the bathrooms?"
    tk = read(q, use_llm=False).extra["takeoff"]
    room = {"label": "Bathroom", "floor_area": 5.0, "perimeter": 9.0, "height": 2.5, "openings": []}
    v, _, expl, tot = wall_summary([room], tk, q)
    assert v == round((5.0 + 22.5) * 1.1, 2)
    assert "Floor only: 5.00 m2, with 10% waste 5.50 m2" in expl
    assert "Floor plus walls: 27.50 m2" in expl


def test_one_foreign_storey_word_keeps_english():
    assert detect_lang(fold("How many windows are shown on the floor plan of 'Erdgeschoss'?")) == "en"
    assert detect_lang(fold("How many windows on Erdgeschoss?")) == "en"
    assert detect_lang(fold("Wie viele Fenster hat das Erdgeschoss?")) == "de"
    assert detect_lang(fold("Kuinka monta ovea?")) == "fi"


def test_split_decimal_area_is_joined():
    q = lambda x0, x1: [[x0, 10], [x1, 10], [x1, 22], [x0, 22]]
    toks = [{"text": "As 5", "conf": 0.9, "quad": q(0, 30), "rot": 0}, {"text": "62 m2", "conf": 0.99, "quad": q(34, 70), "rot": 0}]
    out = join_split_decimals(toks)
    assert len(out) == 1 and out[0]["text"] == "As 5,62 m2"
    far = [toks[0], dict(toks[1], quad=q(200, 240))]
    assert len(join_split_decimals(far)) == 2


def test_sheet_reads_finnish_and_german_room_words():
    assert "toilet" in question_words("Kuinka monta wc-tilaa rakennuksessa on?")
    assert "toilet" in question_words("Wie viele WC-Räume gibt es?")
    assert "corridor" in question_words("Kuinka iso on käytävä?")


def test_square_metres_of_wall_is_an_area():
    q = read("How many square meters of external wall?", use_llm=False)
    assert q.measure == "area" and q.variant == "side_area"
