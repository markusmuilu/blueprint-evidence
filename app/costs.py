"""Compute cost per question at stated, ASSUMED cloud prices.

Every local route is measured in wall seconds and CPU seconds (process time, all threads). GPU seconds
are counted only when the symbol detector actually ran on a GPU (the app runs it on the CPU unless
started with --gpu); the solve's wall time is then booked as GPU time, an upper bound.

The prices are assumptions, not quotes. They are written next to every number they produce.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# ASSUMED prices (USD). Round on-demand list prices for one general-purpose cloud vCPU and one
# inference GPU of the L4 / A10G class; change them here and every table follows.
CPU_VCPU_HOUR = 0.05
GPU_HOUR = 1.00
GPU_ROUTES = {"raster-detector"}


def assumptions() -> dict:
    return {"cpu_vcpu_hour_usd": CPU_VCPU_HOUR, "gpu_hour_usd": GPU_HOUR,
            "note": "Assumed on-demand prices: one general-purpose vCPU, one L4/A10G-class GPU. Not quotes."}


def _gpu_used() -> bool:
    if os.environ.get("CUDA_VISIBLE_DEVICES", None) == "":
        return False
    torch = sys.modules.get("torch")
    try:
        return bool(torch is not None and torch.cuda.is_available())
    except Exception:
        return False


def compute_record(wall_s: float, cpu_s: float, route: str | None, model_usd: float = 0.0,
                   gpu_s: float | None = None, parse_usd: float = 0.0, parse_orig_usd: float = 0.0,
                   parse_cached: bool = False) -> dict:
    if gpu_s is None:
        gpu_s = wall_s if route in GPU_ROUTES and _gpu_used() else 0.0
    cpu_usd = cpu_s / 3600 * CPU_VCPU_HOUR
    gpu_usd = gpu_s / 3600 * GPU_HOUR
    # model_usd = the route's own model call (Nemotron estimate / second opinion) + the question reading by the
    # language model; a cached reading is billed 0 here and its original cost is shown beside it
    route_model = model_usd or 0.0
    model_total = route_model + (parse_usd or 0.0)
    usd = cpu_usd + gpu_usd + model_total
    return {"wall_s": round(wall_s, 3), "cpu_s": round(cpu_s, 3), "gpu_s": round(gpu_s, 3),
            "model_usd": round(model_total, 6), "route_model_usd": round(route_model, 6),
            "parse_usd": round(parse_usd or 0.0, 6), "parse_orig_usd": round(parse_orig_usd or 0.0, 6),
            "parse_cached": bool(parse_cached), "usd": usd, "usd_per_1000": round(usd * 1000, 4),
            "assumed": assumptions()}


def cost_table() -> dict | None:
    """out/cost/cost_by_route.json (tools/cost_eval.py), with the Nemotron baseline row from out/baseline_nemotron.json."""
    f = REPO / "out" / "cost" / "cost_by_route.json"
    if not f.exists():
        return None
    data = json.loads(f.read_text(encoding="utf-8"))
    data["baseline"] = baseline_cost()
    return data


def baseline_cost() -> dict | None:
    f = REPO / "out" / "baseline_nemotron.json"
    if not f.exists():
        return None
    b = json.loads(f.read_text(encoding="utf-8"))
    pin, pout = b.get("price_assumption_usd_per_mtok", [3.0, 15.0])
    recs = [r["nemotron"] for r in b.get("records", []) if r.get("nemotron") and r["nemotron"].get("prompt_tokens") is not None]
    if not recs:
        return None
    n = len(recs)
    tin = sum(r["prompt_tokens"] for r in recs) / n
    tout = sum(r["completion_tokens"] or 0 for r in recs) / n
    lat = sorted(r["latency_s"] for r in recs if r.get("latency_s") is not None)
    usd = (tin * pin + tout * pout) / 1e6
    return {"n": n, "prompt_tokens_mean": round(tin, 1), "completion_tokens_mean": round(tout, 1),
            "latency_median_s": lat[len(lat) // 2] if lat else None,
            "price_in_per_mtok": pin, "price_out_per_mtok": pout, "usd_per_q": usd, "usd_per_1000": round(usd * 1000, 2),
            "source": "out/baseline_nemotron.json"}
