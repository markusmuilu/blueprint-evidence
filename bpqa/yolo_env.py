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
    try:   # Ultralytics wraps PIL's Image.open to install a HEIF plugin on an unreadable image; that turned a damaged
        from ultralytics.utils import patches   # upload into ModuleNotFoundError (14 Nov). Mark it done so PIL's own error stands.
        patches._pil_plugins_registered = True
    except Exception:
        pass
    return YOLO
