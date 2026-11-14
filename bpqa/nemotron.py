"""Nemotron client (NVIDIA Build, OpenAI-compatible chat completions) with a disk cache and a throttle.

The API key is read from the environment or from `.env` (git-ignored) and is never printed, logged or
written to the cache. Every response is cached under out/nemo_cache/ keyed by
(model, prompt, image hash, max_tokens), so a repeated run costs nothing and is deterministic.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import threading
import time
from pathlib import Path

import requests
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
BASE_URL = "https://integrate.api.nvidia.com/v1"
MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"
CACHE_DIR = REPO / "out" / "nemo_cache"
MIN_INTERVAL = float(os.environ.get("NEMO_MIN_INTERVAL", "1.6"))   # about 37 requests per minute
MAX_SIDE = int(os.environ.get("NEMO_MAX_SIDE", "1600"))
MAX_TOKENS = 4096

_lock = threading.Lock()
_last = [0.0]


def enabled() -> bool:
    """Fallback routes run only when NEMO_FALLBACK is not 0/off and a key exists."""
    return os.environ.get("NEMO_FALLBACK", "1").lower() not in ("0", "off", "false", "no") and bool(_key())


def _key() -> str | None:
    k = os.environ.get("NVIDIA_API_KEY")
    if k:
        return k.strip()
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("NVIDIA_API_KEY="):
                return line.split("=", 1)[1].strip() or None
    return None


def prepare_image(img: Image.Image, max_side: int = MAX_SIDE) -> tuple[bytes, tuple[int, int], tuple[int, int]]:
    """Downscale to max_side on the long edge, return (PNG bytes, (w, h) sent, (w, h) original)."""
    img = img.convert("RGB")
    orig = img.size
    if max(orig) > max_side:
        s = max_side / max(orig)
        img = img.resize((max(1, round(orig[0] * s)), max(1, round(orig[1] * s))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False)
    return buf.getvalue(), img.size, orig


def _cache_key(model: str, prompt: str, img_hash: str, max_tokens: int, system: str, think: bool = True) -> str:
    parts = [model, system, prompt, img_hash, max_tokens] + ([] if think else ["nothink"])
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def chat(prompt: str, png: bytes | None = None, system: str = "", max_tokens: int = MAX_TOKENS,
         model: str = MODEL, cache: bool = True, think: bool = True) -> dict:
    """One request. Returns {content, prompt_tokens, completion_tokens, latency_s, cached, error}.

    `latency_s` is the wall time of the original (uncached) call; a cache hit returns it unchanged.
    """
    img_hash = hashlib.sha256(png).hexdigest() if png else ""
    ck = _cache_key(model, prompt, img_hash, max_tokens, system, think)
    path = CACHE_DIR / f"{ck}.json"
    if cache and path.exists():
        r = json.loads(path.read_text(encoding="utf-8"))
        r["cached"] = True
        return r
    key = _key()
    if not key:
        return {"content": None, "error": "no NVIDIA_API_KEY", "cached": False}
    content: list | str = prompt
    if png:
        b64 = base64.b64encode(png).decode()
        content = [{"type": "text", "text": prompt},
                   {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
    body = {"model": model, "messages": msgs, "max_tokens": max_tokens, "temperature": 0}
    if not think:                                     # no reasoning phase: short, cheap, fine for parsing tasks
        body["chat_template_kwargs"] = {"enable_thinking": False}
    err = None
    for attempt in range(6):
        with _lock:                                  # throttle: one request start per MIN_INTERVAL
            wait = _last[0] + MIN_INTERVAL - time.time()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        t0 = time.time()
        try:
            resp = requests.post(f"{BASE_URL}/chat/completions", json=body, timeout=300,
                                 headers={"Authorization": f"Bearer {key}"})
        except requests.RequestException as ex:
            err = f"{type(ex).__name__}"
            time.sleep(min(60, 4 * 2 ** attempt))
            continue
        dt = time.time() - t0
        if resp.status_code == 200:
            d = resp.json()
            msg = d["choices"][0]["message"]
            u = d.get("usage", {})
            r = {"content": msg.get("content"), "prompt_tokens": u.get("prompt_tokens"),
                 "completion_tokens": u.get("completion_tokens"),
                 "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                 "finish_reason": d["choices"][0].get("finish_reason"), "latency_s": round(dt, 2),
                 "model": model, "error": None}
            if cache:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(r), encoding="utf-8")
            r["cached"] = False
            return r
        err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        if resp.status_code in (429, 500, 502, 503, 504):
            ra = resp.headers.get("Retry-After")
            time.sleep(float(ra) if ra and ra.replace('.', '').isdigit() else min(90, 5 * 2 ** attempt))
            continue
        break                                         # 4xx other than 429: do not retry
    return {"content": None, "error": err, "cached": False}


def parse_number(text: str | None) -> float | None:
    """Last number in a short reply; handles '12', '12.5 m2', '1,234.5', '12,5'."""
    import re
    if not text:
        return None
    t = text.strip().replace("−", "-")
    m = re.findall(r"-?\d[\d\s,.']*\d|-?\d", t)
    if not m:
        return None
    s = m[0].replace(" ", "").replace("'", "")
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if len(s.split(",")[-1]) != 3 or s.count(",") == 1 and len(s.split(",")[0]) == 0 else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None
