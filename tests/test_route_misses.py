"""Route misses found by QA (2026-11-14): room-type counts, "toilets" on IFC plans, French/Italian room words,
unnamed numbered rooms in the net indoor total, check-adopted counts, the drawing scale.

    python -m pytest tests/test_route_misses.py      (or: python tests/test_route_misses.py)

The unit tests need no dataset. The tests marked `data` run the QA questions on the real files and are
skipped when the full dataset is not next to the repo (or under STOA_FULL).
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa import checks, takeoff
from bpqa.answer import Answer, abstain
from bpqa.query import parse

REPO = Path(__file__).resolve().parents[1]
FULL = Path(os.environ.get("STOA_FULL") or REPO.parent / "data" / "stoa-full" / "junction-dataset")
data = pytest.mark.skipif(not (FULL / "questions.jsonl").exists(), reason="full dataset not available")


# ---------------------------------------------------------------- parser

@pytest.mark.parametrize("q,obj", [
    ("How many bedrooms?", "bedroom"),
    ("How many bathrooms are there?", "bathroom"),
    ("How many toilet rooms are there?", "wc_room"),
    ("Combien de chambres y a-t-il ?", "bedroom"),
    ("Combien de salles de bains?", "bathroom"),
    ("Quanti bagni ci sono?", "bathroom"),
    ("Quante camere da letto ci sono?", "bedroom"),
    ("Wie viele Schlafzimmer gibt es?", "bedroom"),
    ("Combien de balcons?", "balcony"),
    ("Mitu tualettruumi on sellel korrusel?", "wc_room"),
])
def test_room_type_count_questions(q, obj):
    p = parse(q)
    assert (p.measure, p.obj) == ("count", obj)


def test_bare_toilets_is_ambiguous_and_fixed_phrasings_unchanged():
    p = parse("How many toilets are on this floor?")
    assert p.obj == "toilet" and p.extra.get("toilet_ambiguous")
    for q, obj in [("How many toilets (WC) are drawn on this floor plan?", "toilet"),
                   ("How many toilet bowls (WC seats) are drawn on this floor plan?", "toilet"),
                   ("How many separate WC rooms (toilet rooms marked WC) are on this floor plan?", "wc_room")]:
        p = parse(q)
        assert p.obj == obj and not p.extra.get("toilet_ambiguous")


def test_labelled_and_scoped_questions_keep_their_parse():
    p = parse("How many rooms labelled 'Bad' are there on this floor?")
    assert (p.obj, p.label) == ("room", "Bad")
    p = parse("Combien de pièces portant la désignation « Cuisine » y a-t-il à cet étage ?")
    assert (p.obj, p.label) == ("room", "Cuisine")
    assert parse("How many toilets are on the fifth floor?").extra.get("unresolved_scope")
    assert parse("How many bathrooms are there in total, and how are they distributed across the floors?").extra.get("compound")


def test_scale_question():
    for q in ("What is the scale of this drawing?", "Quelle est l'échelle de ce plan ?", "Welcher Massstab hat der Plan?"):
        p = parse(q)
        assert (p.measure, p.obj) == ("text", "scale"), q


# ---------------------------------------------------------------- room-type vocabulary

@pytest.mark.parametrize("label,types", [
    ("Toilettes", {"wc"}), ("Gabinetto", {"wc"}), ("Doccia", {"bathroom"}), ("Salle à manger", {"dining"}),
    ("Salotto", {"living"}), ("Waschen/Trocknen", {"utility"}), ("Kellerabteil", {"storage"}), ("Halle", {"hall"}),
    ("Cuisinette", {"kitchen"}), ("Chambre à coucher", {"bedroom"}), ("Camera da letto", {"bedroom"}),
    ("Pièce", {"room"}), ("Stanza", {"room"}), ("Zimmer", {"room"}),     # generic: never a bedroom
])
def test_room_words(label, types):
    assert takeoff.room_types(label) == types


# ---------------------------------------------------------------- counts from labels

def rooms(*names):
    return [{"name": n, "number": f"2.{i:02d}"} for i, n in enumerate(names)]


def test_generic_rooms_are_not_bedrooms_and_no_zero_is_claimed():
    rt = takeoff.count_room_type(rooms("Pièce", "Pièce", "Cuisine", "Bain"), "bedroom")
    assert rt["value"] is None and rt["final"] and "Pièce" in rt["why"]
    rt = takeoff.count_room_type(rooms("Schlafzimmer", "Schlafzimmer", "Wohnen", "Bad"), "bedroom")
    assert rt["value"] == 2 and not rt["estimate"]
    rt = takeoff.count_room_type(rooms("Schlafzimmer", "Zimmer", "Wohnen"), "bedroom")
    assert rt["value"] == 1 and "generic" in rt["estimate"]


def test_unnamed_rooms_typed_from_fixtures():
    rs = rooms("Bad", None, None, "Küche")
    kinds = {"2.00": {"toilet", "washbasin"}, "2.01": {"bathtub", "toilet"}, "2.02": {"toilet"}, "2.03": set()}
    fx = lambda r: kinds[r["number"]]
    rt = takeoff.count_room_type(rs, "bathroom", fixtures=fx)
    assert rt["value"] == 2 and "no name" in rt["estimate"] and "may be separate WCs" in rt["estimate"]
    rt = takeoff.count_room_type(rs, "wc_room", fixtures=fx)
    assert rt["value"] == 1


def test_takeoff_generic_reason():
    assert takeoff.generic_reason(rooms("Pièce", "Bain"), ["bedroom"])
    assert not takeoff.generic_reason(rooms("Pièce", "Chambre à coucher"), ["bedroom"])
    assert not takeoff.generic_reason(rooms("Pièce", "Cuisine"), ["bathroom"])


def test_unnamed_numbered_rooms_in_indoor_total():
    from shapely.geometry import box
    from bpqa.routes.dxf_swiss import SwissFloor
    fl = NS(fixture_centroids=[(0.5, 0.5)], is_outdoor=lambda r: False)
    r = lambda poly, name=None, number="2.07": {"poly": poly, "name": name, "number": number}
    f = lambda rr: SwissFloor.counts_as_indoor(fl, rr)
    assert f(r(box(10, 10, 12, 11.5)))        # 1.5 m wide, no name, no fixture: a room (store), counted
    assert not f(r(box(10, 10, 13, 10.6)))    # 0.6 m wide (1.8 m2), no name, no fixture: a shaft, not counted
    assert f(r(box(0, 0, 1, 1)))              # 1 m wide with a fixture inside: a WC, counted
    assert not f(r(box(10, 10, 12, 11.5), number=None))


# ---------------------------------------------------------------- IFC room-type counts (stub model)

def stub_model(names):
    st = NS(Name="EG")
    sps = [NS(Name=f"{i:03d}", LongName=n, id=(lambda i=i: i)) for i, n in enumerate(names)]
    return NS(entities=lambda k: sps if k == "room" else [], storey_of=lambda e: st), st


def test_ifc_toilets_mean_toilet_rooms():
    from bpqa.routes import ifc_route
    M, st = stub_model(["WC Damen", "WC Herren", "Office", "TUALETTRUUM"])
    rc = ifc_route.room_type_count(M, st, parse("How many toilets are on this floor?"))
    assert rc["value"] == 3 and "toilet rooms" in rc["note"] and "no WC fixtures" in rc["note"]
    rc = ifc_route.room_type_count(M, st, parse("How many toilet bowls are drawn on this floor plan?"))
    assert "why" in rc        # fixtures asked, the model has none: abstain, do not count rooms
    M, st = stub_model(["Bathroom 1", "Bedroom 1", "Bedroom 2", "Kitchen"])
    assert ifc_route.room_type_count(M, st, parse("How many bedrooms?"))["value"] == 2


# ---------------------------------------------------------------- router and checking layer

def test_final_abstention_is_not_handed_to_the_model(monkeypatch):
    from bpqa import router
    called = []
    monkeypatch.setattr(router, "candidate_routes", lambda ctx, q=None: [
        lambda c, qq: Answer(route="dxf-geometry", abstain="labels do not say", final=True)])
    monkeypatch.setattr(router.nemo_client, "enabled", lambda: True)
    monkeypatch.setattr(router.nemotron, "answer", lambda *a: called.append(1))
    monkeypatch.setattr(router, "text_routes", lambda ctx: [])
    ctx = router.Context(root=REPO, q=NS(question="How many bedrooms?", ext="pdf", path="x.pdf"), out_dir=REPO / "out",
                         evidence=False)
    monkeypatch.setattr(router, "no_derivatives", lambda c: False)
    _, a = router.solve(ctx)
    assert not called and a.abstain == "labels do not say"


def test_adopted_check_value_leads_the_explanation(monkeypatch):
    c = checks.Check("bathroom>=1-if-shower", "fail", "the detector counts 1 shower(s)", "abstain", 1)
    monkeypatch.setattr(checks, "run", lambda f, o, v: [c])
    a = Answer(value=0, unit="count", route="raster-ocr-labels", confidence=0.7, tier="proven",
               explanation="0 room label(s) read as bathroom")
    a = checks.apply(NS(file=Path("x.png")), NS(measure="count", obj="bathroom"), a)
    assert a.value == 1 and a.explanation.startswith("1 = implied by check bathroom>=1-if-shower")
    assert "alone gave 0" in a.explanation and a.tier == "estimate"


def test_scale_from_a_printed_ratio(tmp_path):
    import pymupdf
    from bpqa.routes import sheet_scale
    pdf = tmp_path / "plan.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=1190.55, height=841.89)          # A3 landscape
    page.insert_text((900, 800), "Massstab 1:50", fontsize=9)
    page.draw_line((100, 100), (600, 100))
    doc.save(str(pdf))
    ctx = NS(q=NS(ext="pdf", path="plan.pdf", question="What is the scale?"), file=pdf, evidence=False)
    a = sheet_scale.answer(ctx, parse("What is the scale of this drawing?"))
    assert a.value == "1:50" and a.tier is None
    assert sheet_scale._nearest_common(99.2) == 100 and sheet_scale._nearest_common(87) is None


# ---------------------------------------------------------------- the QA questions on the real files

def ask(path, question):
    from bpqa.data import Question
    from bpqa.router import Context, solve
    q = Question(qid=-1, path=path, question=question)
    return solve(Context(root=FULL, q=q, out_dir=REPO / "out" / "evidence", evidence=False, nemotron=False))[1]


@data
def test_qa_swiss_bedrooms_from_cad():
    a = ask("data/swiss_dwellings/site3387_bldg5238_floor23498.pdf", "How many bedrooms?")
    assert (a.value, a.route, a.tier) == (5, "dxf-geometry", "proven")


@data
def test_qa_french_generic_rooms_answered_honestly():
    for q in ("How many bedrooms?", "What is the total area of the bedrooms?"):
        a = ask("data/swiss_dwellings/site254_bldg483_floor1927.pdf", q)
        assert not a.answered and a.final and "Pièce" in a.abstain


@data
def test_qa_ifc_toilets_on_the_sheet_storey():
    a = ask("data/ifc_models/bsc-esplanades_1807-ep-ar-v18_plan-01-1-korrus.pdf", "How many toilets are on this floor?")
    assert (a.value, a.route) == (13, "ifc")


@data
def test_qa_net_indoor_total_with_unnamed_rooms():
    a = ask("data/swiss_dwellings/site1000_bldg2082_floor9385.pdf",
            "Wie gross ist die gesamte Nettogeschossfläche in m² aller nummerierten Innenräume dieses Geschosses "
            "(ohne Balkone, Loggien, Terrassen und andere Aussenräume, Schächte und Lufträume)?")
    assert abs(a.value - 341.45) <= 0.02 * 341.45


@data
def test_qa_scale_read_off_the_sheet():
    a = ask("data/swiss_dwellings/site254_bldg483_floor1927.pdf", "What is the scale of this drawing?")
    assert (a.value, a.route, a.tier) == ("1:100", "sheet-scale", "proven")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
