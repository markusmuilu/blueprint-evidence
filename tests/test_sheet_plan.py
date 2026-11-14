"""Large raster drawing sheets (bpqa/sheet.py, bpqa/routes/sheet_plan.py): python -m pytest tests/test_sheet_plan.py

The fast tests use synthetic labels, tokens and outlines. The end-to-end test on the Stoa sample sheet runs only with
STOA_SLOW=1 (the first reading of a sheet takes about a minute of OCR and wall tracing).
"""
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa import sheet as S  # noqa: E402
from bpqa.routes import sheet_plan as SP  # noqa: E402

NAMES = ["Secondary Classroom", "Secondary Classroom (Severe Needs)", "Group Room", "Group Room", "Toilet", "Toilets",
         "Accessible Toilet", "Lift", "Lift Landing", "Store", "Secure Store", "Servery and Furniture Store",
         "Head’s Office", "Staff Office", "Stair (Ground)", "Multi-Purpose Hall", "Plant Room"]
LABELS = [{"text": t, "lines": [t], "box": [0, 0, 10, 10], "conf": 0.9, "h": 10, "rot": 0} for t in NAMES]


def hits(q):
    ph, hs = SP.match_phrase(q, LABELS)
    return " ".join(ph), sorted(h["text"] for h in hs)


def test_question_words_synonyms():
    assert SP.question_words("How many staircases?")[-1] == "stair"
    assert SP.question_words("How many WCs are there")[2] == "toilet"
    assert SP.question_words("number of class rooms") == ["number", "of", "classroom"]
    assert SP.question_words("disabled toilets")[:2] == ["accessible", "toilet"]
    assert SP.question_words("the head’s office") == ["the", "head", "office"]


def test_match_counts_by_name():
    assert hits("How many classrooms are there?") == ("classroom", ["Secondary Classroom", "Secondary Classroom (Severe Needs)"])
    assert hits("How many group rooms are there?")[1] == ["Group Room", "Group Room"]
    assert hits("How many toilets are there?")[1] == ["Accessible Toilet", "Toilet", "Toilets"]
    assert hits("How many accessible toilets?")[1] == ["Accessible Toilet"]
    assert hits("How many disabled WCs?")[1] == ["Accessible Toilet"]
    # head noun: "store" in "Secure Store" and "Servery and Furniture Store", "lift" only in "Lift"
    assert hits("How many stores?")[1] == ["Secure Store", "Servery and Furniture Store", "Store"]
    assert hits("Is there a lift?")[1] == ["Lift"]
    assert hits("How many staircases are there?")[1] == ["Stair (Ground)"]
    assert hits("What is the area of the head's office?")[1] == ["Head’s Office"]
    assert hits("What is the area of the hall?")[1] == ["Multi-Purpose Hall"]
    assert hits("How many offices?")[1] == ["Head’s Office", "Staff Office"]


def test_generic_and_unknown_words_match_nothing():
    assert hits("How many rooms are there?") == ("", [])
    assert hits("Is there a swimming pool?") == ("", [])


def test_existence_and_count_phrasing():
    for q in ("Is there a lift?", "Are there any classrooms?", "Does the building have a kitchen?",
              "Does this plan show a library?"):
        assert SP.EXIST_RE.search(q) and not SP.COUNT_RE.search(q), q
    for q in ("How many toilets are there?", "What is the number of stores?"):
        assert SP.COUNT_RE.search(q), q


def tok(text, x0, y0, x1, y1, conf=0.95):
    return {"text": text, "conf": conf, "quad": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], "rot": 0}


def test_join_lines_stacks_one_label_but_not_across_a_wall():
    g = np.full((400, 400), 255, np.uint8)
    toks = [tok("Secondary", 100, 100, 190, 118), tok("Classroom", 102, 122, 188, 140)]
    labels = S.join_lines(toks, g)
    assert [l["text"] for l in labels] == ["Secondary Classroom"]
    g[119:121, 50:300] = 0                      # a wall line between the two lines: two rooms
    labels = S.join_lines(toks, g)
    assert sorted(l["text"] for l in labels) == ["Classroom", "Secondary"]


def test_scale_from_printed_ratio_and_sheet_size():
    toks = [tok("1:200", 10, 10, 60, 30), tok("SIZE", 500, 500, 560, 520), tok("A2", 505, 530, 530, 550)]
    r = S.printed_ratio(toks)
    assert r["denom"] == 200
    sh = S.sheet_size(toks, 14000, 9902)
    assert sh["name"] == "A2" and sh["next_to_heading"]
    assert abs(sh["px_per_mm"] * 1000 / r["denom"] - 117.85) < 0.05
    assert S.sheet_size(toks, 14000, 14000) is None          # the image is not A2 shaped: the size is not used


def test_door_box_belongs_to_the_wall_it_sits_in():
    ppm = 100.0
    room = {"contour": [[0, 0], [1000, 0], [1000, 600], [0, 600]]}
    c = np.array(room["contour"], np.float32).reshape(-1, 1, 2)
    near = SP.DOOR_NEAR_M * ppm
    in_wall = [400, -10, 490, 10]               # a 0.9 m door in the top wall
    assert SP.on_outline(c, in_wall, near)
    across = [1000, -100, 1020, -10]            # a door in a wall running away from this room's corner
    assert not SP.on_outline(c, across, near)
    boxes = [{"cls": "door", "conf": 0.9, "xyxy": in_wall}, {"cls": "door", "conf": 0.9, "xyxy": across},
             {"cls": "window", "conf": 0.9, "xyxy": [0, 200, 10, 300]}, {"cls": "door", "conf": 0.1, "xyxy": [0, 200, 10, 290]}]
    ops = SP.door_openings(Path("unused.png"), room, conf=0.4, boxes=boxes, ppm=ppm)
    assert [o["width_m"] for o in ops] == [0.9]
    assert SP.door_openings(Path("unused.png"), {"contour": None}, conf=0.4, boxes=boxes, ppm=ppm) == []


def test_outer_wall_split():
    d = {"scale": {"ppm": 100.0}, "footprint": [[[0, 0], [2000, 0], [2000, 1000], [0, 1000]]]}
    assert SP.outer_wall(d, [500, -20, 600, 20]) is True       # in the outer wall
    assert SP.outer_wall(d, [990, 400, 1010, 500]) is False    # an internal glazed panel
    assert SP.outer_wall({"scale": {"ppm": 100.0}, "footprint": []}, [0, 0, 1, 1]) is None


SAMPLE = Path(__file__).resolve().parents[2] / "data" / "stoa_sample" / \
    "uk_dfe_sen-dfe-xx-00-d-x-0201-special_school_plan_level_00-a-c01_p001.png"


@pytest.mark.skipif(os.environ.get("STOA_SLOW") != "1" or not SAMPLE.exists(), reason="STOA_SLOW=1 and the sample sheet")
def test_stoa_sample_sheet_end_to_end(tmp_path):
    from bpqa.data import Question
    from bpqa.router import Context, solve
    os.environ.setdefault("NEMO_FALLBACK", "0")
    root = SAMPLE.parents[2]
    rel = SAMPLE.relative_to(root).as_posix()

    def ask(q):
        _, a = solve(Context(root=root, q=Question(qid=-1, path=rel, question=q), out_dir=tmp_path, evidence=False))
        return a
    assert ask("What is the scale of the drawing?").value == "1:200"
    assert ask("How many classrooms are there?").value == 8          # checked by eye: 6 + 2 (Severe Needs)
    assert ask("How many group rooms are there?").value == 10        # checked by eye
    assert ask("Is there a lift?").value == "yes"
    hall = ask("What is the area of the hall?").value                # by eye 17.8 m x 13.8 m between the walls
    assert 235 <= hall <= 255
    rooms = SP.rooms(SAMPLE)
    lib = next(r for r in rooms if r["label"] == "Library")
    assert len(lib["openings"]) == 2 and abs(lib["perimeter"] - 22.1) < 1.0   # corridor door, group room door
