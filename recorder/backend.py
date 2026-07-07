"""Real-time action recognition backend with live axis generation.

This module encapsulates the frame capture, cost-bar analysis, mouse recording,
semantic recognition and live axis-building logic that was previously bundled in
``scripts/test_action_state_machine.py``.  It is designed to be imported by a
frontend monitor script or run standalone via ``scripts/run_action_backend.py``.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.cache import get_map_by_code
from src.config import DebugConfig
from src.config import GameRatioConfig as ratioconfig
from src.config import ImageProcessingConfig as imgconfig
from src.config import PerformActionConfig as performconfig
from src.input.action_recorder import ActionRecorder
from src.logger import logger
from src.logic.ws_time_source import get_ws_time_source
from recorder.action_archive import ActionArchive
from recorder.action_recognizer import ActionType, AvatarMatcher, DirectionType, SemanticAction
from recorder.action_worker import ActionItem, ActionWorker

try:
    from src.maa import create_side_view_detector
except Exception as exc:  # pragma: no cover - optional dependency
    create_side_view_detector = None  # type: ignore[assignment, misc]
    logger.warning(f"MAA side-view detector unavailable: {exc}")

try:
    from src.frame.frame_source import FrameSource
except Exception as exc:  # pragma: no cover - optional dependency
    FrameSource = None  # type: ignore[assignment, misc]
    logger.warning(f"FrameSource unavailable: {exc}")


__all__ = [
    "NUM_OPERATOR_SLOTS",
    "SlotAvatarMatcher",
    "AxisBuilder",
    "ActionBackend",
    "resolve_max_tick",
    "write_axis_json",
]

# Number of operator card slots across the bottom deploy area.
NUM_OPERATOR_SLOTS = 12


class SlotAvatarMatcher:
    """Fake avatar matcher that identifies operators by bottom-bar slot index."""

    def match(self, frame, center_ratio):
        x, y = center_ratio
        left, top, right, bottom = ratioconfig.OPERATOR_AREA_RATIO
        if not (left <= x <= right and top <= y <= bottom):
            return None, 0.0
        slot = min(int(x * NUM_OPERATOR_SLOTS), NUM_OPERATOR_SLOTS - 1)
        return f"op_slot_{slot}", 1.0

    def match_patch(self, patch):
        return None, 0.0


def resolve_max_tick(max_tick: Optional[int], calibration_path: Optional[Path]) -> int:
    """Resolve ``max_tick`` for the (cycle, tick) decomposition of frame_count.

    Time itself now comes from the WS feed; ``max_tick`` is only the
    per-timeline divisor (default 30).  ``calibration_path`` is accepted for
    API compatibility but no longer read — cost-bar calibration is retired for
    live recording.
    """
    _ = calibration_path  # deprecated; kept for API compatibility
    if max_tick is not None:
        return max_tick
    return 30


class AxisBuilder:
    """Aggregate ``SemanticAction`` objects into an executable JSON axis.

    Rules:
    * ``RETREAT`` and ``SKILL`` are emitted immediately.
    * ``DEPLOY`` that does **not** need direction is emitted immediately.
    * ``DEPLOY`` that needs direction is held as ``pending``.  When the matching
      ``DIRECTION`` semantic action arrives, the deploy is emitted using the
      direction drag's ``tick`` / ``cycle``.
    * ``IGNORE`` and ``SELECT`` are dropped.
    * Every emitted action receives an additional top-level ``cycle`` field taken
      from ``game_time.cycle``.
    """

    def __init__(self, map_height: int, max_tick: int = 30, frame_offset: int = 0) -> None:
        self.map_height = map_height
        self.max_tick = max_tick
        # Frame offset applied to every emitted action — used when resuming a
        # paused recording so new actions land at the correct absolute frame
        # position in the (already-recorded) timeline.
        self.frame_offset = frame_offset
        self.axis_actions: List[Dict[str, Any]] = []
        self.pending_deploys: Dict[Tuple[str, Optional[Tuple[int, int]]], SemanticAction] = {}
        self._lock = threading.Lock()

    def on_semantic_action(self, sa: SemanticAction) -> None:
        """Process one semantic action.  Thread-safe."""
        with self._lock:
            if sa.action_type == ActionType.IGNORE:
                return

            if sa.action_type == ActionType.DIRECTION:
                key = (sa.oper, sa.tile_pos)
                deploy = self.pending_deploys.get(key)
                if deploy is not None:
                    deploy.direction = sa.direction
                    if sa.game_time:
                        deploy.game_time = sa.game_time
                    self.axis_actions.append(self._to_axis_dict(deploy))
                    del self.pending_deploys[key]
                return

            if sa.action_type == ActionType.DEPLOY:
                if sa.needs_direction:
                    key = (sa.oper, sa.tile_pos)
                    self.pending_deploys[key] = sa
                else:
                    self.axis_actions.append(self._to_axis_dict(sa))
                return

            if sa.action_type in (ActionType.RETREAT, ActionType.SKILL):
                self.axis_actions.append(self._to_axis_dict(sa))

    def _to_axis_dict(self, sa: SemanticAction) -> Dict[str, Any]:
        out = sa.to_axis_dict(self.map_height)
        # Primary time field: absolute frame count with resume offset.
        raw_frame = sa.game_time.get("frame") or sa.game_time.get("total_elapsed_frames", 0) if sa.game_time else 0
        out["frame"] = (raw_frame or 0) + self.frame_offset
        return out

    def get_axis(self) -> List[Dict[str, Any]]:
        """Return a snapshot of the axis built so far."""
        with self._lock:
            return list(self.axis_actions)

    def pending_count(self) -> int:
        """Return the number of deploys still waiting for a direction drag."""
        with self._lock:
            return len(self.pending_deploys)

    def clear(self) -> None:
        """Reset the builder."""
        with self._lock:
            self.axis_actions.clear()
            self.pending_deploys.clear()


def write_axis_json(
    axis_actions: List[Dict[str, Any]],
    map_code: str,
    max_tick: int,
    output_path: Path,
    map_name: Optional[str] = None,
) -> None:
    """Write the final axis JSON in the same shape as ``sample1-7.json``."""
    settings: Dict[str, Any] = {
        "map_code": map_code,
        "max_tick": float(max_tick),
        "wait_time1": performconfig.MINIMUM_WAITTIME,
        "wait_time2": performconfig.FRAME_WAITTIME,
        "wait_time3": performconfig.GENERAL_WAITTIME,
        "bullet_threshold": performconfig.BULLET_THRESHOLD,
        "frame_threshold": performconfig.FRAME_THRESHOLD,
    }
    if map_name is not None:
        settings["map_name"] = map_name

    result = {"settings": settings, "actions": axis_actions}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


class ActionBackend:
    """Live backend: capture, analyse, recognise and build an axis in real time.

    The backend is intentionally decoupled from any UI/text output.  Callers can
    subscribe to events via ``event_callback`` and to raw semantic actions via
    ``semantic_callback`` (the latter is wired to an internal ``AxisBuilder`` by
    default so the axis is built automatically).
    """

    def __init__(
        self,
        map_code: str,
        max_tick: Optional[int] = None,
        calibration_path: Optional[Path] = None,
        event_callback: Optional[Callable[..., None]] = None,
        semantic_callback: Optional[Callable[[SemanticAction], None]] = None,
        use_slot_layout: bool = True,
        avatar_threshold: float = imgconfig.TEMPLATE_MATCH_THRESHOLD,
        cost_bar: bool = True,
        fake_avatar: bool = False,
        frame_offset: int = 0,
        recognizer_state: Optional[Dict[str, Any]] = None,
        devices: Optional[List[Dict[str, Any]]] = None,
        _matcher: Optional[Any] = None,
        _view_detector: Optional[Any] = None,
        mouse_debug: bool = False,
        save_keyframes: Optional[bool] = None,
    ) -> None:
        self.map_code = map_code
        self.map_data = get_map_by_code(map_code)
        self.calibration_path = calibration_path
        self.max_tick = resolve_max_tick(max_tick, calibration_path)
        self.event_callback = event_callback
        self._external_semantic_callback = semantic_callback
        self.use_slot_layout = use_slot_layout
        self.avatar_threshold = avatar_threshold
        self.cost_bar = cost_bar
        self.fake_avatar = fake_avatar
        # Resume-recording bias: emitted actions get frame += frame_offset.
        self.frame_offset = frame_offset
        self._recognizer_state = recognizer_state or {}
        self._devices = devices or []
        self._pre_matcher = _matcher
        self._pre_view_detector = _view_detector
        self._mouse_debug = mouse_debug
        self._save_keyframes = save_keyframes

        self.axis_builder = AxisBuilder(
            map_height=self.map_data.get("height", 0),
            max_tick=self.max_tick,
            frame_offset=frame_offset,
        )

        self.frame_source: Any = None
        self.analysis_worker: Any = None
        self.action_worker: Optional[ActionWorker] = None
        self.recorder: Optional[ActionRecorder] = None

        self._running = False
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> "ActionBackend":
        """Start capture, analysis and action recognition."""
        if self._running:
            return self
        self._running = True
        self._stop_event.clear()

        # Avatar matcher — use pre-warmed instance if available
        if self._pre_matcher is not None and not self.fake_avatar:
            matcher: Any = self._pre_matcher
            logger.info("Using pre-warmed avatar matcher")
        elif self.fake_avatar:
            matcher = SlotAvatarMatcher()
            logger.info("Using fake slot avatar matcher")
        else:
            matcher = AvatarMatcher(threshold=self.avatar_threshold)
            logger.info(
                f"Using OpenCV avatar matcher (threshold={self.avatar_threshold})"
            )

        # Optional MAA side-view detector — use pre-warmed instance if available
        if self._pre_view_detector is not None:
            view_detector: Optional[Callable[[Any], bool]] = self._pre_view_detector
            logger.info("Using pre-warmed MAA side-view detector")
        elif create_side_view_detector is not None:
            view_detector = None
            try:
                view_detector = create_side_view_detector()
                logger.info("Using MAA OCR side-view detector")
            except Exception as exc:
                logger.warning(f"Failed to create side-view detector: {exc}")
        else:
            view_detector = None

        # Continuous frame capture
        if FrameSource is not None:
            try:
                self.frame_source = FrameSource(fps=60).start()
            except Exception as exc:
                logger.warning(f"Failed to start frame source: {exc}")

        # Game time now comes from the WS time source (process singleton
        # started in init_app), so there is no cost-bar AnalysisWorker here.
        # The FrameSource above is kept purely for vision (avatar matching +
        # side-view OCR) — it no longer drives the time axis.
        self.analysis_worker = None

        # Bring MuMu to the foreground before the recorder caches its client
        # rect: this avoids two failure modes together — (a) the user's first
        # physical click being routed to the WebView (covering MuMu), and
        # (b) ClientToScreen returning ~(-32000,-32000) if MuMu is minimized,
        # which would make every recorded ratio clamp to 1.0.
        try:
            import win32con
            import win32gui

            from src.mumu.mumu_connection import get_parent_handle

            parent = get_parent_handle()
            if parent and win32gui.IsIconic(parent):
                win32gui.ShowWindow(parent, win32con.SW_RESTORE)
            if parent:
                win32gui.SetForegroundWindow(parent)
        except Exception as exc:
            logger.warning(f"Failed to bring MuMu to foreground: {exc}")

        # Mouse recorder
        try:
            self.recorder = ActionRecorder(debug=self._mouse_debug).start()
        except Exception as exc:
            logger.warning(f"Failed to start action recorder: {exc}")
            self.recorder = None

        # Semantic action worker
        save_keyframes = (
            self._save_keyframes
            if self._save_keyframes is not None
            else DebugConfig.SAVE_ACTION_KEYFRAMES
        )
        archive = None
        if save_keyframes:
            archive = ActionArchive(
                base_dir=Path(DebugConfig.ACTION_ARCHIVE_DIR),
                archive_all=DebugConfig.SAVE_ACTION_KEYFRAMES_ALL,
            )

        self.action_worker = ActionWorker(
            map_data=self.map_data,
            avatar_matcher=matcher,
            view_detector=view_detector,
            frame_provider=self._frame_at_ts,
            event_callback=self.event_callback,
            semantic_callback=self._on_semantic_action,
            use_slot_layout=self.use_slot_layout,
            archive=archive,
        ).start()

        # Seed the recognizer with any pre-placed "devices" (in-map
        # operator-equivalents declared in the timeline settings) and any
        # recognizer state inherited from a paused playback session. Resume
        # state wins on collision: a device named identically to a previously
        # deployed operator yields to the operator's actual tile.
        device_deployed = self._build_device_deployed()
        merged_state: Dict[str, Any] = dict(self._recognizer_state)
        if device_deployed:
            existing = merged_state.get("deployed") or {}
            merged_state["deployed"] = {**device_deployed, **existing}

        if merged_state and self.action_worker is not None:
            try:
                logger.info(
                    f"[recording] restoring recognizer state: {merged_state}"
                )
                self.action_worker.recognizer.load_state(merged_state)
                logger.info(
                    f"[recording] restored state: "
                    f"deployed={list(self.action_worker.recognizer.deployed.keys())}, "
                    f"selected={self.action_worker.recognizer.selected_oper}, "
                    f"view={self.action_worker.recognizer.current_view}"
                )
            except Exception as exc:
                logger.warning(f"Failed to restore recognizer state: {exc}")

        self._worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._worker_thread.start()
        return self

    def stop(self) -> List[Dict[str, Any]]:
        """Stop all subsystems and return the axis built so far."""
        if not self._running:
            return self.axis_builder.get_axis()
        self._running = False
        self._stop_event.set()

        if self._worker_thread is not None:
            self._worker_thread.join(timeout=2.0)

        if self.action_worker is not None:
            self.action_worker.stop()

        if self.recorder is not None:
            try:
                self.recorder.stop()
            except Exception as exc:
                logger.warning(f"Error stopping recorder: {exc}")

        # analysis_worker is retired (time comes from the WS singleton); the
        # field is kept as None for any external readers checking its presence.
        if self.frame_source is not None:
            try:
                self.frame_source.stop()
            except Exception as exc:
                logger.warning(f"Error stopping frame source: {exc}")

        pending = self.axis_builder.pending_count()
        if pending:
            logger.info(f"Discarding {pending} pending deploy(s) without direction")

        return self.axis_builder.get_axis()

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------
    @property
    def latest_state(self) -> Dict[str, Any]:
        """Return the latest recognizer state (empty dict if not running)."""
        if self.action_worker is None:
            return {}
        return self.action_worker.latest_state

    def get_axis(self) -> List[Dict[str, Any]]:
        return self.axis_builder.get_axis()

    @property
    def latest_game_time(self) -> Optional[Dict[str, Any]]:
        """Return best-effort current frame from the WS time feed.

        Biased by ``frame_offset`` so a resumed recording's playhead matches
        the absolute frame that will land in the timeline file.
        """
        try:
            ws = get_ws_time_source()
            frame = ws.get_game_time()
            return {"frame": int(frame) + self.frame_offset}
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _build_device_deployed(self) -> Dict[str, Tuple[int, int]]:
        """Convert ``self._devices`` into a ``recognizer.deployed`` style dict.

        The recognizer indexes ``deployed`` by operator name with values in
        ``(row, col)`` form (matching ``transform_view_to_map``). We accept
        chess-style positions like ``"C3"`` from the timeline settings and
        convert them using the timeline's map height.
        """
        height = int(self.map_data.get("height", 0) or 0)
        result: Dict[str, Tuple[int, int]] = {}
        if height <= 0:
            return result
        for device in self._devices:
            if not isinstance(device, dict):
                continue
            name = (device.get("name") or "").strip()
            pos = (device.get("pos") or "").strip()
            if not name or not pos:
                continue
            try:
                letter = pos[0].upper()
                col = int(pos[1:]) - 1
                row = height - 1 - (ord(letter) - ord("A"))
                if row < 0 or col < 0:
                    raise ValueError(f"negative tile {(row, col)}")
                result[name] = (row, col)
            except Exception as exc:
                logger.warning(f"Skipping device {name!r} with bad pos {pos!r}: {exc}")
        return result

    def _frame_at_ts(self, ts: float) -> Optional[Any]:
        """Return the latest frame from the frame source (timestamp ignored)."""
        if self.frame_source is None:
            return None
        try:
            return self.frame_source.latest()
        except Exception:
            return None

    def _on_semantic_action(self, sa: SemanticAction) -> None:
        """Route semantic actions to the axis builder and optional caller hook."""
        self.axis_builder.on_semantic_action(sa)
        if self._external_semantic_callback is not None:
            try:
                self._external_semantic_callback(sa)
            except Exception:
                logger.exception("external semantic_callback failed")

    def _run_loop(self) -> None:
        """Poll raw mouse actions and feed them to the action worker."""
        if self.recorder is None or self.action_worker is None:
            logger.error("Recorder or action worker not available")
            return

        last_action_count = 0
        while self._running and not self._stop_event.is_set():
            try:
                raw_events = self.recorder.mouse.events
                actions = self.recorder._build_actions(raw_events)

                for action in actions[last_action_count:]:
                    frame, frame_ts = (None, 0.0)
                    if self.frame_source is not None:
                        try:
                            frame, frame_ts = self.frame_source.latest()
                        except Exception:
                            frame, frame_ts = (None, 0.0)

                    # Anchor this mouse action to the current frame from WS.
                    tick_state = None
                    try:
                        ws = get_ws_time_source()
                        frame = ws.get_game_time()
                        fc, game_time, mem_ok = ws.latest()
                        tick_state = {
                            "frame": int(frame),
                            "game_time": float(game_time),
                            "connected": bool(mem_ok),
                        }
                    except Exception:
                        tick_state = None

                    self.action_worker.enqueue(
                        ActionItem(
                            action=action,
                            frame=frame,
                            frame_ts=frame_ts,
                            tick_state=tick_state,
                        )
                    )

                last_action_count = len(actions)
                time.sleep(0.01)
            except Exception:
                logger.exception("Error in action backend run loop")
                time.sleep(0.1)


# ------------------------------------------------------------------------------
# Standalone CLI (mainly for quick smoke tests; prefer scripts/run_action_backend.py)
# ------------------------------------------------------------------------------
def _main() -> None:
    parser = argparse.ArgumentParser(description="Live action backend.")
    parser.add_argument("--map-code", default="1-7", help="Map code, e.g. 1-7")
    parser.add_argument("--max-tick", type=int, default=None, help="Max tick per cycle")
    parser.add_argument("--calibration", type=Path, default=None, help="Calibration JSON path")
    parser.add_argument("--duration", type=float, default=None, help="Run duration in seconds")
    parser.add_argument("--output", type=Path, default=None, help="Output axis JSON path")
    parser.add_argument("--fake-avatar", action="store_true", help="Use slot-based fake avatar matcher")
    parser.add_argument("--no-cost-bar", action="store_true", help="Disable cost-bar analysis")
    args = parser.parse_args()

    backend = ActionBackend(
        map_code=args.map_code,
        max_tick=args.max_tick,
        calibration_path=args.calibration,
        fake_avatar=args.fake_avatar,
        cost_bar=not args.no_cost_bar,
    ).start()

    def _stop(_signum, _frame):
        backend.stop()

    signal.signal(signal.SIGINT, _stop)

    if args.duration:
        time.sleep(args.duration)
    else:
        print("Backend running. Press Ctrl+C to stop.")
        while backend._running:
            time.sleep(0.1)

    axis = backend.stop()
    output_path = args.output or Path(f"axis_{time.strftime('%Y%m%d_%H%M%S')}.json")
    write_axis_json(
        axis_actions=axis,
        map_code=args.map_code,
        max_tick=backend.max_tick,
        output_path=output_path,
        map_name=backend.map_data.get("name"),
    )
    print(f"Axis written to {output_path} ({len(axis)} actions)")


if __name__ == "__main__":
    _main()
