"""Game-time reader backed by the WebSocket time source.

``get_game_time()`` returns the current absolute ``frame_count`` from the WS
feed.  The cost-bar cycle/tick model is removed — all timing uses the raw
frame count directly.
"""

from __future__ import annotations

from typing import Callable, Optional

from src.logger import logger
from src.logic.ws_time_source import get_ws_time_source

# Optional observer notified of every ``get_game_time`` reading.  Playback uses
# this to stream the live frame to the UI and to check timeline breakpoints.
_game_time_observer: Optional[Callable[[int], None]] = None


def set_time_source(ts: Optional[object]) -> None:
    """Deprecated no-op.  The WS singleton is the sole time provider."""
    pass


def get_time_source() -> object:
    """Return the process-wide WS time source singleton."""
    return get_ws_time_source()


def set_game_time_observer(callback: Optional[Callable[[int], None]]) -> None:
    """Register (or clear with ``None``) a hook called on each game-time read."""
    global _game_time_observer
    _game_time_observer = callback


def get_game_time() -> int:
    """Return current absolute ``frame_count`` from the WS time feed.

    Returns 0 when the feed has never delivered a message.
    """
    ws = get_ws_time_source()
    frame = ws.get_game_time()

    observer = _game_time_observer
    if observer is not None:
        try:
            observer(int(frame))
        except Exception:
            logger.debug("game-time observer failed", exc_info=True)

    return int(frame)
