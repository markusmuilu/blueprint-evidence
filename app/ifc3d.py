"""3D view of an IFC model, per storey, for the app's viewer.

GET /api/ifc3d?path=            meta: storeys, element table per storey, classes; 202 while it builds
GET /api/ifc3d/geom?path=&s=&k= one storey's mesh, k=solid|space: little-endian Float32 positions
                                (x, z, -y in metres, centred on the model) then Uint32 triangle indices,
                                gzip-encoded
GET /api/ifc3d/element?path=&guid=  one element's class, name, storey, type and property sets

Geometry comes from IfcOpenShell's own geometry iterator (the same kernel the IFC route uses for
footprints), built once per model file in a background thread and cached under out/app/ifc3d/ by file
name and size, so a copy of the same model on another machine reuses the cache. Openings are not drawn
(the iterator already cuts them from walls); spaces are kept in their own mesh so rooms can be shown
on demand. Elements without a storey go to a last "Site and unassigned" group.

`python -m app.ifc3d <model.ifc> ...` fills the cache ahead of time.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import multiprocessing
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "out" / "app" / "ifc3d"
CACHE_VERSION = "1"
SKIP = ["IfcOpeningElement", "IfcOpeningStandardCase", "IfcSpace", "IfcVirtualElement", "IfcAnnotation",
        "IfcGrid"]

router = APIRouter()
_jobs: dict[str, dict] = {}            # key -> {"state": building|done|failed, "t0", "error", "stage"}
_jobs_lock = threading.Lock()
_build_lock = threading.Lock()         # one model at a time: geometry is CPU and memory heavy


# ---------------------------------------------------------------- locating the model

def model_for(root: Path, rel: str) -> Path | None:
    """The IFC model behind a drawing reference: the file itself, or the model a plan PDF was cut from."""
    f = root / rel
    if f.suffix.lower() == ".ifc":
        return f
    stem = f.stem.split("_plan-")[0]
    p = f.parent / (stem + ".ifc")
    return p if p.exists() else None


def cache_key(ifc: Path) -> str:
    st = ifc.stat()
    raw = f"{CACHE_VERSION}|{ifc.name}|{st.st_size}"
    return re.sub(r"[^\w\-]", "_", ifc.stem)[:48] + "-" + hashlib.sha1(raw.encode()).hexdigest()[:10]


# ---------------------------------------------------------------- building

def _storey_map(m):
    """element id -> storey index, storeys sorted by elevation; spatial decomposition walked once."""
    import ifcopenshell.util.element as E
    import ifcopenshell.util.unit as UU
    scale = UU.calculate_unit_scale(m)
    storeys = list(m.by_type("IfcBuildingStorey"))

    def elev(s):
        try:
            return float(s.Elevation) * scale if s.Elevation is not None else 0.0
        except (TypeError, ValueError):
            return 0.0
    storeys.sort(key=elev)
    owner = {}
    for i, s in enumerate(storeys):
        for el in E.get_decomposition(s):
            owner.setdefault(el.id(), i)
        owner.setdefault(s.id(), i)
    return storeys, owner, [elev(s) for s in storeys]


def build(ifc: Path, out: Path, progress=None) -> dict:
    import ifcopenshell
    import ifcopenshell.geom

    t0 = time.perf_counter()
    m = ifcopenshell.open(str(ifc))
    t_open = time.perf_counter() - t0
    storeys, owner, elevs = _storey_map(m)
    n_groups = len(storeys) + 1                       # last group: site and unassigned
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    settings.set("weld-vertices", True)

    # per group and kind: lists of (guid, class, name, verts ndarray, faces ndarray)
    groups = {(g, k): [] for g in range(n_groups) for k in ("solid", "space")}
    classes: dict[str, int] = {}
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    n_el = 0
    threads = max(1, min(8, multiprocessing.cpu_count()))

    def run(include=None, exclude=None, kind="solid"):
        nonlocal n_el, lo, hi
        kw = {"include": include} if include else {"exclude": exclude}
        it = ifcopenshell.geom.iterator(settings, m, threads, **kw)
        if not it.initialize():
            return
        while True:
            sh = it.get()
            g = sh.geometry
            v = np.asarray(g.verts, dtype=np.float64).reshape(-1, 3)
            f = np.asarray(g.faces, dtype=np.uint32).reshape(-1, 3)
            if len(v) and len(f):
                el = m.by_id(sh.id)
                grp = owner.get(sh.id, n_groups - 1)
                cls = sh.type
                classes.setdefault(cls, len(classes))
                groups[(grp, kind)].append((sh.guid, cls, (getattr(el, "Name", None) or "")[:80], v, f))
                if kind == "solid":
                    lo = np.minimum(lo, v.min(0))
                    hi = np.maximum(hi, v.max(0))
                n_el += 1
                if progress and n_el % 200 == 0:
                    progress(f"{n_el} elements")
            if not it.next():
                break

    run(exclude=SKIP, kind="solid")
    try:
        run(include=["IfcSpace"], kind="space")
    except Exception:
        pass                                           # rooms are optional in the view
    t_geom = time.perf_counter() - t0 - t_open
    if not np.isfinite(lo).all():
        raise RuntimeError("the geometry kernel produced no shapes for this model")
    centre = (lo + hi) / 2
    centre[2] = lo[2]                                  # ground at y = 0 in the view

    out.mkdir(parents=True, exist_ok=True)
    meta_storeys = []
    for gi in range(n_groups):
        name = (storeys[gi].Name or f"Storey {gi + 1}") if gi < len(storeys) else "Site and unassigned"
        entry = {"i": gi, "name": str(name), "elevation": round(elevs[gi], 3) if gi < len(storeys) else None}
        zlo, zhi = np.inf, -np.inf
        for kind in ("solid", "space"):
            rows = groups[(gi, kind)]
            if not rows:
                entry[kind] = None
                continue
            pos, idx, table = [], [], []
            nv = nt = 0
            for guid, cls, nm, v, f in rows:
                p = v - centre
                if kind == "solid":
                    zlo, zhi = min(zlo, p[:, 2].min()), max(zhi, p[:, 2].max())
                pos.append(np.stack([p[:, 0], p[:, 2], -p[:, 1]], axis=1).astype(np.float32))
                idx.append(f + nv)
                table.append([guid, classes[cls], nm, nv, len(v), nt * 3, len(f) * 3])
                nv += len(v)
                nt += len(f)
            blob = np.concatenate(pos).tobytes() + np.concatenate(idx).astype(np.uint32).tobytes()
            gz = gzip.compress(blob, compresslevel=6)
            (out / f"s{gi}_{kind}.bin.gz").write_bytes(gz)
            entry[kind] = {"nv": nv, "nt": nt, "bytes": len(gz), "elements": table}
        if entry["solid"] is None and entry["space"] is None:
            continue
        entry["z"] = [round(float(zlo), 3), round(float(zhi), 3)] if np.isfinite(zlo) else None
        meta_storeys.append(entry)

    size = (hi - lo)
    meta = {
        "model": ifc.name, "version": CACHE_VERSION,
        "classes": sorted(classes, key=classes.get),
        "storeys": meta_storeys,
        "size_m": [round(float(size[0]), 2), round(float(size[2]), 2), round(float(size[1]), 2)],
        "elements": sum(len(s["solid"]["elements"]) if s["solid"] else 0 for s in meta_storeys),
        "spaces": sum(len(s["space"]["elements"]) if s["space"] else 0 for s in meta_storeys),
        "triangles": sum((s["solid"] or {}).get("nt", 0) + (s["space"] or {}).get("nt", 0) for s in meta_storeys),
        "bytes": sum((s["solid"] or {}).get("bytes", 0) + (s["space"] or {}).get("bytes", 0) for s in meta_storeys),
        "build_s": {"open": round(t_open, 2), "geometry": round(t_geom, 2),
                    "total": round(time.perf_counter() - t0, 2)},
        "threads": threads,
        "kernel": f"IfcOpenShell {ifcopenshell.version}",
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def _start(ifc: Path, key: str, out: Path) -> dict:
    with _jobs_lock:
        job = _jobs.get(str(out))
        if job and job["state"] in ("building", "failed"):
            return job
        job = {"state": "building", "t0": time.time(), "stage": "queued", "error": None}
        _jobs[str(out)] = job

    def work():
        with _build_lock:
            try:
                job["stage"] = "reading the model"
                build(ifc, out, progress=lambda s: job.__setitem__("stage", s))
                job["state"] = "done"
            except Exception as e:                     # the view falls back to the plan
                job["state"] = "failed"
                job["error"] = f"{type(e).__name__}: {e}"[:300]
            finally:
                import gc
                gc.collect()                           # the parsed model (about 0.7 GB for Molio) goes now
    threading.Thread(target=work, daemon=True, name=f"ifc3d-{key}").start()
    return job


# ---------------------------------------------------------------- endpoints

def _locate(path: str) -> tuple[Path, str, Path]:
    """(model, cache key, cache folder). An upload's geometry lives inside its upload folder, so deleting
    the upload (the public demo does after 15 minutes) deletes its geometry too."""
    from . import server                               # late: server imports this module
    root, rel = server.resolve(path)
    ifc = model_for(root, rel)
    if ifc is None:
        raise HTTPException(404, "no IFC model behind this drawing")
    key = cache_key(ifc)
    out = (root / "ifc3d" / key) if path.startswith("upload:") else CACHE / key
    return ifc, key, out


@router.get("/api/ifc3d")
def ifc3d_meta(path: str):
    ifc, key, out = _locate(path)
    meta = out / "meta.json"
    if meta.exists():
        return Response(meta.read_bytes(), media_type="application/json",
                        headers={"Cache-Control": "private, max-age=3600"})
    job = _start(ifc, key, out)
    if job["state"] == "failed":
        return JSONResponse({"state": "failed", "error": job["error"]}, status_code=500)
    return JSONResponse({"state": "building", "stage": job["stage"], "elapsed_s": round(time.time() - job["t0"], 1),
                         "model": ifc.name, "mb": round(ifc.stat().st_size / 1e6, 1)}, status_code=202)


@router.get("/api/ifc3d/geom")
def ifc3d_geom(path: str, s: int, k: str = "solid"):
    if k not in ("solid", "space"):
        raise HTTPException(400, "k is solid or space")
    _, _, out = _locate(path)
    f = out / f"s{int(s)}_{k}.bin.gz"
    if not f.exists():
        raise HTTPException(404, "no geometry for this storey")
    return Response(f.read_bytes(), media_type="application/octet-stream",
                    headers={"Content-Encoding": "gzip", "Cache-Control": "private, max-age=3600"})


def _val(v):
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, (int, str, bool)) or v is None:
        return v
    return str(v)[:120]


@router.get("/api/ifc3d/element")
def ifc3d_element(path: str, guid: str):
    if not re.fullmatch(r"[0-9A-Za-z_$]{22}", guid):
        raise HTTPException(400, "not an IFC GlobalId")
    ifc, _, _ = _locate(path)
    import ifcopenshell.util.element as E
    from bpqa.routes import ifc_route                  # shares the router's loaded-model cache
    M = ifc_route.load(str(ifc))
    try:
        el = M.m.by_guid(guid)
    except RuntimeError:
        raise HTTPException(404, "no element with this GlobalId")
    st = M.storey_of(el)
    typ = E.get_type(el)
    psets = {}
    for name, props in (E.get_psets(el) or {}).items():
        rows = {k: _val(v) for k, v in props.items() if k != "id"}
        if rows:
            psets[name] = dict(list(rows.items())[:24])
        if len(psets) >= 12:
            break
    return {
        "guid": guid, "class": el.is_a(), "name": getattr(el, "Name", None), "tag": getattr(el, "Tag", None),
        "object_type": getattr(el, "ObjectType", None), "type": getattr(typ, "Name", None) if typ else None,
        "storey": getattr(st, "Name", None) if st is not None else None, "step_id": el.id(), "psets": psets,
    }


if __name__ == "__main__":
    for a in sys.argv[1:]:
        p = Path(a)
        k = cache_key(p)
        meta = build(p, CACHE / k)
        print(json.dumps({"model": p.name, "key": k, **{x: meta[x] for x in ("elements", "spaces", "triangles", "bytes", "build_s")}}))
