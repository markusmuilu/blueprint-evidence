"""app/gate.py: lanes run side by side, a question goes before background work, an abandoned wait is dropped."""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.gate import Cancelled, SolveGate, lane_for  # noqa: E402


def test_lane_for():
    assert lane_for("data/cubicasa5k/a.png") == "raster"
    assert lane_for("data/x/plan.JPG") == "raster"
    assert lane_for("data/swiss/plan.pdf") == "vector"
    assert lane_for("data/ifc/m.ifc") == "vector"


def test_lanes_are_independent():
    g = SolveGate()
    g.acquire("raster")
    got = threading.Event()
    t = threading.Thread(target=lambda: (g.acquire("vector"), got.set()))
    t.start()
    assert got.wait(1.0), "a vector solve waited for a raster one"
    g.release("vector")
    g.release("raster")


def test_question_before_background():
    g = SolveGate()
    g.acquire("raster")
    order = []

    def bg():
        with g.hold("raster", background=True):
            order.append("bg")

    def fg():
        with g.hold("raster"):
            order.append("fg")

    tb = threading.Thread(target=bg); tb.start()
    time.sleep(0.1)
    tf = threading.Thread(target=fg); tf.start()
    time.sleep(0.1)
    g.release("raster")
    tb.join(2); tf.join(2)
    assert order == ["fg", "bg"], order


def test_abandoned_wait_is_dropped():
    g = SolveGate()
    g.acquire("raster")
    gone = threading.Event()
    res = []

    def waiter():
        try:
            g.acquire("raster", cancelled=gone.is_set)
            res.append("got")
        except Cancelled:
            res.append("cancelled")

    t = threading.Thread(target=waiter); t.start()
    time.sleep(0.1)
    gone.set()
    t.join(2)
    assert res == ["cancelled"], res
    # the dropped waiter left no count behind: background work still gets the lane once it is free
    g.release("raster")
    done = threading.Event()
    threading.Thread(target=lambda: (g.acquire("raster", background=True), done.set())).start()
    assert done.wait(1.0)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
