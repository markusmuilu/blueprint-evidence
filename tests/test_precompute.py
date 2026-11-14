"""Background reading at upload (app/precompute.py): python -m pytest tests/test_precompute.py"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import precompute as P  # noqa: E402


def test_needed_stage():
    assert P.needed_stage("How many doors are there?") == "doors"
    assert P.needed_stage("How many windows are shown?") == "doors"
    assert P.needed_stage("What is the area of the hall?") == "rooms"
    assert P.needed_stage("What is the skirting length of the library?") == "rooms"
    assert P.needed_stage("How many classrooms are there?") == "text"
    assert P.needed_stage("What is the scale of the drawing?") == "text"


def test_non_raster_gets_no_job(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"%PDF")
    assert P.start("upload:x/a.pdf", f, threading.Lock(), lambda p: None) is None


def _job(path, **kw):
    j = {"state": "running", "kind": "sheet", "stage": "text", "done_stages": [], "stage_done": 3, "stage_total": 10,
         "queued_at": time.time(), "started_at": time.time(), "finished_at": None, "error": None,
         "_lock": None, "_ask": None}
    j.update(kw)
    with P._COND:
        P._JOBS[path] = j


def test_message_names_the_stage_and_what_is_next():
    _job("upload:t/m.png")
    m = P.status("upload:t/m.png")["message"]
    assert m.startswith("Reading the drawing: text 30% now, room outlines and doors and windows next")
    assert "_lock" not in P.status("upload:t/m.png")


def test_question_waits_only_for_the_stage_it_needs():
    path = "upload:t/w.png"
    _job(path)

    def finish_text_then_rest():
        time.sleep(0.3)
        P._set(path, done_stages=["text"], stage="rooms")
        time.sleep(0.6)
        P._set(path, done_stages=list(P.STAGES), state="done")
    th = threading.Thread(target=finish_text_then_rest)
    th.start()
    t0 = time.time()
    P.wait_for(path, "How many classrooms are there?")
    t_text = time.time() - t0
    P.wait_for(path, "How many doors are there?")
    t_doors = time.time() - t0
    th.join()
    assert 0.2 < t_text < 0.8 and t_doors >= 0.8
    assert P.wait_for(path, "How many doors are there?") == 0.0          # done: no wait
    assert P.wait_for("upload:t/none.png", "How many doors?") == 0.0     # no job: no wait


def test_scan_job_waits_for_the_whole_job():
    path = "upload:t/s.png"
    _job(path, kind="scan", stage="scan")
    threading.Timer(0.3, lambda: P._set(path, state="done")).start()
    assert P.wait_for(path, "How many classrooms are there?") >= 0.2
