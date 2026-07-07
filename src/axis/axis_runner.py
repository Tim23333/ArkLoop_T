import threading
import time
from typing import Callable, Dict, Any, List, Optional, Tuple

from src.cache import get_map_by_code, get_map_by_name
from src.config import PerformActionConfig as actionconfig
from src.excel import StatusColor
from src.logic.action import Action, ActionType
from src.logic.auto_enter import auto_enter
from src.logic.calc_view import transform_map_to_view
from src.logic.convert_pos import convert_position
from src.logic.analyze_time import get_game_time, set_game_time_observer, set_time_source
from src.logic.game_time import GameTime
from src.logic.ws_time_source import get_ws_time_source  # noqa: F401
from src.logic.perform_action import perform_action, PerformLateError, UserPausedError
from src.logger import logger
from src.utils.error_to_log import ErrorToLog

__all__ = ["AxisRunner", "BreakpointHit"]


class BreakpointHit(Exception):
    """Raised when the runner's wait loop reaches a configured breakpoint.

    Carries the (cycle, tick) the source was reading when the breakpoint
    fired — frontend uses it to update the cycle_offset for resume.
    """

    def __init__(self, cycle: int, tick: int):
        super().__init__(f"Breakpoint hit at cycle={cycle}, tick={tick}")
        self.cycle = cycle
        self.tick = tick


class AxisRunner:
    """
    Runs a list of Actions against the game.

    This decouples the execution loop from the Excel/JSON source so both
    `--xlsm` and `--axis` can share the same logic.
    """

    def __init__(
        self,
        actions: List[Action],
        settings: Dict[str, Any],
        is_paused: Callable[[], bool],
        autoenter: bool = False,
        show_error: Optional[Callable[[str], None]] = None,
        set_result_color: Optional[Callable[[int], None]] = None,
        debug: bool = False,
        tick_callback: Optional[Callable[[int, int], None]] = None,
        stop_event: Optional[threading.Event] = None,
        cycle_offset: int = 0,
        breakpoints: Optional[List[Tuple[int, int]]] = None,
        on_pause: Optional[Callable[[int, int], None]] = None,
        initial_state: Optional[Dict[str, Any]] = None,
    ):
        self.actions = actions
        self.settings = settings
        self._external_is_paused = is_paused
        self.autoenter = autoenter
        self.show_error = show_error
        self.set_result_color = set_result_color
        self.debug = debug
        self.tick_callback = tick_callback
        self.stop_event = stop_event
        # Timeline-cycle of the first in-game cycle we'll see. Used to resume
        # from a paused session: skip actions before offset; compare against
        # (time_source.cycle + cycle_offset) when waiting.
        self.cycle_offset = cycle_offset
        # Sorted list of (timeline_cycle, tick) breakpoints. Runner performs the
        # same stop-and-save-offset flow as the UI Pause button when reading
        # reaches one.
        self.breakpoints: List[Tuple[int, int]] = sorted(breakpoints or [])
        self.on_pause = on_pause
        # Pause state set when a breakpoint is reached or the caller signals stop.
        self._pause_requested = False
        # Pre-computed internal totals and index of the next breakpoint to check.
        self._breakpoint_totals: List[int] = []
        self._breakpoint_idx: int = 0
        # State machine snapshot tracked during execution so that recording
        # can resume after playback with the same deployed/selected knowledge.
        self._runner_state: Dict[str, Any] = {
            "current_view": True,
            "selected_oper": None,
            "side_source": None,
            "deployed": {},
            "pending_deploy": None,
        }
        # Seed the deployed set from a previous (paused) playback session so that
        # operators placed in an earlier segment remain known after a resume.
        # Only ``deployed`` is carried forward — the transient view/selection
        # fields naturally reset at the start of a fresh playback run.
        self._seed_deployed(initial_state)

    def _seed_deployed(self, state: Optional[Dict[str, Any]]) -> None:
        """Pre-populate ``_runner_state['deployed']`` from a carried-over state."""
        if not isinstance(state, dict):
            return
        deployed = state.get("deployed")
        if not isinstance(deployed, dict):
            return
        for oper, tile in deployed.items():
            if (
                isinstance(tile, (list, tuple))
                and len(tile) == 2
                and oper is not None
            ):
                try:
                    self._runner_state["deployed"][oper] = (int(tile[0]), int(tile[1]))
                except (TypeError, ValueError):
                    continue

    def is_paused(self) -> bool:
        """Return True if the runner should stop at the next safe point.

        This aggregates the external pause signal (UI stop button), the shared
        stop_event, and any breakpoint-triggered pause request.
        """
        if self._pause_requested:
            return True
        if self.stop_event is not None and self.stop_event.is_set():
            return True
        if self._external_is_paused is not None:
            return self._external_is_paused()
        return False


    def _check_breakpoints(self, cycle: int, tick: int) -> None:
        """Trigger UI-pause flow for any breakpoints whose time has arrived.

        ``cycle``/``tick`` are internal (time-source) values. The stored
        breakpoints are timeline-cycle values, so they are converted using
        ``cycle_offset`` before comparison.
        """
        if self._pause_requested or not self._breakpoint_totals:
            return
        tick_max = GameTime.get_tick_max()
        current_total = cycle * tick_max + tick
        while self._breakpoint_idx < len(self._breakpoint_totals):
            if self._breakpoint_totals[self._breakpoint_idx] > current_total:
                break
            bp_cycle, bp_tick = self.breakpoints[self._breakpoint_idx]
            self._breakpoint_idx += 1
            self._trigger_breakpoint(bp_cycle, bp_tick)

    def _trigger_breakpoint(self, bp_cycle: int, bp_tick: int) -> None:
        """Perform the same stop-and-notify as the UI Pause button."""
        if self._pause_requested:
            return
        logger.info(f"Breakpoint reached at cycle={bp_cycle}, tick={bp_tick}")
        if self.on_pause is not None:
            try:
                self.on_pause(int(bp_cycle), int(bp_tick))
            except Exception:
                logger.exception("on_pause callback failed at breakpoint")
        self._pause_requested = True
        if self.stop_event is not None:
            self.stop_event.set()

    def _apply_settings(self):
        """Apply top-level settings to global config.

        TICK_MAX is NOT set here — the timeline uses absolute frame_count,
        and the (cycle, tick) decomposition is purely for display.  Changing
        TICK_MAX mid-session would cause the debug overlay to flicker between
        different decompositions.
        """
        wait_time1 = self.settings.get("wait_time1")
        if wait_time1 is not None:
            actionconfig.MINIMUM_WAITTIME = wait_time1
            logger.debug(f"Set minimum wait time to {actionconfig.MINIMUM_WAITTIME}")

        wait_time2 = self.settings.get("wait_time2")
        if wait_time2 is not None:
            actionconfig.FRAME_WAITTIME = wait_time2
            logger.debug(f"Set frame wait time to {actionconfig.FRAME_WAITTIME}")

        wait_time3 = self.settings.get("wait_time3")
        if wait_time3 is not None:
            actionconfig.GENERAL_WAITTIME = wait_time3
            logger.debug(f"Set general wait time to {actionconfig.GENERAL_WAITTIME}")

        bullet_threshold = self.settings.get("bullet_threshold")
        if bullet_threshold is not None:
            actionconfig.BULLET_THRESHOLD = bullet_threshold
            logger.debug(f"Set bullet threshold to {actionconfig.BULLET_THRESHOLD}")

        frame_threshold = self.settings.get("frame_threshold")
        if frame_threshold is not None:
            actionconfig.FRAME_THRESHOLD = frame_threshold
            logger.debug(f"Set frame threshold to {actionconfig.FRAME_THRESHOLD}")

    def _load_map(self):
        """Load map data from settings."""
        map_name = self.settings.get("map_name")
        map_code = self.settings.get("map_code")

        if map_name is not None:
            return get_map_by_name(map_name)
        elif map_code is not None:
            return get_map_by_code(map_code)
        else:
            raise ErrorToLog("未指定关卡。")

    def _set_result(self, color: int):
        if self.set_result_color is not None:
            self.set_result_color(color)

    def _update_runner_state(self, action: Action) -> None:
        """Update the state-machine snapshot after an action has been executed."""
        if action.action_type == ActionType.DEPLOY:
            if action.oper is not None and action.tile_pos is not None:
                # action.tile_pos is (col, row_from_top) per convert_position,
                # but ActionRecognizer.deployed stores (row, col) (matching
                # transform_view_to_map). Swap so load_state restores tiles
                # the recognizer can actually find on subsequent clicks.
                col, row = action.tile_pos
                self._runner_state["deployed"][action.oper] = (row, col)
            self._runner_state["selected_oper"] = None
            self._runner_state["current_view"] = False
            self._runner_state["pending_deploy"] = None
        elif action.action_type == ActionType.SELECT:
            self._runner_state["selected_oper"] = action.oper
            self._runner_state["current_view"] = True
            self._runner_state["side_source"] = None
        elif action.action_type == ActionType.SKILL:
            self._runner_state["selected_oper"] = None
            self._runner_state["current_view"] = False
        elif action.action_type == ActionType.RETREAT:
            if action.oper is not None:
                self._runner_state["deployed"].pop(action.oper, None)
            self._runner_state["selected_oper"] = None
            self._runner_state["current_view"] = False

    def _register_skipped_action(self, action: Action, map_height: int, map_width: int) -> None:
        """Fold a skipped DEPLOY/RETREAT into the state machine snapshot.

        Actions before the resume offset (or whose scheduled time already
        passed) are not executed, but they still describe what is on the map.
        Without this, an operator deployed in a skipped region would be missing
        from ``deployed`` and a later RETREAT — during playback or in a recording
        resumed from this state — could not be matched.
        """
        if action.action_type not in (ActionType.DEPLOY, ActionType.RETREAT):
            return
        if action.action_type == ActionType.DEPLOY and action.tile_pos is None:
            convert_position(action, map_height, map_width)
        self._update_runner_state(action)

    def get_state(self) -> Dict[str, Any]:
        """Return a JSON-serializable snapshot of the execution state machine.

        This is used to warm up the recognizer when recording resumes after
        playback, so that already-deployed operators are still known.
        """
        return dict(self._runner_state)

    def _await_breakpoints_until(self, bp_idx: int, action_target_total: int) -> int:
        """Wait until all breakpoints up to ``action_target_total`` are reached.

        Breakpoints are checked every time ``get_game_time()`` is sampled, so a
        breakpoint can fire in the middle of an action as well as between
        actions. When a breakpoint fires we perform the same stop-and-notify flow
        as the UI Pause button: ``on_pause`` is called, ``stop_event`` is set,
        and ``is_paused()`` starts returning True.

        ``action_target_total`` is the action's target as total in-game frames
        (already adjusted for ``cycle_offset``).  ``bp_idx`` is the index into
        ``self.breakpoints`` of the next unprocessed breakpoint.

        Returns the new ``bp_idx`` so the caller can keep iterating.
        """
        tick_max = GameTime.get_tick_max()
        while bp_idx < len(self.breakpoints):
            bp_cycle, bp_tick = self.breakpoints[bp_idx]
            bp_total = (bp_cycle - self.cycle_offset) * tick_max + bp_tick
            if bp_total > action_target_total:
                # No breakpoint between the current position and the next action.
                break

            # Poll until the game's elapsed frames reach bp_total, checking for
            # an external pause/stop at every sample.
            while not self.is_paused():
                gt = get_game_time()
                self._check_breakpoints(gt.cycle, gt.tick)
                if self.is_paused():
                    break
                current_total = gt.cycle * tick_max + gt.tick
                if current_total >= bp_total:
                    # Observer/path already triggered the breakpoint; make sure
                    # the pause request is set before returning.
                    self._trigger_breakpoint(bp_cycle, bp_tick)
                    break
                # Light sleep — breakpoint accuracy is per-tick, no need to spin.
                time.sleep(0.01)

            if self.is_paused():
                return bp_idx
            bp_idx += 1

        return bp_idx


    def run(self):
        """Execute all actions."""
        # The time axis now comes from the WebSocket time source (external
        # game-memory reader) instead of cost-bar calibration.  The singleton
        # is started once at app startup; here we only ensure it is live and
        # refuse to play back when the feed is unavailable.
        ws = get_ws_time_source()
        ws.start()
        if not ws.wait_connected(timeout=5):
            raise ErrorToLog(
                "时间源 WS 未连接，无法回放。请在设置中配置正确的 WS 地址并启动游戏时间服务。"
            )

        # TICK_MAX stays at the default (30).  It's only used for the
        # cycle/tick display decomposition; execution uses frame_count
        # directly.  Do NOT set it from settings.max_tick — that was a
        # cost-bar-era setting that would cause the debug overlay to jump.
        set_time_source(ws)  # no-op compat; documents intent

        self._apply_settings()
        map_data = self._load_map()
        view_data_front = transform_map_to_view(map_data, False)
        view_data_side = transform_map_to_view(map_data, True)

        map_height, map_width = map_data["height"], map_data["width"]
        operator_loc: Dict[str, Any] = {}
        operator_alias: Dict[str, str] = {}

        # Pre-deployed devices declared in the timeline settings act exactly
        # like already-deployed units: their position is known up-front so
        # SELECT / SKILL / RETREAT actions on them resolve without a prior
        # DEPLOY. We mirror this into ``operator_loc`` (used by perform_action)
        # and ``_runner_state["deployed"]`` (used to warm up the recognizer
        # when recording resumes after playback).
        for device in (self.settings.get("devices") or []):
            if not isinstance(device, dict):
                continue
            name = (device.get("name") or "").strip()
            pos = (device.get("pos") or "").strip()
            if not name or not pos:
                continue
            try:
                letter_idx = ord(pos[0].upper()) - ord("A")
                col = int(pos[1:]) - 1
                row = map_height - 1 - letter_idx
                if row < 0 or col < 0:
                    raise ValueError(f"negative tile {(row, col)}")
                # operator_loc uses (col, row_from_top) like Action.tile_pos.
                operator_loc[name] = (col, row)
                # recognizer.deployed uses (row, col).
                self._runner_state["deployed"][name] = (row, col)
                logger.info(f"Pre-deployed device {name!r} at {pos} → tile (row={row}, col={col})")
            except Exception as exc:
                logger.warning(f"Skipping device {name!r} with bad pos {pos!r}: {exc}")

        # Auto enter if needed
        if self.autoenter and not self.is_paused():
            auto_enter()

        # Stream the live (cycle, tick) to the UI at the runner's own read rate,
        # and also check timeline breakpoints every time the time source is read.
        # ``get_game_time`` is called frequently while waiting for / stepping to
        # each action, so breakpoints can fire in the middle of an action, not
        # only between actions.
        cb = self.tick_callback

        def _game_time_observer(cycle: int, tick: int) -> None:
            self._check_breakpoints(cycle, tick)
            if cb is not None:
                cb(int(cycle), int(tick))

        set_game_time_observer(_game_time_observer)

        # Iteration index into self.breakpoints: anything < this has already
        # been processed (or skipped because the game is already past it).
        # When resuming from a previous breakpoint hit, the game's current
        # time equals the breakpoint's time — we must advance past it,
        # otherwise the same breakpoint re-fires immediately and "Play"
        # appears to do nothing.
        tick_max = GameTime.get_tick_max()
        initial_gt = get_game_time()
        initial_total = initial_gt.cycle * tick_max + initial_gt.tick
        self._breakpoint_totals = [
            (bp_cycle - self.cycle_offset) * tick_max + bp_tick
            for bp_cycle, bp_tick in self.breakpoints
        ]
        bp_idx = 0
        while bp_idx < len(self._breakpoint_totals):
            if self._breakpoint_totals[bp_idx] <= initial_total:
                bp_idx += 1
            else:
                break
        self._breakpoint_idx = bp_idx

        try:
            for action in self.actions:
                if self.is_paused():
                    logger.info("Paused/stopped, stopping runner.")
                    break

                # Compute frame_offset for resume: frame_offset = cycle_offset * max_tick.
                tick_max = GameTime.get_tick_max()
                frame_offset = self.cycle_offset * tick_max

                # Skip actions that the user has already passed on a previous
                # session (paused-and-resumed).  Still fold them into the state
                # machine so the deployed set reflects the skipped region.
                action_frame = action.frame if action.frame is not None else (
                    (action.cycle or 0) * tick_max + (action.tick or 0)
                )
                if action_frame < frame_offset:
                    self._register_skipped_action(action, map_height, map_width)
                    continue

                # Check if the action is valid
                if not action.is_valid():
                    logger.warning(f"Invalid action: {action}")
                    logger.info("Terminating the program")
                    break

                # Target frame for this action (relative to time source origin).
                target_frame = action_frame - frame_offset

                # Wait for any breakpoint that falls before this action; when
                # reached, _await_breakpoints_until returns with is_paused() True
                # and the same stop-and-notify flow as the UI Pause button.
                bp_idx = self._await_breakpoints_until(bp_idx, target_frame)

                if self.is_paused():
                    logger.info("Paused/stopped after breakpoint check, stopping runner.")
                    break

                # Skip actions whose scheduled time has already passed. This
                # happens when playback is paused and resumed: the runner is
                # recreated and only knows the frame offset, not which actions
                # were already executed. Re-executing a DEPLOY after the operator
                # is already on the map will fail avatar matching in the deploy
                # bar.
                ws = get_ws_time_source()
                current_frame = ws.latest()[0]  # frame_count from WS
                if current_frame > target_frame + actionconfig.FRAME_THRESHOLD:
                    logger.warning(
                        f"Skipping action {action} because its scheduled time has passed "
                        f"(current={current_frame}, target={target_frame})"
                    )
                    self._register_skipped_action(action, map_height, map_width)
                    continue

                # Bias action time so perform_action's wait loops compare to
                # the in-game time_source (which restarts at frame 0).
                action.frame = target_frame
                if action.cycle is not None:
                    action.cycle = action.cycle - self.cycle_offset

                # Calculate the tile position from raw position
                convert_position(action, map_height, map_width)

                # Memorize operator location if needed
                if action.action_type == ActionType.DEPLOY:
                    operator_loc[action.oper] = action.tile_pos
                    if action.alias is not None:
                        operator_loc[action.alias] = action.tile_pos
                    logger.info(f"Memorized {action.oper} location at {operator_loc[action.oper]}")
                else:
                    if action.tile_pos is None:
                        action.tile_pos = operator_loc.get(action.oper)
                        if action.tile_pos is not None:
                            logger.info(f"Auto set {action.oper} location to {action.tile_pos}")

                # Tackle alias if needed
                if action.alias is not None:
                    operator_alias[action.alias] = action.oper
                    logger.info(f"Memorized {action.alias} as an alias of {action.oper}")

                if action.oper in operator_alias:
                    logger.info(f"Detected alias, replace {action.oper} with {operator_alias[action.oper]}")
                    action.oper = operator_alias[action.oper]

                # Fetch view position
                if action.tile_pos is None:
                    raise ErrorToLog(f"无法确定 {action.oper} 的坐标。")
                action.view_pos_front = view_data_front[action.tile_pos[1]][action.tile_pos[0]]
                action.view_pos_side = view_data_side[action.tile_pos[1]][action.tile_pos[0]]

                # Perform the action
                try:
                    perform_action(action, self.is_paused)
                    self._set_result(StatusColor.SUCCESS)
                    self._update_runner_state(action)
                except PerformLateError as e:
                    self._set_result(StatusColor.WARNING)
                    if e.actual_time > e.scheduled_time + GameTime(1, 0):
                        raise ErrorToLog(f"当前操作晚了超过一周期。疑似发生错误。请求人工接管。")
                except UserPausedError:
                    # Clean stop, same as the UI Pause button: do not surface an
                    # error dialog. The caller already received notification via
                    # on_pause / stop_event.
                    logger.info("Paused/stopped during action execution, stopping runner.")
                    break
                except Exception as e:
                    self._set_result(StatusColor.FAILURE)
                    raise

            # Drain any breakpoints scheduled after the last action — they
            # are pause-points on the timeline and should still fire even
            # when no action lies beyond them.
            if not self.is_paused():
                self._await_breakpoints_until(bp_idx, 10 ** 18)

        except ErrorToLog as e:
            logger.error(f"Error occurred: {e}")
            if self.show_error is not None:
                self.show_error(str(e))
        except UserPausedError:
            # Polling helpers raise this when the user-paused signal flips while
            # waiting. Treat it as a clean user-initiated stop, same as the UI
            # Pause button.
            logger.info("User-initiated stop while waiting for breakpoint/action")
        except Exception as e:
            # str(e) is empty for many no-arg exceptions; include the class
            # name so debugging logs are never blank. logger.exception adds
            # the traceback so the next time something slips through we can
            # see where it came from.
            logger.exception(f"Unhandled runner error: {type(e).__name__}: {e!r}")
            if self.show_error is not None:
                msg = str(e) or type(e).__name__
                self.show_error(f"未定义错误：{msg}")
        finally:
            set_game_time_observer(None)
            set_time_source(None)
            if self.debug:
                logger.info("Press any key to exit.")
                input()
