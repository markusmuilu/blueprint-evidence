"""Train the room-type detector (Ultralytics YOLO) on Modal (CPU container).

    python tools/train_rooms_modal.py pack --ds ../data/detector/ds_rooms          # tar the dataset
    modal run --detach tools/train_rooms_modal.py::train --name rooms_n640 --imgsz 640 --time 1.2
    modal volume get stoa-rooms runs/rooms_n640/weights/best.pt models/rooms.pt    # bring weights back

The dataset (tools/build_rooms_dataset.py: CubiCasa5K plans allowed by tools/overlap.py, the tune plans
held out) is uploaded once to the Modal volume `stoa-rooms` as ds_rooms.tar; the run folder is written
back to the same volume (weights after every epoch, so a run can be read before it ends). Starts from
Ultralytics' pretrained COCO weights (yolo26n.pt, fetched by Ultralytics inside the container).
Modal GPUs need a payment method on the account, which is not added, so training runs on a 40-core
CPU container (about 4 images/s for yolo26n at 640 px); the time budget bounds the cost: at most
--time hours per run (Ultralytics fits the learning-rate schedule to it).
"""
from __future__ import annotations

import sys
import tarfile
from pathlib import Path

import modal

APP = "stoa-rooms-train"
VOL = "stoa-rooms"
CPUS = 40.0
REPO = Path(__file__).resolve().parents[1]
PRETRAINED = REPO.parent / "data" / "detector" / "pretrained"

app = modal.App(APP)
vol = modal.Volume.from_name(VOL, create_if_missing=True)
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1", "libglib2.0-0")
    .pip_install("torch==2.11.0", "torchvision==0.26.0", "ultralytics==8.4.212", "numpy==2.4.6",
                 index_url="https://download.pytorch.org/whl/cpu", extra_index_url="https://pypi.org/simple")
)


@app.function(image=image, cpu=CPUS, memory=32768, volumes={"/vol": vol}, timeout=3 * 3600)
def _train(name: str, weights: str, hours: float, imgsz: int, batch: int, epochs: int):
    import os
    import shutil
    os.environ["YOLO_AUTOINSTALL"] = "false"
    work = Path("/tmp/ds")
    with tarfile.open("/vol/ds_rooms.tar") as t:
        t.extractall(work, filter="data")
    yml = work / "data.yaml"
    yml.write_text(yml.read_text().replace("path: .", f"path: {work}"))
    from ultralytics import YOLO
    model = YOLO(f"/vol/pretrained/{weights}" if Path(f"/vol/pretrained/{weights}").exists() else weights)
    dst = Path("/vol/runs") / name

    def save_epoch(trainer):
        (dst / "weights").mkdir(parents=True, exist_ok=True)
        for f in ("best.pt", "last.pt"):
            if (Path(trainer.save_dir) / "weights" / f).exists():
                shutil.copy(Path(trainer.save_dir) / "weights" / f, dst / "weights" / f)
        if (Path(trainer.save_dir) / "results.csv").exists():
            shutil.copy(Path(trainer.save_dir) / "results.csv", dst / "results.csv")
        vol.commit()

    model.add_callback("on_fit_epoch_end", save_epoch)
    model.train(data=str(work / "data.yaml"), epochs=epochs, imgsz=imgsz, batch=batch, device="cpu", workers=8,
                project="/tmp/runs", name=name, exist_ok=True, plots=False,
                # plans: mirroring is label-preserving; rooms need whole-room context, so little mosaic
                fliplr=0.5, flipud=0.5, degrees=0.0, hsv_h=0.0, hsv_s=0.0, hsv_v=0.2, mosaic=0.3,
                scale=0.3, translate=0.1, close_mosaic=5, patience=30, seed=0, verbose=True,
                **({"time": hours} if hours else {}))
    shutil.copytree(Path("/tmp/runs") / name, dst, dirs_exist_ok=True)
    vol.commit()
    return (dst / "results.csv").read_text() if (dst / "results.csv").exists() else ""


@app.local_entrypoint()
def train(name: str = "rooms_n640", weights: str = "yolo26n.pt", time: float = 1.2, imgsz: int = 640,
          batch: int = 16, epochs: int = 30, upload: bool = False):
    tar = REPO.parent / "data" / "detector" / "ds_rooms.tar"
    if upload:
        with vol.batch_upload(force=True) as b:
            b.put_file(str(tar), "/ds_rooms.tar")
            if (PRETRAINED / weights).exists():
                b.put_file(str(PRETRAINED / weights), f"/pretrained/{weights}")
        print("uploaded", tar, weights)
    print(_train.remote(name, weights, time, imgsz, batch, epochs)[-3000:])


def pack(ds: Path):
    tar = ds.parent / "ds_rooms.tar"
    with tarfile.open(tar, "w") as t:
        for n in ("data.yaml", "train.txt", "val.txt"):
            t.add(ds / n, arcname=n)
        for sub in ("images", "labels"):
            t.add(ds / sub, arcname=sub)
    print(tar, round(tar.stat().st_size / 1e6), "MB")


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "pack" and sys.argv[2] == "--ds":
        pack(Path(sys.argv[3]))
    else:
        print(__doc__)
