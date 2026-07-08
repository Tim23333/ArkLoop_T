import threading
import time
from typing import Callable, Dict, Any, List, Optional

from src.cache import get_map_by_code, get_map_by_name
from src.config import PerformActionConfig as actionconfig
from src.excel import StatusColor
from src.logic.action import Action, ActionType
from src.logic.auto_enter import auto_enter
from src.logic.calc_view import transform_map_to_view
from src.logic.convert_pos import convert_position
from src.logic.analyze_time import get_game_time, set_game_time_observer, set_time_source
from src.logic.ws_time_source import get_ws_time_source
from src.logic.perform_action import perform_action, UserPausedError
from src.logger import logger
from src.utils.error_to_log import ErrorToLog

__all__ = ["AxisRunner", "BreakpointHit"]


class BreakpointHit(Exception):
    """Raised when the runner's wait loop reaches a configured breakpoint."""

    def __init__(self, frame: int):
        super().__init__(f"Breakpoint hit at frame {frame}")
        self.frame = frame


class AxisRunner:
    """
    Runs a list of Actions against the game.

    All timing uses the WS ``frame_count`` directly. The old cycle/tick fields are accepted only when loading legacy JSON.
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
        frame_offset: int = 0,
        breakpoints: Optional[List[int]] = None,
        on_pause: Optional[Callable[[int], None]] = None,
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
        # Absolute frame offset for resume: actions with frame < frame_offset
        # are skipped (already executed in a previous session).
        self.frame_offset = frame_offset
        # Sorted list of absolute frame breakpoints.
        self.breakpoints: List[int] = sorted(breakpoints or [])
        self.on_pause = on_pause
        self._pause_requested = False
        self._breakpoint_idx: int = 0
        # State machine snapshot for recognizer warm-up on resume.
        self._runner_state: Dict[str, Any] = {
            "current_view": True,
            "selected_oper": None,
            "side_source": None,
            "deployed": {},
            "pending_deploy": None,
        }
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
        """Return True if the runner should stop at the next safe point."""
        if self._pause_requested:
            return True
        if self.stop_event is not None and self.stop_event.is_set():
            return True
        if self._external_is_paused is not None:
            return self._external_is_paused()
        return False

    def _check_breakpoints(self, frame: int) -> None:
        """Trigger pause for any breakpoints whose frame has been reached."""
        if self._pause_requested or not self.breakpoints:
            return
        while self._breakpoint_idx < len(self.breakpoints):
            bp_frame = self.breakpoints[self._breakpoint_idx]
            if bp_frame > frame:
                break
            self._breakpoint_idx += 1
            self._trigger_breakpoint(bp_frame)

    def _trigger_breakpoint(self, bp_frame: int) -> None:
        """Perform the same stop-and-notify as the UI Pause button."""
        if self._pause_requested:
            return
        logger.info(f"Breakpoint reached at frame {bp_frame}")
        if self.on_pause is not None:
            try:
                self.on_pause(int(bp_frame))
            except Exception:
                logger.exception("on_pause callback failed at breakpoint")
        self._pause_requested = True
        if self.stop_event is not None:
            self.stop_event.set()

    def _apply_settings(self):
        """Apply top-level settings to global config."""
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
        """Fold a skipped action into the state machine snapshot."""
        if action.action_type not in (ActionType.DEPLOY, ActionType.RETREAT):
            return
        if action.action_type == ActionType.DEPLOY and action.tile_pos is None:
            convert_position(action, map_height, map_width)
        self._update_runner_state(action)

    def get_state(self) -> Dict[str, Any]:
        """Return a JSON-serializable snapshot of the execution state machine."""
        return dict(self._runner_state)

    def _await_breakpoints_until(self, bp_idx: int, target_frame: int) -> int:
        """Wait until all breakpoints up to ``target_frame`` are reached.

        Returns the new ``bp_idx``.
        """
        while bp_idx < len(self.breakpoints):
            bp_frame = self.breakpoints[bp_idx]
            if bp_frame > target_frame:
                break

            while not self.is_paused():
                current = get_game_time()
                self._check_breakpoints(current)
                if self.is_paused():
                    break
                if current >= bp_frame:
                    self._trigger_breakpoint(bp_frame)
                    break
                time.sleep(0.01)

            if self.is_paused():
                return bp_idx
            bp_idx += 1

        return bp_idx

    def run(self):
        """Execute all actions."""
        # The WS time source is the sole time provider.  Ensure it's live.
        ws = get_ws_time_source()
        ws.start()
        if not ws.wait_connected(timeout=5):
            raise ErrorToLog(
                "时间源 WS 未连接，无法回放。请在设置中配置正确的 WS 地址并启动游戏时间服务。"
            )
        set_time_source(ws)

        self._apply_settings()
        map_data = self._load_map()
        view_data_front = transform_map_to_view(map_data, False)
        view_data_side = transform_map_to_view(map_data, True)

        map_height, map_width = map_data["height"], map_data["width"]
        operator_loc: Dict[str, Any] = {}
        operator_alias: Dict[str, str] = {}

        # Pre-deployed devices.
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
                operator_loc[name] = (col, row)
                self._runner_state["deployed"][name] = (row, col)
                logger.info(f"Pre-deployed device {name!r} at {pos} → tile (row={row}, col={col})")
            except Exception as exc:
                logger.warning(f"Skipping device {name!r} with bad pos {pos!r}: {exc}")

        if self.autoenter and not self.is_paused():
            auto_enter()

        # If the game frame is from a previous battle (high value), wait for
        # it to reset to 0 — the WS frame_count resets when a new battle starts.
        initial_frame = get_game_time()
        if initial_frame > 30 and self.frame_offset <= 0:
            logger.info(
                f"[playback] game frame is {initial_frame}, waiting for new battle (frame reset to 0)..."
            )
            while not self.is_paused():
                current = get_game_time()
                if current <= 1:
                    logger.info(f"[playback] frame reset detected (frame={current}), starting playback")
                    break
                time.sleep(0.05)

        # Set up observer for breakpoint checking on every get_game_time() read.
        def _game_time_observer(frame: int) -> None:
            self._check_breakpoints(frame)

        set_game_time_observer(_game_time_observer)

        # Skip breakpoints already past the current frame.
        initial_frame = get_game_time()
        bp_idx = 0
        while bp_idx < len(self.breakpoints):
            if self.breakpoints[bp_idx] <= initial_frame:
                bp_idx += 1
            else:
                break
        self._breakpoint_idx = bp_idx

        try:
            for action in self.actions:
                if self.is_paused():
                    logger.info("Paused/stopped, stopping runner.")
                    break

                # Skip actions already past the resume offset.
                action_frame = action.frame if action.frame is not None else 0
                if action_frame < self.frame_offset:
                    self._register_skipped_action(action, map_height, map_width)
                    continue

                if not action.is_valid():
                    logger.warning(f"Invalid action: {action}")
                    logger.info("Terminating the program")
                    break

                target_frame = action_frame - self.frame_offset

                # Wait for breakpoints before this action.
                bp_idx = self._await_breakpoints_until(bp_idx, target_frame)

                if self.is_paused():
                    logger.info("Paused/stopped after breakpoint check, stopping runner.")
                    break

                # Skip actions whose scheduled time has already passed.
                current_frame = get_game_time()
                if current_frame > target_frame + actionconfig.FRAME_THRESHOLD:
                    logger.warning(
                        f"Skipping action {action} because its scheduled time has passed "
                        f"(current={current_frame}, target={target_frame})"
                    )
                    self._register_skipped_action(action, map_height, map_width)
                    continue

                # Bias action frame for the time source (which restarts at 0).
                action.frame = target_frame

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
                            logger.info(f"Auto set {action.oper} location to {operator_loc[action.oper]}")

                if action.alias is not None:
                    operator_alias[action.alias] = action.oper
                    logger.info(f"Memorized {action.alias} as an alias of {action.oper}")

                if action.oper in operator_alias:
                    logger.info(f"Detected alias, replace {action.oper} with {operator_alias[action.oper]}")
                    action.oper = operator_alias[action.oper]

                if action.tile_pos is None:
                    raise ErrorToLog(f"无法确定 {action.oper} 的坐标。")
                action.view_pos_front = view_data_front[action.tile_pos[1]][action.tile_pos[0]]
                action.view_pos_side = view_data_side[action.tile_pos[1]][action.tile_pos[0]]

                # Perform the action
                try:
                    perform_action(action, self.is_paused)
                    self._set_result(StatusColor.SUCCESS)
                    self._update_runner_state(action)
                except UserPausedError:
                    logger.info("Paused/stopped during action execution, stopping runner.")
                    break
                except Exception as e:
                    self._set_result(StatusColor.FAILURE)
                    raise

            # Drain breakpoints past the last action.
            if not self.is_paused():
                self._await_breakpoints_until(bp_idx, 10 ** 18)

        except ErrorToLog as e:
            logger.error(f"Error occurred: {e}")
            if self.show_error is not None:
                self.show_error(str(e))
        except UserPausedError:
            logger.info("User-initiated stop while waiting for breakpoint/action")
        except Exception as e:
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
