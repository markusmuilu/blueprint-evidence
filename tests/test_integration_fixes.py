"""Integration check after the 14 Nov evening merges: question readings that must not need the language model."""
from bpqa import compose as C
from bpqa import phrasing as P
from bpqa.baseboard import parse_baseboard
from bpqa.checks import check_fixture_bounds
from bpqa.walls import _parse_inputs, spelled_percent


def test_finnish_heights_and_waste_are_read():
    # FZK Erdgeschoss: tiles to 1.2 m, not the full 2.5 m room height (16.64 m2 order, not 34.34)
    assert _parse_inputs("Paljonko seinälaattaa kylpyhuoneeseen 1,2 metrin korkeuteen?")["tile_m"] == 1.2
    assert _parse_inputs("laattaa kylpyhuoneeseen 120 cm korkeuteen 12 prosentin hukalla") == \
        {"tile_m": 1.2, "waste_pct": 12.0, "order": True}
    assert _parse_inputs("Fliesen im Bad bis 120 Zentimeter, 10 Prozent Verschnitt")["waste_pct"] == 10.0
    # 10372: "10 % hukalla" (hukka inflects: hukalla, hukan)
    assert _parse_inputs("Paljonko lattiamateriaalia tarvitaan makuuhuoneisiin 10 % hukalla?")["waste_pct"] == 10.0
    assert _parse_inputs("tiles for the bathroom walls up to 1.2 m with 15% waste")["tile_m"] == 1.2
    assert _parse_inputs("huonekorkeus on 2,6 metriä")["ceiling_m"] == 2.6
    assert parse_baseboard("Paljonko jalkalistaa makuuhuoneisiin 10 % hukalla?")["waste_pct"] == 10.0
    assert _parse_inputs("Paljonko parkettia makuuhuoneisiin 8 %:n hukalla?")["waste_pct"] == 8.0
    q = P.read("Paljonko lattiamateriaalia tarvitaan makuuhuoneisiin 10 % hukalla?", use_llm=False)
    assert "weak" not in q.extra and q.extra["takeoff"]["material"] == "flooring"


def test_spelled_percentages():
    assert spelled_percent("hukkaa kymmenen prosenttia") == "hukkaa 10 prosenttia"
    assert spelled_percent("add ten percent for cutting") == "add 10 percent for cutting"
    assert spelled_percent("incl. 10pct spare") == "incl. 10 % spare"
    assert spelled_percent("ten bedrooms") == "ten bedrooms"
    assert _parse_inputs("Lattiamateriaali makuuhuoneisiin, hukkaa kymmenen prosenttia")["waste_pct"] == 10.0
    # a waste percentage is not a share question
    assert C.plan("Lattiamateriaali makuuhuoneisiin, hukkaa kymmenen prosenttia") is None


def test_whole_flat_area_without_the_reader():
    for s in ("What is the total floor area?", "Paljonko neliöitä on yhteensä?", "Mikä on asunnon pinta-ala?",
              "How many square metres in total?", "What is the total area of the apartment?"):
        q = P.read(s, use_llm=False)
        assert (q.measure, q.obj, q.variant) == ("area", "room", "total"), s
    # the building's gross or net area stays out (property routes or the reader)
    for s in ("What is the building's total gross floor area?", "What is the total net floor area of the building?",
              "What is the gross floor area of each floor?"):
        assert P.read(s, use_llm=False).obj is None, s
    q = P.read("What is the total area of apartment A?", use_llm=False)
    assert q.obj == "apartment" and q.apartment == "A"


def test_list_the_rooms():
    for s in ("list the rooms", "List all rooms", "luettele huoneet", "What rooms are there?"):
        assert P.read(s, use_llm=False).measure == "text", s
        assert C.plan(s) == {"kind": "list", "types": []}, s
    assert C.plan("Can you give me the list of all room types?") is None


def test_one_error_label_does_not_replace_a_shower_count():
    # high_quality-14334: "PESUIHUONE" read for PESUHUONE; no shower or bathtub is drawn (gold 0)
    F = {"labels": {"bathroom": ["PESUIHUONE"], "kitchen": [], "wc_room": [], "bedroom": []},
         "labels_exact": {"bathroom": []}}
    (c,) = check_fixture_bounds(F, "shower_or_bath", 0)
    assert c.status == "fail" and c.suggest is None
    F["labels_exact"]["bathroom"] = ["KH"]
    assert check_fixture_bounds(F, "shower_or_bath", 0)[0].suggest == 1


def test_named_room_read_without_the_reader():
    q = P.read("What is the area of the cafe?", use_llm=False)
    assert (q.measure, q.obj, q.label) == ("area", "room", "cafe") and q.extra["label_from_text"]
    assert P.read("how big is MEETING 01", use_llm=False).label == "MEETING 01"
    k = P.read("How big is the kitchen?", use_llm=False)          # a room type stays a takeoff
    assert k.variant == "type_sum" and k.label is None and "single_room" not in k.extra
    for s in ("What is the area of the facade?", "How big is this plan?", "What is the area of the apartment?"):
        assert not P.read(s, use_llm=False).label, s


def test_room_labels_by_the_finnish_convention():
    from bpqa.routes.raster_summary import FI_ROOM, ROOM_LABEL
    assert all(ROOM_LABEL.fullmatch(p) for p in ("OH", "MH2", "H", "OLOHUONE", "HUONE", "BEDROOM", "LOUNGE"))
    assert not any(ROOM_LABEL.fullmatch(p) for p in ("K", "KPH", "WC", "ET", "VH", "S", "KITCHEN", "HALLWAY"))
    assert FI_ROOM.fullmatch("MH") and not FI_ROOM.fullmatch("BEDROOM")


def test_area_of_the_apartment_is_one_flat():
    q = P.read("What is the total area of the apartment?", use_llm=False)
    assert (q.obj, q.variant) == ("room", "total") and q.extra.get("one_apartment")
    assert not P.read("What is the total floor area?", use_llm=False).extra.get("one_apartment")


def test_qa_journey_round_3_readings():
    assert C.plan("What is the total area of the apartments on this floor?") == {"kind": "per_apartment", "types": []}
    assert C.plan("How many bathrooms per apartment?") == {"kind": "per_apartment", "types": ["bathroom"], "count": True}
    assert C.plan("How many meeting rooms are on this floor and how big are they?") == {"kind": "list", "types": ["meeting"]}
    # a material takeoff over two room types is one order, not two counts
    assert C.plan("How many square meters of parquet for all bedrooms and the living room?") is None
    assert C.split_parts("How many square metres are the kitchen and the bathroom together?") is None
    q = P.read("How big is the apartment?", use_llm=False)
    assert (q.measure, q.obj, q.variant) == ("area", "room", "total") and q.extra["one_apartment"]
    a = P.read("How big is apartment A?", use_llm=False)
    assert a.obj == "apartment" and a.apartment == "A"


def test_flat_numbering_assigns_rows_to_apartments():
    from bpqa.answer import Answer
    rows = C.rows_of(Answer(value=1, items=[{"number": "11-1", "name": "ELUTUBA", "area_m2": 20.0},
                                            {"number": "201", "name": "TREPIKODA", "area_m2": 9.0}]))
    assert [r["unit"] for r in rows] == ["11", None] and rows[0]["item"]["unit_from"] == "space numbering"
