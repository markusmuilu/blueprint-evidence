"""Background reading at upload (app/precompute.py): python -m pytest tests/test_precompute.py"""
import sys
import pytest
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


# ---------------------------------------------------------------- several containers (shared Dict)

class _FakeDict(dict):
    def put(self, k, v):
        self[k] = v


@pytest.fixture
def shared_dict(monkeypatch, tmp_path):
    from app import shared as SH
    d = _FakeDict()
    monkeypatch.setattr(SH, "NAME", "fake")
    monkeypatch.setattr(SH, "_D", d)
    monkeypatch.setattr(SH, "OUT", tmp_path / "out")
    monkeypatch.setattr(SH, "_PULLED", {})
    P._JOBS.clear()
    P._PUB.clear()
    return d, SH, tmp_path / "out"


def test_status_comes_from_the_shared_dict_when_this_container_has_no_job(shared_dict):
    d, SH, out = shared_dict
    _job("upload:abc/u1-m.png", stage="doors", done_stages=["text", "rooms"])
    P._publish("upload:abc/u1-m.png", force=True)
    P._JOBS.clear()                               # another container: no job here
    st = P.status("upload:abc/u1-m.png")
    assert st["state"] == "running" and st["done_stages"] == ["text", "rooms"]
    assert "doors and windows" in st["message"] and "updated_at" not in st
    assert P.status("upload:abc/none.png") is None


def test_dead_container_status_goes_stale(shared_dict):
    d, SH, out = shared_dict
    _job("upload:abc/u1-m.png")
    P._publish("upload:abc/u1-m.png", force=True)
    d["pre:upload:abc/u1-m.png"]["updated_at"] -= P.STALE_S + 1
    P._JOBS.clear()
    assert P.status("upload:abc/u1-m.png")["state"] == "error"


def test_caches_are_published_and_pulled_by_stem(shared_dict):
    d, SH, out = shared_dict
    (out / "sheet").mkdir(parents=True)
    (out / "det" / "m-1024").mkdir(parents=True)
    (out / "sheet" / "u1-m.v3.99.json").write_text("{\"a\": 1}")
    (out / "sheet" / "u1-mx.v3.99.json").write_text("{}")             # another drawing, not published
    (out / "det" / "m-1024" / "u1-m.json").write_text("[1]")
    SH.publish_caches("u1-m", ["text"])
    assert sorted(d["pc:u1-m"]["files"]) == ["det/m-1024/u1-m.json", "sheet/u1-m.v3.99.json"]
    for f in (out / "sheet" / "u1-m.v3.99.json", out / "det" / "m-1024" / "u1-m.json"):
        f.unlink()
    assert len(SH.pull_caches("u1-m")) == 2
    assert (out / "sheet" / "u1-m.v3.99.json").read_text() == "{\"a\": 1}"
    assert SH.pull_caches("u1-m") == []                                # nothing new: no second write


def test_question_waits_for_the_shared_stage_then_pulls(shared_dict, monkeypatch):
    d, SH, out = shared_dict
    monkeypatch.setattr(P, "POLL_S", 0.05)
    path = "upload:abc/u1-m.png"
    _job(path, stage="rooms", done_stages=["text"])
    P._publish(path, force=True)
    P._JOBS.clear()
    (out / "sheet").mkdir(parents=True)

    def finish():
        time.sleep(0.3)
        (out / "sheet" / "u1-m.v3.9.json").write_text("{}")
        SH.publish_caches("u1-m", ["text", "rooms", "doors"])
        d[f"pre:{path}"] = {**d[f"pre:{path}"], "done_stages": ["text", "rooms", "doors"], "state": "done",
                            "updated_at": time.time()}
    threading.Thread(target=finish).start()
    (out / "sheet" / "u1-m.v3.9.json").unlink(missing_ok=True)
    waited = P.wait_for(path, "How many doors are there?", timeout=5)
    assert 0.2 < waited < 3
    assert (out / "sheet" / "u1-m.v3.9.json").exists()                 # pulled before the question is solved
    assert P.wait_for(path, "How many doors are there?", timeout=5) < 0.5   # done: no wait


def test_wait_gives_up_after_the_timeout(shared_dict, monkeypatch):
    d, SH, out = shared_dict
    monkeypatch.setattr(P, "POLL_S", 0.05)
    _job("upload:abc/u1-m.png", done_stages=["text"])
    P._publish("upload:abc/u1-m.png", force=True)
    P._JOBS.clear()
    assert P.wait_for("upload:abc/u1-m.png", "doors?", timeout=0.3) >= 0.3


def test_without_the_dict_nothing_is_published_or_waited(monkeypatch):
    from app import shared as SH
    monkeypatch.setattr(SH, "NAME", "")
    P._JOBS.clear()
    assert P.status("upload:abc/none.png") is None
    assert P.wait_for("upload:abc/none.png", "doors?") == 0.0
    assert SH.pull_caches("x") is None
