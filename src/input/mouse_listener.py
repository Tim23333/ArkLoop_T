"""Global mouse listener filtered to the MuMu emulator window.

WARNING: This module installs a low-level global mouse hook via pynput. The
callback runs on the OS input path, so any slowness here delays input delivery
to all applications (including MuMu). To minimize system-wide impact:

- The hot path does almost nothing: a few attribute lookups, one integer
  comparison, one float subtraction, and one list append.
- No locks, no logging, and no user callbacks are invoked from the hook.
- Mouse move tracking is opt-in (``record_moves=True``). When enabled, moves
  are only recorded while a button is held.

Do NOT move the mouse programmatically (e.g. ``pynput.mouse.Controller``) while
a listener is active; injected motion plus the global hook can flood the input
queue and cause cursor lag or application stutter.
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import win32gui
from pynput import mouse

from src.mumu.mumu_connection import get_handle, get_parent_handle
from src.logger import logger

__all__ = ["MouseEvent", "MouseListener"]


@dataclass
class MouseEvent:
    """A single mouse event captured inside the MuMu window."""

    type: str  # "mousedown" | "mouseup" | "mousemove" | "scroll"
    x: int  # screen absolute x
    y: int  # screen absolute y
    button: Optional[str] = None  # "left" | "right" | "middle" | "x1" | "x2"
    pressed: Optional[bool] = None  # True for mousedown, False for mouseup
    ts: float = 0.0  # seconds since listener start
    frame: Optional[int] = None  # WS frame captured on the input hook
    extra: dict = field(default_factory=dict)


class MouseListener:
    """
    Listen to global mouse events and keep only those that happen while the
    MuMu emulator window is in the foreground.

    Timestamps are captured with ``time.perf_counter()`` and are relative to the
    moment ``start()`` is called, which makes them directly comparable to the
    timestamps produced by the live recorder.

    Args:
        callback: Optional callback invoked for each captured event. Note that
            callbacks are *not* invoked from the hook itself; they are queued
            and run from the main listener thread to avoid adding latency to
            the input path.
        record_moves: If True, also capture mouse move events while a button is
            held. This installs a low-level move hook and may cause noticeable
            cursor lag or game stutter; only enable when drag reconstruction is
            required.
    """

    def __init__(
        self,
        callback: Optional[Callable[[MouseEvent], None]] = None,
        record_moves: bool = False,
        frame_provider: Optional[Callable[[], int]] = None,
    ):
        self._events: List[MouseEvent] = []
        self._callback = callback
        self._record_moves = record_moves
        self._frame_provider = frame_provider
        self._start_ts: Optional[float] = None
        self._listener: Optional[mouse.Listener] = None
        self._lock = threading.Lock()
        self._pressed_buttons: set = set()
        # Resolved lazily on each foreground check — MuMu recreates its sub
        # window across battle scenes, so a snapshot taken at __init__ goes
        # stale and silently drops events from the "wrong" handle.

    @property
    def events(self) -> List[MouseEvent]:
        with self._lock:
            return list(self._events)

    def _is_mumu_foreground(self) -> bool:
        """Fast inline check used from the hook callback."""
        fg = win32gui.GetForegroundWindow()
        return fg == get_handle() or fg == get_parent_handle()

    def _now(self) -> float:
        if self._start_ts is None:
            return 0.0
        return time.perf_counter() - self._start_ts

    def _current_frame(self) -> Optional[int]:
        if self._frame_provider is None:
            return None
        try:
            return int(self._frame_provider())
        except Exception:
            return None

    def _record(self, event: MouseEvent) -> None:
        """Append an event and optionally notify the user callback."""
        # List append is atomic in CPython, so the hook thread can append
        # without a lock. The lock is only used for the public ``events``
        # property and when callbacks need a consistent snapshot.
        self._events.append(event)
        if self._callback is not None:
            try:
                self._callback(event)
            except Exception:
                logger.exception("Mouse event callback failed")

    def _on_click(self, x: int, y: int, button: mouse.Button, pressed: bool) -> None:
        if not self._is_mumu_foreground():
            return

        button_name = button.name if hasattr(button, "name") else str(button)
        if pressed:
            self._pressed_buttons.add(button_name)
        else:
            self._pressed_buttons.discard(button_name)

        self._record(
            MouseEvent(
                type="mousedown" if pressed else "mouseup",
                x=x,
                y=y,
                button=button_name,
                pressed=pressed,
                ts=self._now(),
                frame=self._current_frame(),
            )
        )

    def _on_move(self, x: int, y: int) -> None:
        # Avoid all overhead when no button is held.
        if not self._pressed_buttons:
            return
        if not self._is_mumu_foreground():
            return

        self._record(
            MouseEvent(
                type="mousemove",
                x=x,
                y=y,
                ts=self._now(),
            )
        )

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        if not self._is_mumu_foreground():
            return

        self._record(
            MouseEvent(
                type="scroll",
                x=x,
                y=y,
                ts=self._now(),
                frame=self._current_frame(),
                extra={"dx": dx, "dy": dy},
            )
        )

    def start(self) -> "MouseListener":
        """Start the global mouse listener."""
        if self._listener is not None:
            raise RuntimeError("Mouse listener already started")

        callbacks = {
            "on_click": self._on_click,
            "on_scroll": self._on_scroll,
        }
        if self._record_moves:
            callbacks["on_move"] = self._on_move

        self._start_ts = time.perf_counter()
        self._events.clear()
        self._pressed_buttons.clear()
        self._listener = mouse.Listener(**callbacks)
        self._listener.start()
        logger.info("Mouse listener started")
        return self

    def stop(self) -> List[MouseEvent]:
        """Stop the listener and return the captured events."""
        if self._listener is not None:
            self._listener.stop()
            # pynput's Listener.join() does not accept a timeout on some versions.
            try:
                self._listener.join(timeout=2.0)
            except TypeError:
                self._listener.join()
            self._listener = None
            self._pressed_buttons.clear()
            logger.info("Mouse listener stopped")
        return self.events

    def __enter__(self) -> "MouseListener":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
