"""Asynchronous consumer for raw mouse actions.

The worker runs an ``ActionRecognizer`` in a dedicated thread and consumes
``ActionItem`` objects from a queue.  View detection (OCR) is only performed
for click actions; drags are assumed to occur in side view and are processed
without blocking on the view detector.
"""

import threading
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any, Callable, Dict, Optional

import numpy as np

from recorder.action_archive import ActionArchive
from recorder.action_recognizer import ActionRecognizer, SemanticAction
from src.logger import logger

__all__ = ["ActionItem", "ActionWorker"]


@dataclass
class ActionItem:
    """One raw action together with contextual metadata captured at enqueue time."""

    action: Dict[str, Any]
    frame: Optional[np.ndarray] = None
    frame_ts: float = 0.0
    tick_state: Optional[Dict[str, Any]] = None


class ActionWorker:
    """Consume ``ActionItem`` objects from a queue and run ``ActionRecognizer``.

    The worker maintains the recognizer state machine in a single thread, so no
    locks are required.  Callers enqueue actions from the main / producer thread
    and continue immediately; results are emitted through the recognizer's
    ``event_callback``.
    """

    def __init__(
        self,
        map_data: Dict[str, Any],
        avatar_matcher: Any,
        view_detector: Optional[Callable[[np.ndarray], bool]] = None,
        frame_provider: Optional[Callable[[float], Optional[np.ndarray]]] = None,
        event_callback: Optional[Callable[..., None]] = None,
        use_slot_layout: bool = True,
        archive: Optional[ActionArchive] = None,
        semantic_callback: Optional[Callable[[SemanticAction], None]] = None,
    ) -> None:
        self.archive = archive
        self.semantic_callback = semantic_callback
        self.queue: Queue[ActionItem] = Queue()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._current_item: Optional[ActionItem] = None
        self.latest_state: Dict[str, Any] = {}

        # ``ActionRecognizer`` needs a frame_provider for slot-layout avatar
        # matching.  We route it to the frame attached to the current action.
        self.recognizer = ActionRecognizer(
            map_data=map_data,
            avatar_matcher=avatar_matcher,
            frame_provider=self._current_frame_provider,
            event_callback=event_callback,
            view_detector=view_detector,
            use_slot_layout=use_slot_layout,
        )

        # If the caller also supplied a fallback frame provider, we ignore it
        # because the per-action frame is more accurate.  The recognizer's own
        # frame_provider is already bound to ``_current_frame_provider`` above.
        _ = frame_provider  # reserved for future use

    def _current_frame_provider(self, _ts: float) -> Optional[np.ndarray]:
        """Return the frame attached to the action currently being processed."""
        item = self._current_item
        return item.frame if item is not None else None

    def enqueue(self, item: ActionItem) -> None:
        """Add an action to the processing queue."""
        self.queue.put(item)

    def start(self) -> "ActionWorker":
        """Start the consumer thread."""
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("ActionWorker already started")

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("ActionWorker started")
        return self

    def stop(self) -> None:
        """Stop the consumer thread and wait for it to finish."""
        if self._thread is None:
            return

        self._stop_event.set()
        self._thread.join(timeout=2.0)
        if self._thread.is_alive():
            logger.warning("ActionWorker did not stop within 2 seconds")
        self._thread = None
        logger.info("ActionWorker stopped")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self.queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                self._process_item(item)
            except Exception:
                logger.exception("Failed to process action item")

    def _process_item(self, item: ActionItem) -> None:
        self._current_item = item
        try:
            action = item.action

            def _game_time(_action: Dict[str, Any]) -> Dict[str, Any]:
                ts = item.tick_state or {}
                return {
                    "frame": ts.get("frame"),
                    "tick": ts.get("tick"),
                    "cycle": ts.get("cycle"),
                    "total_elapsed_frames": ts.get("total_elapsed_frames"),
                    "timestamp": item.frame_ts,
                }

            # Only run OCR view detection for click actions.  Deployment and
            # direction drags are assumed to be in side view and must not block
            # the pipeline waiting for the view detector.
            if item.frame is not None and action.get("type") == "click":
                self.recognizer.update_view(item.frame)

            semantic = self.recognizer.process_single(action, game_time=_game_time)
        finally:
            self._current_item = None

        if self.semantic_callback is not None and semantic is not None:
            try:
                self.semantic_callback(semantic)
            except Exception:
                logger.exception("semantic_callback failed")

        if self.archive is not None:
            try:
                self.archive.save(
                    action=item.action,
                    frame=item.frame,
                    frame_ts=item.frame_ts,
                    tick_state=item.tick_state,
                    semantic=semantic,
                    final_state=self._state_dict(),
                )
            except Exception:
                logger.exception("Failed to archive action")

        self.latest_state = self._state_dict()

    def _state_dict(self) -> Dict[str, Any]:
        state = self.recognizer.state_dict()
        state["queue_size"] = self.queue.qsize()
        return state
