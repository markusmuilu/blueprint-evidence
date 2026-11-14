"""Readable drawing names in the library (app/catalog.py friendly_name / friendly_names)."""
from app.catalog import floor_name, friendly_name, friendly_names


def test_known_sources():
    assert friendly_name("data/ifc_models/bsc-esplanades_1807-ep-ar-v18_plan-01-1-korrus.pdf") == "Esplanades apartment block, 1st floor"
    assert friendly_name("data/ifc_models/bsc-molio_molio-with-uris_plan-06-3-sal.pdf") == "Molio office, 3rd floor"
    assert friendly_name("data/ifc_models/kit-fzk-haus_ac20-fzk-haus_plan-01-erdgeschoss.pdf") == "FZK-Haus, ground floor"
    assert friendly_name("data/swiss_dwellings/site3387_bldg5238_floor23498.pdf") == "Swiss apartment floor, site 3387"
    assert friendly_name("data/cubicasa5k/colorful-11196.png") == "Scanned flat plan 11196"
    assert friendly_name("data/ifc_models/bsc-clinic_clinic-architectural.ifc") == "Clinic, architecture (IFC model)"


def test_fallback_tidies_unknown_stems():
    assert friendly_name("data/new_source/some_new-drawing.pdf") == "Some new drawing"
    assert friendly_name("data/ifc_models/acme-tower_x_plan-03-2-og.pdf") == "Tower, 2nd floor"


def test_floor_labels():
    assert floor_name("ug2") == "basement 2"
    assert floor_name("e00-okrd") == "ground floor"
    assert floor_name("12-korrus") == "12th floor"
    assert floor_name("klder") == "basement"


def test_duplicates_get_told_apart():
    a = "data/ifc_models/bimspeed-gdynia_ruchu-oporu-18_plan-02-1.pdf"
    b = "data/ifc_models/bimspeed-gdynia_ruchu-oporu-18_plan-04-1.pdf"
    n = friendly_names([a, b])
    assert n[a] != n[b] and n[a].endswith("sheet 2") and n[b].endswith("sheet 4")
