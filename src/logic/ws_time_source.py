"""WebSocket time source: a live game-time feed replaces cost-bar detection.

Connects to an external service that reads the game's memory and pushes
``{game_time, frame_count, connected}`` over WebSocket.  ``frame_count`` is the
absolute logical frame since the battle started (monotonic, resets per battle);
it is decomposed into the existing ``GameTime(cycle, tick)`` model via
``TICK_MAX`` so the rest of the timeline engine (axis runner, breakpoints,
cycle-offset, perform_action) is unchanged.

This module owns a process-wide singleton started once at app startup and read
by ``analyze_time.get_game_time()`` in both recording and playback.  The URL is
user-configurable (``config.json`` -> ``time_source.ws_url``); it is NOT
hardcoded.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, Optional, Tuple

from src.logger import logger

try:
    import websocket  # from the `websocket-client` package
except Exception as exc:  # pragma: no cover - optional dependency
    websocket = None  # type: ignore[assignment]
    logger.warning(f"websocket-client unavailable: {exc}; WS time source disabled")

__all__ = ["WSTimeSource", "get_ws_time_source", "DEFAULT_WS_URL"]

DEFAULT_WS_URL = "ws://127.0.0.1:59555"


class WSTimeSource:
    """Process-wide WebSocket time client.

    A single background thread runs ``WebSocketApp.run_forever`` with auto
    reconnect.  The latest parsed message is cached under ``_data_lock``;
    readers (``get_game_time``, ``latest``, ``status``) never block on the
    network.
    """

    def __init__(self, url: str = DEFAULT_WS_URL) -> None:
        self.url = url
        self._frame_count: int = 0
        self._game_time: float = 0.0
        # ``connected`` field from the last message (memory-read OK).
        self._mem_ok: bool = False
        # Whether the WS transport itself is open.
        self._connected: bool = False
        self._ever_received: bool = False
        self._data_lock = threading.Lock()
        self._callback: Optional[Callable[[int, float, bool], None]] = None
        self._ws: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # Timestamp of the last received message (time.monotonic).  Used to
        # implement a grace period: briefly disconnected → keep pushing last
        # known data instead of immediately flipping to "未连接".
        self._last_msg_time: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self, url: Optional[str] = None) -> "WSTimeSource":
        """Start the background connect/reconnect thread.

        ``url`` overrides the configured URL.  Calling ``start`` on an already
        running source with the same URL is a no-op; with a different URL the
        connection is restarted.
        """
        if websocket is None:
            logger.error("Cannot start WSTimeSource: websocket-client not installed")
            return self
        if url:
            self.url = url
        if self._thread is not None and self._thread.is_alive():
            if self.url == getattr(self, "_running_url", None):
                return self
            # URL changed: stop the old loop and reconnect below.
            self.stop()

        self._stop.clear()
        self._running_url = self.url
        self._ws = websocket.WebSocketApp(
            self.url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(f"WSTimeSource connecting to {self.url}")
        return self

    def stop(self) -> None:
        """Stop the background thread and close the socket."""
        self._stop.set()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        with self._data_lock:
            self._connected = False
        logger.info("WSTimeSource stopped")

    def set_callback(self, callback: Optional[Callable[[int, float, bool], None]]) -> None:
        """Register a callback ``(frame_count, game_time, mem_ok)`` fired on every
        new WS message, outside the data lock.  Only one callback at a time."""
        self._callback = callback

    def _run(self) -> None:
        # run_forever handles reconnect internally; the stop_event is checked
        # via the close() call from stop().  Pings are disabled because the
        # server pushes data every ~8 ms — if the server dies, recv() will
        # fail naturally.  Aggressive ping/pong was causing spurious
        # disconnects when the server didn't respond to pings in time.
        while not self._stop.is_set():
            try:
                self._ws.run_forever(ping_interval=0)
            except Exception as exc:
                logger.warning(f"WSTimeSource run_forever exited: {exc}")
            if self._stop.is_set():
                break
            # Brief backoff before reconnect.  Keep it short so the display
            # recovers quickly when the game pauses/resumes (the server may
            # drop the connection during pause).
            self._stop.wait(0.1)

    # ------------------------------------------------------------------
    # WebSocketApp callbacks (run in the background thread)
    # ------------------------------------------------------------------
    def _on_open(self, _ws: Any) -> None:
        with self._data_lock:
            self._connected = True
        logger.info(f"WSTimeSource connected to {self.url}")

    def _on_close(self, _ws: Any, *args: Any) -> None:
        with self._data_lock:
            self._connected = False
        logger.warning("WSTimeSource connection closed")

    def _on_error(self, _ws: Any, exc: Any) -> None:
        with self._data_lock:
            self._connected = False
        logger.warning(f"WSTimeSource error: {exc}")

    def _on_message(self, _ws: Any, message: Any) -> None:
        try:
            data = json.loads(message)
        except (ValueError, TypeError) as exc:
            logger.debug(f"WSTimeSource: ignoring non-JSON message: {exc}")
            return
        if not isinstance(data, dict):
            return
        try:
            frame_count = int(data.get("frame_count", 0) or 0)
        except (TypeError, ValueError):
            frame_count = 0
        try:
            game_time = float(data.get("game_time", 0.0) or 0.0)
        except (TypeError, ValueError):
            game_time = 0.0
        mem_ok = bool(data.get("connected", False))
        with self._data_lock:
            self._frame_count = frame_count
            self._game_time = game_time
            self._mem_ok = mem_ok
            self._ever_received = True
            self._last_msg_time = time.monotonic()
        # NO callback / evaluate_js here — that would block the recv loop and
        # cause buffer accumulation at 125 Hz.  The display thread polls the
        # cache at a fixed rate instead.

    # ------------------------------------------------------------------
    # Reads (thread-safe, non-blocking)
    # ------------------------------------------------------------------
    def latest(self) -> Tuple[int, float, bool]:
        """Return ``(frame_count, game_time, mem_ok)`` from the last message."""
        with self._data_lock:
            return self._frame_count, self._game_time, self._mem_ok

    def is_connected(self) -> bool:
        """True if the WS transport is open AND at least one message arrived."""
        with self._data_lock:
            return self._connected and self._ever_received

    @property
    def ever_received(self) -> bool:
        with self._data_lock:
            return self._ever_received

    def is_fresh(self, max_age: float = 2.0) -> bool:
        """True if a message was received within the last ``max_age`` seconds.

        Used by the display publisher to avoid flipping to "未连接" during
        brief disconnects / reconnects — if data is less than 2 s old it's
        still "live" even if the transport is momentarily down.
        """
        with self._data_lock:
            return self._ever_received and (time.monotonic() - self._last_msg_time) < max_age

    def wait_connected(self, timeout: float = 5.0) -> bool:
        """Block briefly until the first message arrives. Returns True on success."""
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if self.is_connected():
                return True
            self._stop.wait(0.05)
        return self.is_connected()

    def get_game_time(self) -> int:
        """Return the latest absolute ``frame_count`` from the WS feed.

        Returns the last known value when the feed is briefly interrupted so
        callers in bullet-time / frame-stepping loops keep working off a cached
        reading rather than crashing.
        """
        frame_count, _game_time, _mem_ok = self.latest()
        return int(frame_count)

    def status(self) -> Dict[str, Any]:
        """Snapshot for the UI: connection + latest reading + configured URL."""
        frame_count, game_time, mem_ok = self.latest()
        with self._data_lock:
            connected = self._connected
            ever = self._ever_received
        return {
            "connected": connected and ever,
            "transport_connected": connected,
            "mem_ok": mem_ok,
            "url": self.url,
            "frame_count": frame_count,
            "game_time": game_time,
            "ever_received": ever,
        }


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------
_singleton: Optional[WSTimeSource] = None
_singleton_lock = threading.Lock()


def get_ws_time_source() -> WSTimeSource:
    """Return (creating if needed) the process-wide WSTimeSource singleton."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = WSTimeSource(DEFAULT_WS_URL)
        return _singleton
