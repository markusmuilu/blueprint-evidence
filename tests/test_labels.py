"""Room-label matching: Finnish abbreviations unchanged, English and German words, one-error OCR reads.

    python tests/test_labels.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bpqa.routes.labels import exact, find_rooms, fuzzy, fuzzy_reads, is_word, match, parts  # noqa: E402


def tok(text, x, conf=0.95):
    return {"text": text, "conf": conf, "quad": [[x, 0], [x + 60, 0], [x + 60, 20], [x, 20]], "rot": 0, "mirror": False}


def main():
    # Finnish and Swedish: as before, never fuzzy
    for obj, part in [("bedroom", "MH2"), ("kitchen", "K"), ("bathroom", "KPH"), ("sauna", "S"), ("balcony", "PARVEKE"),
                      ("wc_room", "WC"), ("bathroom", "BADRUM")]:
        assert exact(obj, part) and fuzzy(obj, part) is None, (obj, part)
    assert not match("bedroom", "MX") and not match("kitchen", "KX")
    # English and German words, with a room number
    for obj, text in [("bedroom", "Bedroom 2"), ("bedroom", "BED ROOM"), ("kitchen", "Kitchen"), ("kitchen", "Küche"),
                      ("bathroom", "Bathroom"), ("bathroom", "En-suite"), ("bathroom", "Badezimmer"), ("wc_room", "Toilet"),
                      ("balcony", "Balcony"), ("balcony", "Balkon"), ("bedroom", "Schlafzimmer")]:
        assert any(exact(obj, p) for p in parts(text)), (obj, text)
    assert parts("Living Room") == ["LIVINGROOM"] and is_word("Living Room") and is_word("Flur")
    # one OCR error on a long word is a fuzzy read; two errors, or short words, are not
    assert fuzzy("bedroom", "BEDROCM3") == "BEDROOM"
    assert fuzzy("bathroom", "PATHROOM") == "BATHROOM"
    assert fuzzy("bedroom", "BECROM") is None                 # two errors
    assert fuzzy("kitchen", "KICHEN") == "KITCHEN"            # one letter missing
    assert fuzzy("bedroom", "BEDROOM") is None                # exact, not fuzzy
    assert fuzzy("balcony", "BALENCY") is None
    # find_rooms marks fuzzy hits; fuzzy_reads lists them for the zero guard
    toks = [tok("bedrocm3", 0), tok("Bedroom 1", 200), tok("Lounge", 400), tok("Kitchen", 600)]
    hits = find_rooms(toks, "bedroom")
    assert len(hits) == 2 and sorted(bool(h["fuzzy"]) for h in hits) == [False, True]
    assert fuzzy_reads(toks) == [("bedrocm3", "BEDROOM")]
    assert len(find_rooms(toks, "kitchen")) == 1 and not find_rooms(toks, "balcony")
    print("ok")


if __name__ == "__main__":
    main()
