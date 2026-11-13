"""Compute fixture candidates and their features for every CubiCasa image -> out/fixtures/<stem>.npz.
Features only; no answers are read here."""
import sys, os, glob
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2, numpy as np
from bpqa.data import data_root
from bpqa.routes.fixtures import candidates, features
out = Path(__file__).resolve().parents[1] / "out" / "fixtures"
out.mkdir(parents=True, exist_ok=True)
shard, n = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (0, 1)
files = sorted((data_root() / "data" / "cubicasa5k").glob("*.png"))[shard::n]
for f in files:
    p = out / (f.stem + ".npz")
    if p.exists(): continue
    im = cv2.imread(str(f))
    cs = candidates(im)
    X = features(im, cs) if cs else np.zeros((0, 8, 655), np.float32)
    np.savez_compressed(p, X=X.astype(np.float16), boxes=np.array([c["xywh"] for c in cs], np.int32).reshape(-1, 4))
    print(f.name, len(cs), flush=True)
