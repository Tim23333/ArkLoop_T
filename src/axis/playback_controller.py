from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Any, Callable, Dict, Optional

from src.axis.adaptive_timing import AdaptivePlaybackTiming
from src.config import GameRatioConfig as ratioconfig
from src.config import PerformActionConfig as actionconfig
from src.logger import logger
from src.logic.action import Action, ActionType
from src.logic.analyze_time import get_game_time, wait_for_game_time_update
from src.logic.perform_action import perform_deploy, perform_retreat, perform_skill
from src.maa import MaaRecognizer
from src.mumu.mumu_controller import mouseclick
from src.mumu.mumu_vision import capture_game_window

__all__ = [
    "PlaybackController",
    "PlaybackInterrupted",
    "PlaybackPhase",
    "PrecisePauseError",
    "StopMode",
]


class PlaybackPhase(str, Enum):
    IDLE = "idle"
    WAITING_BULLET = "waiting_bullet"
    PRESELECTING = "preselecting"
    WAITING_PAUSE = "waiting_pause"
    PAUSING = "pausing"
    FRAME_STEPPING = "frame_stepping"
    EXECUTING = "executing"
    RESUMING = "resuming"
    WAITING_ACTION = "waiting_action"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


class StopMode(str, Enum):
    PAUSE = "pause"
    STOP = "stop"


class PlaybackInterrupted(Exception):
    def __init__(self, mode: StopMode, action_completed: bool = False) -> None:
        super().__init__(f"Playback interrupted: {mode.value}")
        self.mode = mode
        self.action_completed = action_completed


class PrecisePauseError(Exception):
    pass


_pause_recognizer: MaaRecognizer | None = None


def _get_pause_recognizer() -> MaaRecognizer:
    global _pause_recognizer
    if _pause_recognizer is None:
        _pause_recognizer = MaaRecognizer()
    return _pause_recognizer


def _image_reports_paused() -> bool | None:
    """Return paused/running, or None when image recognition is inconclusive."""
    image = capture_game_window(ratio=None, color=True)
    paused = _get_pause_recognizer().detect_pause_state(image)
    logger.debug(f"pause image verification: paused={paused!r}")
    return paused


class PlaybackController:
    """Own the complete lifecycle of frame-accurate playback control.

    ``AxisRunner`` decides *which* action is next. This controller exclusively
    decides *when* and *how* that action is executed, including pause requests,
    precise frame stepping, input dispatch, resume, and stop cleanup.
    """

    def __init__(
        self,
        state_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self._state_callback = state_callback
        self._lock = threading.RLock()
        self._request_event = threading.Event()
        self._stop_mode: StopMode | None = None
        self._stop_source: str | None = None
        self._phase = PlaybackPhase.IDLE
        self._target_frame: int | None = None
        self._current_frame = 0
        self._action_type: str | None = None
        self._game_paused = False
        self._last_pause_toggle_at: float | None = None
        self._timing = AdaptivePlaybackTiming.from_config(actionconfig)

    @property
    def stop_requested(self) -> bool:
        return self._request_event.is_set()

    @property
    def game_paused(self) -> bool:
        with self._lock:
            return self._game_paused

    @property
    def phase(self) -> PlaybackPhase:
        with self._lock:
            return self._phase

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "phase": self._phase.value,
                "target_frame": self._target_frame,
                "current_frame": self._current_frame,
                "action_type": self._action_type,
                "game_paused": self._game_paused,
                "stop_mode": self._stop_mode.value if self._stop_mode else None,
                "stop_source": self._stop_source,
                "timing": self._timing.snapshot(),
            }

    def _publish(self) -> None:
        if self._state_callback is None:
            return
        try:
            self._state_callback(self.snapshot())
        except Exception:
            logger.debug("playback state callback failed", exc_info=True)

    def _set_phase(self, phase: PlaybackPhase) -> None:
        with self._lock:
            if self._phase == phase:
                return
            self._phase = phase
        logger.debug(f"Playback phase -> {phase.value}")
        self._publish()

    def _read_frame(self) -> int:
        frame = int(get_game_time())
        self._timing.observe_frame(frame)
        with self._lock:
            self._current_frame = frame
        return frame

    def request_pause(self, source: str = "user") -> None:
        with self._lock:
            if self._stop_mode == StopMode.STOP:
                return
            self._stop_mode = StopMode.PAUSE
            self._stop_source = source
            self._request_event.set()
        self._publish()

    def request_stop(self, source: str = "user") -> None:
        with self._lock:
            self._stop_mode = StopMode.STOP
            self._stop_source = source
            self._request_event.set()
        self._publish()

    def _toggle_game_pause(self, settle: bool = False) -> None:
        minimum_interval = max(
            0.0,
            float(actionconfig.PAUSE_TOGGLE_MIN_INTERVAL),
        )
        now = time.perf_counter()
        with self._lock:
            last_toggle_at = self._last_pause_toggle_at
        if last_toggle_at is not None:
            remaining = minimum_interval - (now - last_toggle_at)
            if remaining > 0:
                time.sleep(remaining)

        click_started = time.perf_counter()
        mouseclick(ratioconfig.PAUSE_BUTTON_RATIO)
        clicked_at = time.perf_counter()
        self._timing.observe_input_latency(clicked_at - click_started)
        with self._lock:
            self._last_pause_toggle_at = clicked_at
        if settle:
            time.sleep(float(actionconfig.PAUSE_TOGGLE_SETTLE))

    def _pause_state_matches(
        self,
        expected_paused: bool,
        label: str,
    ) -> bool | None:
        reads = max(1, int(actionconfig.PAUSE_VERIFY_READS))
        interval = max(0.0, float(actionconfig.PAUSE_VERIFY_READ_INTERVAL))
        observed_state: bool | None = None
        for read_index in range(reads):
            try:
                current_state = _image_reports_paused()
                if current_state == expected_paused:
                    return True
                if current_state is not None:
                    observed_state = current_state
            except Exception:
                logger.warning(
                    f"Pause image recognition failed during {label}",
                    exc_info=True,
                )
            if read_index + 1 < reads and interval > 0:
                time.sleep(interval)
        return False if observed_state is not None else None

    def _ensure_game_paused(self, label: str) -> None:
        retries = max(1, int(actionconfig.PAUSE_VERIFY_RETRIES))
        for attempt in range(retries + 1):
            matched = self._pause_state_matches(True, label)
            if matched is True:
                with self._lock:
                    self._game_paused = True
                self._publish()
                return
            if matched is None:
                raise PrecisePauseError(
                    f"Unable to verify game pause before {label}: image state inconclusive"
                )
            if attempt >= retries:
                break
            logger.warning(
                f"Pause image not detected during {label}; "
                f"retrying pause ({attempt + 1}/{retries})"
            )
            self._toggle_game_pause(settle=True)
            with self._lock:
                self._game_paused = True
        raise PrecisePauseError(f"Unable to verify game pause before {label}")

    def _pause_game(self, label: str) -> None:
        if self.game_paused:
            self._ensure_game_paused(label)
            return
        self._set_phase(PlaybackPhase.PAUSING)
        self._toggle_game_pause(settle=True)
        with self._lock:
            self._game_paused = True
        self._ensure_game_paused(label)

    def _ensure_game_resumed(self, label: str) -> None:
        retries = max(1, int(actionconfig.RESUME_VERIFY_RETRIES))
        for attempt in range(retries + 1):
            matched = self._pause_state_matches(False, f"resume after {label}")
            if matched is True:
                with self._lock:
                    self._game_paused = False
                self._publish()
                return
            if matched is None:
                logger.warning(
                    f"Unable to verify resume after {label}; "
                    "trusting the delivered resume click without toggling again"
                )
                with self._lock:
                    self._game_paused = False
                self._publish()
                return
            if attempt >= retries:
                break
            logger.warning(
                f"Game still paused after {label}; "
                f"retrying resume ({attempt + 1}/{retries})"
            )
            self._toggle_game_pause()
            time.sleep(float(actionconfig.RESUME_TOGGLE_SETTLE))
        raise PrecisePauseError(f"Unable to verify game resume after {label}")

    def _resume_game(self, label: str) -> None:
        if not self.game_paused:
            return
        self._set_phase(PlaybackPhase.RESUMING)
        self._toggle_game_pause()
        time.sleep(float(actionconfig.RESUME_TOGGLE_SETTLE))
        self._ensure_game_resumed(label)
        logger.debug(f"Game resumed after {label}")

    def ensure_game_running(self, label: str = "playback exit") -> None:
        """Resume the game on every exit except an intentional playback pause."""
        with self._lock:
            keep_paused = (
                self._stop_mode == StopMode.PAUSE
                or self._phase == PlaybackPhase.PAUSED
            )
        if keep_paused:
            return

        paused = self.game_paused
        try:
            detected_state = _image_reports_paused()
            if detected_state is not None:
                paused = detected_state
        except Exception:
            logger.warning(
                f"Unable to inspect pause state during {label}; using controller state",
                exc_info=True,
            )

        with self._lock:
            self._game_paused = bool(paused)
        if paused:
            self._resume_game(label)
        else:
            self._publish()

    def _settle_interruption(self, action_completed: bool = False) -> None:
        with self._lock:
            mode = self._stop_mode or StopMode.STOP

        if mode == StopMode.PAUSE:
            if not self.game_paused:
                self._pause_game("playback pause")
            self._set_phase(PlaybackPhase.PAUSED)
        else:
            if self.game_paused:
                self._resume_game("playback stop")
            self._set_phase(PlaybackPhase.STOPPED)
        raise PlaybackInterrupted(mode, action_completed=action_completed)

    def check_interruption(self, action_completed: bool = False) -> None:
        if self.stop_requested:
            self._settle_interruption(action_completed=action_completed)

    def pause_at_breakpoint(self, frame: int) -> None:
        """Synchronously pause the game for a reached timeline breakpoint."""
        with self._lock:
            self._current_frame = int(frame)
        self.request_pause(source="breakpoint")
        try:
            self._settle_interruption()
        except PlaybackInterrupted:
            pass

    def resume_for_new_session(self) -> None:
        """Dismiss a pause retained by a previous paused playback session."""
        if self.game_paused:
            self._resume_game("new session")
        with self._lock:
            self._stop_mode = None
            self._stop_source = None
            self._request_event.clear()
            self._target_frame = None
            self._action_type = None
        self._set_phase(PlaybackPhase.IDLE)

    def mark_completed(self) -> None:
        if not self.stop_requested and self.phase != PlaybackPhase.FAILED:
            self._set_phase(PlaybackPhase.COMPLETED)

    def _wait_until(self, target_frame: int, phase: PlaybackPhase) -> None:
        self._set_phase(phase)
        while self._read_frame() < target_frame:
            self.check_interruption()
            wait_for_game_time_update(timeout=0.01)
        self.check_interruption()

    def _wait_for_frame_advance(self, start_frame: int) -> int:
        timeout = max(0.0, float(actionconfig.FRAME_STEP_UPDATE_TIMEOUT))
        poll_interval = max(
            0.001,
            float(actionconfig.FRAME_STEP_POLL_INTERVAL),
        )
        deadline = time.perf_counter() + timeout
        current_frame = self._read_frame()

        while current_frame <= start_frame:
            self.check_interruption()
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            wait_for_game_time_update(timeout=min(poll_interval, remaining))
            current_frame = self._read_frame()

        if current_frame > start_frame:
            settle = max(0.0, float(actionconfig.FRAME_STEP_FEED_SETTLE))
            if settle > 0:
                time.sleep(settle)
                current_frame = self._read_frame()
        return current_frame

    def _frame_step_until(self, target_frame: int) -> None:
        self._set_phase(PlaybackPhase.FRAME_STEPPING)
        current_frame = self._read_frame()
        while current_frame < target_frame:
            self.check_interruption()

            start_frame = current_frame
            self._toggle_game_pause()
            with self._lock:
                self._game_paused = False
            time.sleep(self._timing.pulse_seconds)
            self._toggle_game_pause(settle=True)
            with self._lock:
                self._game_paused = True
            self._ensure_game_paused("frame step")

            current_frame = self._wait_for_frame_advance(start_frame)
            frame_delta = current_frame - start_frame
            self._timing.record_step(frame_delta)
            if current_frame <= start_frame:
                if self._timing.no_progress_pulses >= int(
                    actionconfig.FRAME_STEP_MAX_EMPTY_PULSES
                ):
                    raise PrecisePauseError(
                        "Game frame did not advance after repeated adaptive pause pulses"
                    )
                logger.debug(
                    f"Frame pulse made no progress at {start_frame}; "
                    f"next pulse={self._timing.pulse_seconds * 1000.0:.1f}ms"
                )
                continue
            logger.debug(
                f"Frame step {start_frame} -> {current_frame} "
                f"(target {target_frame}, "
                f"pulse={self._timing.pulse_seconds * 1000.0:.1f}ms)"
            )

        if current_frame > target_frame:
            logger.warning(
                f"Frame step overshot target: frame {current_frame} "
                f"vs target {target_frame}"
            )
        self.check_interruption()

    @staticmethod
    def _preselect_pos(action: Action) -> tuple[float, float]:
        if action.action_type == ActionType.DEPLOY:
            return ratioconfig.LAST_OPER_RATIO
        if action.action_type in (ActionType.SKILL, ActionType.RETREAT):
            if action.view_pos_front is None:
                raise ValueError(f"Missing front-view position for {action.action_type}")
            return action.view_pos_front
        raise ValueError(f"Unsupported playback action type: {action.action_type}")

    @staticmethod
    def _execute_input(action: Action) -> None:
        if action.action_type == ActionType.DEPLOY:
            perform_deploy(action)
        elif action.action_type == ActionType.SKILL:
            perform_skill(action)
        elif action.action_type == ActionType.RETREAT:
            perform_retreat(action)
        else:
            raise ValueError(f"Unsupported playback action type: {action.action_type}")

    def execute(self, action: Action) -> int:
        """Execute one action at its exact target frame and return actual frame."""
        target_frame = action.get_game_time()
        with self._lock:
            self._target_frame = target_frame
            self._action_type = action.action_type.value if action.action_type else None
        self._publish()

        try:
            bullet_frame = max(0, target_frame - int(actionconfig.BULLET_TIME_FRAMES))

            self._wait_until(bullet_frame, PlaybackPhase.WAITING_BULLET)
            self._set_phase(PlaybackPhase.PRESELECTING)
            preselect_started = time.perf_counter()
            mouseclick(self._preselect_pos(action))
            self._timing.observe_input_latency(time.perf_counter() - preselect_started)
            time.sleep(float(actionconfig.MINIMUM_WAITTIME))

            pause_lead = self._timing.precise_pause_lead(
                int(actionconfig.PRECISE_PAUSE_FRAMES),
                min(
                    int(actionconfig.BULLET_TIME_FRAMES),
                    int(actionconfig.ADAPTIVE_PRECISE_PAUSE_MAX_FRAMES),
                ),
            )
            pause_frame = max(bullet_frame, target_frame - pause_lead)
            logger.debug(
                f"Adaptive precise pause: target={target_frame}, entry={pause_frame}, "
                f"lead={pause_lead}, timing={self._timing.snapshot()}"
            )

            self._wait_until(pause_frame, PlaybackPhase.WAITING_PAUSE)
            self._pause_game("precise-pause entry")
            self._frame_step_until(target_frame)

            self._set_phase(PlaybackPhase.EXECUTING)
            self._execute_input(action)
            actual_frame = self._read_frame()

            self.check_interruption(action_completed=True)
            time.sleep(float(actionconfig.ACTION_RESUME_DELAY))
            self.check_interruption(action_completed=True)
            self._resume_game("action")
            self._set_phase(PlaybackPhase.WAITING_ACTION)
        except PlaybackInterrupted:
            raise
        except Exception:
            try:
                self.ensure_game_running("failed action")
            except Exception:
                logger.exception("Failed to resume game after playback action error")
            finally:
                self._set_phase(PlaybackPhase.FAILED)
            raise

        if actual_frame == target_frame:
            logger.info(f"Performed action: {action}")
        elif actual_frame > target_frame:
            logger.warning(
                f"Performed action: {action} "
                f"(not on time, frame {actual_frame} vs target {target_frame})"
            )
        else:
            logger.warning(
                f"Performed action: {action} "
                f"(unexpected time, frame {actual_frame} vs target {target_frame})"
            )
        return actual_frame
