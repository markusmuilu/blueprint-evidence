"""Run RapidOCR on every CubiCasa image in all four orientations and cache the text boxes.

    python tools/ocr_cache.py [shard] [nshards]     # writes out/ocr/<stem>.r<rot>.json
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.data import data_root
from bpqa.routes.ocr import cached, ocr_image

shard, n = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (0, 1)
files = sorted((data_root() / "data" / "cubicasa5k").glob("*.png"))[shard::n]
for i, f in enumerate(files):
    if not cached(f):
        ocr_image(f)
        print(i, f.name, flush=True)
