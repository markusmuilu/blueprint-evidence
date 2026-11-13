"""Train the fixture/door/window detector (Ultralytics YOLO) on a dataset from build_detector_dataset.py.

    python tools/train_detector.py --data ../data/detector/ds/data.yaml --name cubi1 --epochs 60 --imgsz 1280

Starts from scratch (a model yaml) by default, so nothing is downloaded; --weights takes a local
.pt to fine-tune from. Runs offline (bpqa/yolo_env.py). The best weights are copied to
models/detector.pt (models/ is not committed); the run folder is under ../data/detector/runs/.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from bpqa.yolo_env import yolo  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--name", default="det")
    ap.add_argument("--model", default="yolo26n.yaml", help="architecture yaml (from scratch) or a local .pt")
    ap.add_argument("--weights", default="", help="local .pt to start from (optional)")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--device", default="0")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--fraction", type=float, default=1.0, help="use a fraction of the training images")
    ap.add_argument("--project", default=str(REPO.parent / "data" / "detector" / "runs"))
    ap.add_argument("--no-copy", action="store_true", help="do not copy best.pt to models/detector.pt")
    a = ap.parse_args()
    YOLO = yolo()
    model = YOLO(a.weights or a.model)
    model.train(data=a.data, epochs=a.epochs, imgsz=a.imgsz, batch=a.batch, device=a.device, workers=a.workers,
                project=a.project, name=a.name, exist_ok=True, plots=False, fraction=a.fraction,
                # plans: rotations by 90 degrees and mirroring are label-preserving; no colour jitter needed
                fliplr=0.5, flipud=0.5, degrees=0.0, hsv_h=0.0, hsv_s=0.0, hsv_v=0.2, mosaic=0.5,
                scale=0.3, translate=0.1, close_mosaic=5, patience=20, seed=0, verbose=True)
    best = Path(a.project) / a.name / "weights" / "best.pt"
    print("best weights:", best)
    if best.exists() and not a.no_copy:
        (REPO / "models").mkdir(exist_ok=True)
        shutil.copy(best, REPO / "models" / "detector.pt")
        print("copied to", REPO / "models" / "detector.pt")


if __name__ == "__main__":
    main()
