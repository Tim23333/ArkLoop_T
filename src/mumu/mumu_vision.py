import os
import sys
import json
from typing import Tuple, Optional

import cv2
import numpy as np
from PIL import Image

from src.config import ImageProcessingConfig as imgconfig
from src.logger import logger
from src.mumu.mumu_connection import get_handle
from src.mumu.capture_controller import BaseCaptureController
from src.mumu.mumu_dll_controller import MuMuPlayerController
from src.mumu.win32_capture import Win32CaptureController

__all__ = ["capture_game_window", "create_capture_controller"]

if getattr(sys, "frozen", False):
    # Frozen onedir: user-writable config.json sits next to the EXE, not
    # inside _internal/ where __file__ points.
    _CONFIG_PATH = os.path.join(os.path.dirname(sys.executable), "config.json")
else:
    _CONFIG_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "config.json",
    )

_controller: Optional[BaseCaptureController] = None
_win32_controller: Optional[Win32CaptureController] = None


def _load_config() -> dict:
    """Load optional user config for capture source."""
    if not os.path.isfile(_CONFIG_PATH):
        return {}
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load config.json: {e}")
        return {}


def create_capture_controller() -> BaseCaptureController:
    """
    Create and return a capture controller.

    Preference order:
      1. MuMu DLL if config.json provides mumu.install_path
      2. Win32 BitBlt fallback otherwise
    """
    config = _load_config()
    capture_type = config.get("capture_type", "auto")

    if capture_type in ("auto", "mumu"):
        mumu_config = config.get("mumu", {})
        install_path = mumu_config.get("install_path")
        instance_index = mumu_config.get("instance_index", 0)

        if install_path:
            try:
                controller = MuMuPlayerController(install_path, instance_index)
                controller.connect()
                logger.info("MuMu DLL capture controller created.")
                return controller
            except Exception as e:
                logger.warning(f"Failed to create MuMu DLL controller: {e}")
                if capture_type == "mumu":
                    raise

    if capture_type in ("auto", "win32"):
        logger.info("Falling back to Win32 BitBlt capture controller.")
        controller = Win32CaptureController(get_handle())
        controller.connect()
        return controller

    raise ValueError(f"Unknown capture_type: {capture_type}")


def capture_game_window(
    ratio: Optional[Tuple[float, float, float, float]] = None,
    color: bool = False,
) -> np.array:
    """
    Take a screenshot of the game window.

    Args:
        ratio: Relative coordinates (left, top, right, bottom). If None, captures the full window.
        color: If True, return a BGR color image; otherwise return a grayscale image.

    Returns:
        np.ndarray: Captured image.
    """
    global _controller, _win32_controller

    if _controller is None:
        _controller = create_capture_controller()

    if isinstance(_controller, MuMuPlayerController):
        pil_img = _controller.capture_frame()
        if color:
            img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        else:
            img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2GRAY)
        if ratio is None:
            return cv2.resize(img, imgconfig.SCREEN_STANDARD_SIZE)
        h, w = img.shape[:2]
        img = img[
            int(h * ratio[1]):int(h * ratio[3]),
            int(w * ratio[0]):int(w * ratio[2]),
        ]
        std_w = int((ratio[2] - ratio[0]) * imgconfig.SCREEN_STANDARD_SIZE[0])
        std_h = int((ratio[3] - ratio[1]) * imgconfig.SCREEN_STANDARD_SIZE[1])
        return cv2.resize(img, (std_w, std_h))

    elif isinstance(_controller, Win32CaptureController):
        # MuMu recreates its render sub-window across scenes; refresh the
        # handle each call so a stale hwnd doesn't make BitBlt grab the wrong
        # (or a dead) window. Falls back to the cached handle if re-resolution
        # fails (e.g. MuMu briefly not found).
        try:
            fresh = get_handle()
            if fresh:
                _controller.hwnd = fresh
        except Exception as exc:
            logger.debug(f"handle refresh failed, using cached hwnd: {exc}")
        if ratio is None:
            return _controller.capture_frame(color=color)
        return _controller.capture_window_area(ratio)

    raise TypeError(f"Unknown controller type: {type(_controller)}")


if __name__ == "__main__":
    from time import time
    from src.config import GameRatioConfig as ratioconfig

    start_time = time()
    img = capture_game_window(ratio=ratioconfig.COST_AREA_RATIO)
    end_time = time()
    logger.info(f"Time taken: {end_time - start_time:.4f} seconds")

    cv2.imshow("Game Window", img)
    cv2.waitKey(0)
    cv2.destroyAllWindows()
