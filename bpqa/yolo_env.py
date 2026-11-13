"""Import Ultralytics offline: no telemetry, no pip auto-install, no weight or font downloads.

Import `yolo()` from here instead of importing ultralytics directly.
"""
from __future__ import annotations

import os


def yolo():
    os.environ.setdefault("YOLO_OFFLINE", "1")
    os.environ.setdefault("YOLO_AUTOINSTALL", "false")
    os.environ.setdefault("YOLO_VERBOSE", "false")
    from ultralytics import YOLO
    from ultralytics.utils import SETTINGS
    if SETTINGS.get("sync"):
        SETTINGS.update({"sync": False})
    return YOLO
