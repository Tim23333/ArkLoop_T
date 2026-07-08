"""Capture game frames and push them to a queue for downstream analysis.

This module decouples frame capture from frame analysis so that the detector
thread does not need to call ``capture_game_window`` itself. This avoids
potential DLL multi-threading issues and keeps the analysis pipeline simple.
"""

import threading
import time
from queue import Queue, Empty
from typing import Optional, Tuple

import numpy as np
from PIL import Image

from src.config import RecordingConfig as recconfig
from src.logger import logger
from src.mumu.mumu_vision import capture_game_window

__all__ = ["FrameSource"]


class FrameSource:
    """
    Continuously capture the game window and publish the most recent frame to
    a ``queue.Queue``.

    The queue has ``maxsize=1`` so that slow consumers always see the latest
    frame instead of accumulating stale frames.
    """

    def __init__(
        self,
        fps: float = recconfig.FPS,
        frame_queue: Optional[Queue] = None,
    ):
        self.fps = fps
        self.frame_queue = frame_queue if frame_queue is not None else Queue(maxsize=1)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_frame: Optional[np.ndarray] = None
        self._last_timestamp: float = 0.0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_frame(self, timeout: float = 0.1) -> Optional[np.ndarray]:
        """Return the latest frame from the queue without blocking indefinitely."""
        try:
            return self.frame_queue.get(timeout=timeout)
        except Empty:
            return None

    def latest(self) -> Tuple[Optional[np.ndarray], float]:
        """Return the most recently captured frame and its timestamp.

        Unlike ``get_frame``, this does not remove the frame from the queue,
        so other consumers can still read it.
        """
        return self._last_frame, self._last_timestamp

    def _capture_loop(self) -> None:
        interval = 1.0 / self.fps
        while not self._stop_event.is_set():
            try:
                frame = capture_game_window(ratio=None, color=True)
                if frame is not None:
                    self._last_frame = frame
                    self._last_timestamp = time.perf_counter()
                    # Drop the old frame if the queue is full.
                    if self.frame_queue.full():
                        try:
                            self.frame_queue.get_nowait()
                        except Empty:
                            pass
                    self.frame_queue.put_nowait(frame)
            except Exception as e:
                logger.warning(f"FrameSource capture error: {e}")

            # Sleep until next capture interval, but allow quick shutdown.
            self._stop_event.wait(interval)

    def start(self) -> "FrameSource":
        """Start the capture thread."""
        if self.is_running:
            raise RuntimeError("FrameSource already started")

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info(f"FrameSource started at {self.fps} FPS")
        return self

    def stop(self) -> None:
        """Stop the capture thread."""
        if not self.is_running:
            return

        self._stop_event.set()
        self._thread.join(timeout=2.0)
        if self._thread.is_alive():
            logger.warning("FrameSource thread did not stop within 2 seconds")
        self._thread = None
        logger.info("FrameSource stopped")

    def __enter__(self) -> "FrameSource":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
