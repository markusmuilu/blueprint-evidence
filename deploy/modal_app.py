"""Modal deployment of the Blueprint Evidence demo app (CPU only).

    python deploy/build_demo_set.py              # once, or when the demo set changes
    python -m modal deploy deploy/modal_app.py   # deploy or redeploy; prints the public URL
    BPE_WARM=1 python -m modal deploy deploy/modal_app.py   # same, with one container kept warm

Image layers, cheapest to change last: Debian slim + Python 3.11, CPU torch, the pinned requirements
(these rebuild only when requirements.txt changes), then the repo code, the detector weights, the demo
set and the eval results, added at container start, so a code-only redeploy does not rebuild the image.

Nemotron: the Modal Secret `nvidia` (key NVIDIA_API_KEY) is attached when it exists. Without it the app
deploys and runs with the Nemotron fallback off. See deploy/README.md for the one command that creates it.
"""
from __future__ import annotations

import os
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REMOTE = "/root/stoa"

APP_NAME = os.environ.get("BPE_APP_NAME", "blueprint-evidence")
WARM = os.environ.get("BPE_WARM", "0").lower() in ("1", "true", "yes", "on")

IGNORE = ["**/__pycache__", "**/*.pyc", "**/.pytest_cache"]

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1", "libglib2.0-0")          # opencv (pulled in by rapidocr) needs these
    .pip_install("torch==2.11.0", "torchvision==0.26.0", index_url="https://download.pytorch.org/whl/cpu")
    .pip_install_from_requirements(str(REPO / "requirements.txt"))
    .pip_install_from_requirements(str(REPO / "app" / "requirements.txt"))
    .pip_install("requests")
    .env({
        "PYTHONPATH": REMOTE,
        "CUDA_VISIBLE_DEVICES": "",
        "YOLO_OFFLINE": "1", "YOLO_AUTOINSTALL": "false", "YOLO_VERBOSE": "false",
        "YOLO_CONFIG_DIR": "/tmp/ultralytics",
        "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4",
        "STOA_DATA": "/root/demo_data",
        "BPE_MAX_UPLOAD_MB": "30",
        "BPE_UPLOAD_TTL": "900",
    })
    .add_local_dir(str(REPO / "bpqa"), f"{REMOTE}/bpqa", ignore=IGNORE)
    .add_local_dir(str(REPO / "app"), f"{REMOTE}/app", ignore=IGNORE)
    .add_local_dir(str(REPO / "tools"), f"{REMOTE}/tools", ignore=IGNORE)   # results.py loads tools/unseen_report.py
    .add_local_file(str(HERE / "web.py"), f"{REMOTE}/deploy/web.py")
    .add_local_dir(str(REPO / "models"), f"{REMOTE}/models")
    .add_local_dir(str(HERE / "demo_data"), "/root/demo_data")
    .add_local_dir(str(HERE / "demo_results"), f"{REMOTE}/out")
)


def nvidia_secret() -> list:
    """[Secret('nvidia')] if it exists in the workspace, else [] (Nemotron off). Checked at deploy time."""
    if not modal.is_local():
        return []
    try:
        s = modal.Secret.from_name("nvidia", required_keys=["NVIDIA_API_KEY"])
        s.hydrate()
        return [s]
    except Exception as ex:     # NotFoundError, or the key is missing
        print(f"[deploy] Modal Secret 'nvidia' not found or incomplete ({type(ex).__name__}); Nemotron fallback off")
        return []


app = modal.App(APP_NAME)


@app.function(
    image=image,
    cpu=2.0,                    # 2 physical cores = 4 vCPU; billed on use above the request
    memory=4096,                # MiB request; torch + detector + a large IFC fit with room to spare
    min_containers=1 if WARM else 0,
    max_containers=1,           # uploads live in the container's temp folder, so one container serves all
    scaledown_window=300 if WARM else 120,
    timeout=600,
    secrets=nvidia_secret(),
)
@modal.concurrent(max_inputs=8)  # previews and static files keep flowing while one solve runs (solves are serialised)
@modal.asgi_app(label=APP_NAME)
def web():
    import sys
    sys.path.insert(0, REMOTE)
    os.chdir(REMOTE)
    from deploy.web import asgi
    return asgi
