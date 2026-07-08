"""WebSocket time source for the live game-time feed.

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

Architecture — lock-free hot path:
  The recv thread writes to a shared tuple via an atomic reference swap
  (``_data = (fc, gt, ok)``).  No lock is held during the swap, so the recv
  loop is never blocked by readers.  A ``threading.Event`` (``_data_event``)
  is set on every new message so that ``wait_for_update()`` can sleep until
  fresh data arrives instead of busy-polling.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

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

    Design principles
    -----------------
    * **Lock-free hot path** — the recv thread writes to ``_data`` via an
      atomic reference swap (Python GIL guarantees atomicity for a single
      attribute store).  No lock is ever held in the recv callback.
    * **Event notification** — ``_data_event`` (a ``threading.Event``) is set
      on every new message.  Callers that need to wait for fresh data
      (e.g. the frame-stepping loop in ``perform_action``) can call
      ``wait_for_update(timeout)`` which blocks on the event instead of
      busy-polling with ``time.sleep``.
    * **Snapshot reads** — ``latest()`` and ``get_game_time()`` read the
      ``_data`` reference once (GIL-atomic) and return immediately.
    """

    def __init__(self, url: str = DEFAULT_WS_URL) -> None:
        self.url = url
        # ── lock-free data cache ──────────────────────────────────────
        # Atomic reference swap: the recv thread stores a new tuple;
        # readers load the reference once and unpack.  No lock needed.
        # (frame_count, game_time, mem_ok)
        self._data: Tuple[int, float, bool] = (0, 0.0, False)
        # Signalled on every new WS message.  Cleared by waiters.
        self._data_event = threading.Event()
        # ── connection state (protected by _conn_lock) ────────────────
        self._connected: bool = False
        self._ever_received: bool = False
        self._conn_lock = threading.Lock()
        # Timestamp of the last received message (time.monotonic).
        self._last_msg_time: float = 0.0
        self._time_lock = threading.Lock()
        # ── WS lifecycle ──────────────────────────────────────────────
        self._callback: Optional[Callable[[int, float, bool], None]] = None
        self._ws: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

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
        with self._conn_lock:
            self._connected = False
        logger.info("WSTimeSource stopped")

    def set_callback(self, callback: Optional[Callable[[int, float, bool], None]]) -> None:
        """Register a callback ``(frame_count, game_time, mem_ok)`` fired on every
        new WS message, outside any lock.  Only one callback at a time."""
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
    # WebSocketApp callbacks (run in the background recv thread)
    # ------------------------------------------------------------------
    def _on_open(self, _ws: Any) -> None:
        with self._conn_lock:
            self._connected = True
        logger.info(f"WSTimeSource connected to {self.url}")

    def _on_close(self, _ws: Any, *args: Any) -> None:
        with self._conn_lock:
            self._connected = False
        logger.warning("WSTimeSource connection closed")

    def _on_error(self, _ws: Any, exc: Any) -> None:
        with self._conn_lock:
            self._connected = False
        logger.warning(f"WSTimeSource error: {exc}")

    def _on_message(self, _ws: Any, message: Any) -> None:
        # ── LOCK-FREE HOT PATH ────────────────────────────────────────
        # Parse JSON and store via atomic reference swap.  No lock is held
        # so the recv loop is never blocked by readers.
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
        # Atomic reference swap (GIL-protected).  Readers that load _data
        # before this assignment see the old tuple; readers after see the
        # new one.  No torn reads possible.
        self._data = (frame_count, game_time, mem_ok)
        # Signal waiters that fresh data is available.
        self._data_event.set()
        # Update last-message timestamp (separate lock, non-blocking).
        with self._time_lock:
            self._last_msg_time = time.monotonic()
        if not self._ever_received:
            with self._conn_lock:
                self._ever_received = True
        # NO callback / evaluate_js here — that would block the recv loop
        # and cause buffer accumulation at 125 Hz.

    # ------------------------------------------------------------------
    # Reads (snapshot, non-blocking)
    # ------------------------------------------------------------------
    def latest(self) -> Tuple[int, float, bool]:
        """Return ``(frame_count, game_time, mem_ok)`` from the last message.

        This is a single atomic reference load — no lock, no contention.
        """
        return self._data

    def is_connected(self) -> bool:
        """True if the WS transport is open AND at least one message arrived."""
        with self._conn_lock:
            return self._connected and self._ever_received

    @property
    def ever_received(self) -> bool:
        with self._conn_lock:
            return self._ever_received

    def is_fresh(self, max_age: float = 2.0) -> bool:
        """True if a message was received within the last ``max_age`` seconds.

        Used by the display publisher to avoid flipping to "未连接" during
        brief disconnects / reconnects — if data is less than 2 s old it's
        still "live" even if the transport is momentarily down.
        """
        with self._time_lock:
            return self._ever_received and (time.monotonic() - self._last_msg_time) < max_age

    def wait_connected(self, timeout: float = 5.0) -> bool:
        """Block briefly until the first message arrives. Returns True on success."""
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if self.is_connected():
                return True
            self._stop.wait(0.05)
        return self.is_connected()

    def wait_for_update(self, timeout: float = 0.01) -> bool:
        """Block until a new WS message arrives or *timeout* elapses.

        Returns True if fresh data is available, False on timeout.
        The event is auto-cleared after waking so the next call blocks again.
        Signalled by ``_on_message`` (recv thread) — no polling needed.
        """
        signalled = self._data_event.wait(timeout=timeout)
        if signalled:
            self._data_event.clear()
        return signalled

    def get_game_time(self) -> int:
        """Return the latest absolute ``frame_count`` from the WS feed.

        Returns the last known value when the feed is briefly interrupted so
        callers in bullet-time / frame-stepping loops keep working off a cached
        reading rather than crashing.

        This is a snapshot read — no lock, no contention.
        """
        return int(self._data[0])

    def status(self) -> Dict[str, Any]:
        """Snapshot for the UI: connection + latest reading + configured URL."""
        frame_count, game_time, mem_ok = self._data
        with self._conn_lock:
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
