"""Game-time reader backed by the WebSocket time source.

``get_game_time()`` reads the latest ``frame_count`` pushed by the external WS
service (``ws_time_source``) and decomposes it into ``GameTime(cycle, tick)``.
The cost-bar OCR / pixel-detection time source is retired: the external service
reads the game's memory directly, which is more accurate and cheaper than
capturing+analyzing a screenshot on every call.

The optional observer is still notified on every read — playback uses it to
stream the live (cycle, tick) to the UI and to check timeline breakpoints.
"""

from __future__ import annotations

from typing import Callable, Optional

from src.logger import logger
from src.logic.game_time import GameTime
from src.logic.ws_time_source import WSTimeSource, get_ws_time_source

# Optional observer notified of every ``get_game_time`` reading.  Playback uses
# this to stream the live (cycle, tick) to the UI at the runner's own read rate
# — accurate even when the game is paused / frame-stepped, because it reflects
# exactly the frames the runner samples.
_game_time_observer: Optional[Callable[[int, int], None]] = None


def set_time_source(ts: Optional[object]) -> None:
    """Deprecated compatibility shim.

    The WS time source is now the sole time provider and is owned as a
    process-wide singleton (started in ``init_app``).  Nothing needs to be
    installed per-session, so this is a no-op kept only so legacy callers
    (``main.py``) do not break.
    """
    _ = ts  # ignored


def get_time_source() -> WSTimeSource:
    """Return the process-wide WS time source singleton."""
    return get_ws_time_source()


def set_game_time_observer(callback: Optional[Callable[[int, int], None]]) -> None:
    """Register (or clear with ``None``) a hook called on each game-time read."""
    global _game_time_observer
    _game_time_observer = callback


def get_game_time() -> GameTime:
    """Return current ``GameTime(cycle, tick)`` from the WS time feed.

    When the feed has never delivered a message the caller gets ``GameTime(0,0)``;
    callers that must gate on a live feed (e.g. ``AxisRunner.run``) should check
    ``get_ws_time_source().is_connected()`` themselves at startup.
    """
    ws = get_ws_time_source()
    gt = ws.get_game_time()

    observer = _game_time_observer
    if observer is not None:
        try:
            observer(int(gt.cycle), int(gt.tick))
        except Exception:
            logger.debug("game-time observer failed", exc_info=True)

    return gt


if __name__ == "__main__":
    import time as _time

    ws = get_ws_time_source()
    ws.start()
    GameTime.set_tick_max(30)
    if not ws.wait_connected(timeout=5):
        logger.error("WS time source not connected; cannot demo get_game_time().")
    else:
        for _ in range(3):
            start = _time.time()
            gt = get_game_time()
            logger.info(f"Game time: {gt} ({(_time.time() - start) * 1000:.1f} ms)")
            _time.sleep(0.5)
    ws.stop()
