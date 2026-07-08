from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def write_image(path: Path, image: np.ndarray) -> bool:
    """Write an image to paths that may contain non-ASCII characters."""
    suffix = path.suffix or ".png"
    ok, encoded = cv2.imencode(suffix, image)
    if not ok:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded.tofile(str(path))
    return True
