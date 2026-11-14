"""Fill the detector cache (out/det/) for a list of CubiCasa images, for every object policy that the
object scale reads (doors, toilets, showers and baths, sinks). Same caches the raster-detector route uses.

    python tools/objscale_detect.py --data <root> --list out/objscale/pool.txt [--gpu]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.data import data_root
from bpqa.routes import detector as D


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    ap.add_argument("--list", required=True)
    ap.add_argument("--gpu", action="store_true")
    a = ap.parse_args()
    root = data_root(a.data)
    names = [n for n in Path(a.list).read_text().split() if n]
    dev = 0 if a.gpu else "cpu"
    t0 = time.time()
    for i, n in enumerate(names):
        f = root / "data" / "cubicasa5k" / n
        for obj in ("door", "toilet", "shower_or_bath"):
            D.views_for(f, D.config(obj), device=dev)
        if i % 10 == 0:
            print(f"{i + 1}/{len(names)} {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
