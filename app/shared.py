"""Optional storage shared between several containers (a Modal Dict named by BPE_SHARED_DICT).

Unset (the default, and always when running locally) every function here is a no-op and the app keeps
everything on local disk. Set, each container still works on its own disk, and the two things another
container may be asked for are published to the Dict and pulled on a miss:
  - an upload (all its files and their mtimes, so the answer cache key is the same everywhere)
  - an evidence PNG with its marks sidecar
  - the background reading of an upload (app/precompute.py): its status, and the cache files each stage leaves
    (OCR, sheet reading, detector tiles), so a container without the job reports the status and does not read
    the drawing again
A Modal Volume was tried first and dropped: its commit/reload gave no read-after-write guarantee between
containers (about 1 in 6 fresh uploads came back 404 from another container under 30 parallel clients).
"""
from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path

NAME = os.environ.get("BPE_SHARED_DICT") or ""
_D = None
SHARE_MAX_BYTES = int(os.environ.get("BPE_SHARE_MAX_MB", "40")) * 1024 * 1024
STATS = {"puts": 0, "gets": 0, "get_miss": 0, "errors": 0}


def enabled() -> bool:
    return bool(NAME)


def _d():
    global _D
    if _D is None:
        import modal
        _D = modal.Dict.from_name(NAME, create_if_missing=True)
    return _D


def _try(fn, tries: int = 3, wait: float = 0.4):
    for i in range(tries):
        try:
            return fn()
        except Exception as ex:
            STATS["errors"] += 1
            if i == tries - 1:
                print(f"[shared] {type(ex).__name__}: {ex}", flush=True)
                raise
            time.sleep(wait)


# ---------------------------------------------------------------- uploads

def publish_upload(uid: str, folder: Path) -> None:
    """folder = <uploads>/<uid>; label.json is written last by uploads.ingest and marks a complete upload."""
    if not enabled():
        return
    files = [p for p in folder.rglob("*") if p.is_file()]
    if sum(p.stat().st_size for p in files) > SHARE_MAX_BYTES:
        # a very large upload (e.g. a 139 MB TIFF) stays on the container that took it: copying it to the shared
        # Dict hung the upload request (QA 14 Nov). Questions on it are served by this container.
        print(f"[shared] upload {uid}: too large to share, kept local", flush=True)
        return
    tree = {}
    for p in files:
        tree[p.relative_to(folder).as_posix()] = (p.read_bytes(), int(p.stat().st_mtime))
    try:
        _try(lambda: _d().put(f"up:{uid}", tree))
        _try(lambda: _d().put(f"upm:{uid}", time.time()))
        STATS["puts"] += 1
    except Exception:
        pass         # logged by _try; the upload still works on this container


def fetch_upload(uid: str, root: Path) -> bool:
    """Copy upload uid from the Dict into root/<uid> (atomically). False when it is not there."""
    if not enabled():
        return False
    dst = root / uid
    for attempt in range(3):
        try:
            tree = _try(lambda: _d().get(f"up:{uid}"))
        except Exception:
            return False
        STATS["gets"] += 1
        if tree:
            tmp = root / f".{uid}.{uuid.uuid4().hex[:6]}"
            try:
                for rel, (data, mtime) in tree.items():
                    f = tmp / rel
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_bytes(data)
                    os.utime(f, (mtime, mtime))
                if not dst.exists():
                    os.replace(tmp, dst)
                return True
            except OSError:
                if (dst / "label.json").exists():
                    return True
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        STATS["get_miss"] += 1
        time.sleep(0.4)
    return False


# ---------------------------------------------------------------- evidence

def publish_evidence(eid: str, png: Path, marks: Path) -> None:
    if not enabled():
        return
    try:
        val = {"png": png.read_bytes(), "marks": marks.read_bytes() if marks.exists() else None}
        _try(lambda: _d().put(f"ev:{eid}", val))
        STATS["puts"] += 1
    except Exception:
        pass


def fetch_evidence(eid: str, png: Path, marks: Path) -> bool:
    if not enabled():
        return False
    for attempt in range(3):
        try:
            val = _try(lambda: _d().get(f"ev:{eid}"))
        except Exception:
            return False
        STATS["gets"] += 1
        if val:
            tmp = png.with_name(f".{png.name}.{uuid.uuid4().hex[:6]}")
            tmp.write_bytes(val["png"])
            if val.get("marks") is not None:
                marks.write_bytes(val["marks"])
            os.replace(tmp, png)
            return True
        STATS["get_miss"] += 1
        time.sleep(0.4)
    return False


# ---------------------------------------------------------------- background reading

OUT = Path(__file__).resolve().parents[1] / "out"
CACHE_DIRS = ("sheet", "ocr", "det", "rooms_det")     # where bpqa writes per-drawing caches, all named by the file stem
_PULLED: dict[str, tuple] = {}                         # stem -> the stage list last pulled by this container


def cache_files(stem: str, out: Path | None = None) -> list[Path]:
    """Every cache file bpqa has written for the drawing whose file stem is `stem` (<stem>.json or <stem>.*.json)."""
    out = out or OUT
    found = []
    for d in CACHE_DIRS:
        base = out / d
        if base.is_dir():
            for p in base.rglob("*.json"):
                if p.name == f"{stem}.json" or p.name.startswith(f"{stem}."):
                    found.append(p)
    return found


def publish_status(path: str, status: dict) -> None:
    if not enabled():
        return
    try:
        _try(lambda: _d().put(f"pre:{path}", {**status, "updated_at": time.time()}), tries=2)
    except Exception:
        pass


def fetch_status(path: str) -> dict | None:
    if not enabled():
        return None
    try:
        return _try(lambda: _d().get(f"pre:{path}"), tries=2)
    except Exception:
        return None


def publish_caches(stem: str, stages: list, out: Path | None = None) -> None:
    """Publish all cache files of one drawing (call as each stage finishes), with the stages they cover."""
    if not enabled():
        return
    out = out or OUT
    try:
        files = {p.relative_to(out).as_posix(): p.read_bytes() for p in cache_files(stem, out)}
        _try(lambda: _d().put(f"pc:{stem}", {"stages": list(stages), "files": files}), tries=2)
        STATS["puts"] += 1
    except Exception:
        pass


def pull_caches(stem: str, out: Path | None = None) -> list[Path] | None:
    """Write the published cache files of `stem` that this container lacks or holds in an older form (the sheet
    reading is rewritten when the rooms stage ends). Returns the files written ([] when nothing was new), None when
    nothing is published. Cheap when nothing new: the bundle is only written when its stage list differs from the
    one this container last pulled."""
    if not enabled():
        return None
    out = out or OUT
    try:
        val = _try(lambda: _d().get(f"pc:{stem}"), tries=2)
    except Exception:
        return None
    STATS["gets"] += 1
    if not val:
        return None
    stages = tuple(val.get("stages") or ())
    if _PULLED.get(stem) == stages:
        return []
    written = []
    for rel, data in val["files"].items():
        f = out / rel
        if f.exists() and f.stat().st_size == len(data):
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f".{f.name}.{uuid.uuid4().hex[:6]}")
        tmp.write_bytes(data)
        os.replace(tmp, f)
        written.append(f)
    _PULLED[stem] = stages
    return written


# ---------------------------------------------------------------- expiry

def purge_expired(ttl: float) -> list[str]:
    """Delete uploads older than ttl seconds, and the evidence made from them. Returns the uids."""
    if not enabled():
        return []
    now = time.time()
    d = _d()
    keys = list(_try(lambda: list(d.keys())))
    gone = []
    for k in keys:
        if k.startswith("upm:"):
            t = _try(lambda: d.get(k))
            if t is not None and now - t > ttl:
                uid = k[4:]
                for kk in (f"up:{uid}", k):
                    try:
                        d.pop(kk)
                    except Exception:
                        pass
                gone.append(uid)
    for uid in gone:
        for k in keys:
            if (k.startswith(("ev:", "pc:")) and f"u{uid}" in k) or k.startswith(f"pre:upload:{uid}/"):
                try:
                    d.pop(k)
                except Exception:
                    pass
    return gone
