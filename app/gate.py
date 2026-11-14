"""Who may solve now: the server's replacement for one global solve lock.

Two lanes, by the drawing's file type: "raster" (PNG, JPEG, TIFF scans: detector, OCR, room model) and "vector" (PDF,
DXF, IFC, SVG). One solve at a time per lane, so a 1 s question on a PDF no longer waits behind a 20 s first reading of
a scan (QA 14 Nov: switching drawing while a scan answered left the next answer stuck). Within a lane, a person's
question goes before background work (witnesses, the upload precompute), and witnesses take the lane one format at a
time, so a question waits for at most one witness solve. A request whose browser has gone away while it waited for
the lane is dropped (`Cancelled`) instead of solving for nobody.

The routes keep module-level caches; two lanes share them only through dict reads and writes and lazily loaded
models. The YOLO models are the one piece that is not thread-safe, and bpqa/yolo_env.py serialises their predict.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager

RASTER_EXT = {"png", "jpg", "jpeg", "tif", "tiff"}


class Cancelled(Exception):
    """The request was abandoned while it waited for its lane."""


def lane_for(rel: str) -> str:
    return "raster" if rel.rsplit(".", 1)[-1].lower() in RASTER_EXT else "vector"


class SolveGate:
    def __init__(self):
        self._cv = threading.Condition()
        self._busy: set[str] = set()
        self._fg: dict[str, int] = {}          # interactive requests waiting, per lane

    def acquire(self, lane: str, background: bool = False, cancelled=None) -> None:
        with self._cv:
            if not background:
                self._fg[lane] = self._fg.get(lane, 0) + 1
            try:
                while lane in self._busy or (background and self._fg.get(lane, 0) > 0):
                    if cancelled is not None and cancelled():
                        raise Cancelled()
                    self._cv.wait(0.25 if cancelled is not None else None)
                self._busy.add(lane)
            finally:
                if not background:
                    self._fg[lane] -= 1
                    self._cv.notify_all()      # background work held back by this request may go now

    def release(self, lane: str) -> None:
        with self._cv:
            self._busy.discard(lane)
            self._cv.notify_all()

    @contextmanager
    def hold(self, lane: str, background: bool = False, cancelled=None):
        self.acquire(lane, background, cancelled)
        try:
            yield
        finally:
            self.release(lane)

    def background(self, lane: str) -> "_Held":
        """A reusable `with` object for code that expects a lock (app/precompute.py)."""
        return _Held(self, lane)


class _Held:
    def __init__(self, gate: SolveGate, lane: str):
        self.gate, self.lane = gate, lane

    def __enter__(self):
        self.gate.acquire(self.lane, background=True)
        return self

    def __exit__(self, *exc):
        self.gate.release(self.lane)
        return False


# ---------------------------------------------------------------- witnesses one format at a time
_TL = threading.local()


def gate_witness_solves(wit_module, gate: SolveGate) -> None:
    """bpqa.witnesses.run solves every format in one loop. Its `solve` is wrapped once so that, in a thread that set
    `hold_for_witnesses(...)`, each format's solve takes the lane of that format's file (background priority).
    Other threads call straight through."""
    real = wit_module.solve
    if getattr(real, "_gated", False):
        return

    def solve(ctx, *a, **k):
        cancelled = getattr(_TL, "cancelled", None)
        if getattr(_TL, "active", False):
            with gate.hold(lane_for(ctx.q.path), background=True, cancelled=cancelled):
                return real(ctx, *a, **k)
        return real(ctx, *a, **k)

    solve._gated = True
    wit_module.solve = solve


@contextmanager
def hold_for_witnesses(cancelled=None):
    _TL.active, _TL.cancelled = True, cancelled
    try:
        yield
    finally:
        _TL.active, _TL.cancelled = False, None


# ---------------------------------------------------------------- YOLO predict
_PREDICT = threading.RLock()


def serialise_yolo() -> None:
    """Ultralytics models are not safe to predict with from two threads at once (one predictor per model). The two
    lanes and the upload precompute may each reach a model, so every predict takes one lock. No-op without
    ultralytics."""
    try:
        from bpqa.yolo_env import yolo
        yolo()                                   # offline settings before the import below
        from ultralytics.engine.model import Model
    except Exception:
        return
    if getattr(Model.predict, "_locked", False):
        return
    real = Model.predict

    def predict(self, *a, **k):
        # device=None makes Ultralytics use the device saved in the checkpoint (a GPU); with CUDA hidden that fails
        # with "Invalid device id" unless something predicted on the CPU first (deploy/web.py prewarm does)
        if k.get("device") is None and os.environ.get("CUDA_VISIBLE_DEVICES") == "":
            k["device"] = "cpu"
        with _PREDICT:
            return real(self, *a, **k)

    predict._locked = True
    Model.predict = predict
