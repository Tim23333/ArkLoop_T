import time
from typing import Callable

from src.logger import logger
from src.config import GameRatioConfig as ratioconfig
from src.config import PerformActionConfig as actionconfig
from src.logic.action import Action, ActionType, DirectionType
from src.logic.locate_avatar import locate_avatar
from src.logic.analyze_time import get_game_time, wait_for_game_time_update
from src.mumu.mumu_controller import (
    mouseclick,
    mousedown,
    mouseup,
    mousemove,
)


class PerformLateError(Exception):
    def __init__(self, actual_frame: int, scheduled_frame: int):
        super().__init__(
            f"Performed action at frame {actual_frame} instead of {scheduled_frame}"
        )
        self.actual_frame = actual_frame
        self.scheduled_frame = scheduled_frame

    def __str__(self):
        return (
            f"Performed action at frame {self.actual_frame} "
            f"instead of {self.scheduled_frame}"
        )


class UserPausedError(Exception):
    pass


class PrecisePauseError(Exception):
    pass


def _drag_mouse(
    start: tuple[float, float],
    end: tuple[float, float],
    via: tuple[float, float] | None = None,
) -> None:
    """Send a stable held drag with small interpolation steps."""
    _begin_drag(start)
    _move_drag(start, end, via=via)
    _end_drag(end)


def _begin_drag(start: tuple[float, float]) -> None:
    """Press and hold the left mouse button long enough for the game to pick up."""
    hold_time = float(getattr(actionconfig, "DRAG_HOLD_TIME", actionconfig.MINIMUM_WAITTIME))
    mousedown(start)
    time.sleep(hold_time)


def _move_drag(
    start: tuple[float, float],
    end: tuple[float, float],
    via: tuple[float, float] | None = None,
) -> None:
    """Move the held mouse along a smooth path."""
    steps = max(1, int(getattr(actionconfig, "DRAG_STEPS", 5)))
    step_wait = float(getattr(actionconfig, "DRAG_STEP_WAIT", actionconfig.MINIMUM_WAITTIME))

    points = [start, end] if via is None else [start, via, end]
    for segment_start, segment_end in zip(points, points[1:]):
        for idx in range(1, steps + 1):
            ratio = idx / steps
            pos = (
                segment_start[0] + (segment_end[0] - segment_start[0]) * ratio,
                segment_start[1] + (segment_end[1] - segment_start[1]) * ratio,
            )
            mousemove(pos)
            time.sleep(step_wait)


def _end_drag(end: tuple[float, float]) -> None:
    """Release the held mouse button."""
    mouseup(end)
    time.sleep(actionconfig.MINIMUM_WAITTIME)


def _toggle_game_pause(settle: bool = False) -> None:
    """Toggle the in-game pause/play button without sending ESC."""
    mouseclick(ratioconfig.PAUSE_BUTTON_RATIO)
    if settle:
        time.sleep(
            float(
                getattr(
                    actionconfig,
                    "PAUSE_TOGGLE_SETTLE",
                    actionconfig.MINIMUM_WAITTIME,
                )
            )
        )


def _frame_stable_for(duration: float, user_paused: Callable[[], bool]) -> bool:
    """Return True when the frame counter does not advance for ``duration``."""
    duration = max(0.0, float(duration))
    if duration <= 0:
        return True

    start_frame = get_game_time()
    deadline = time.perf_counter() + duration
    while time.perf_counter() < deadline:
        if user_paused():
            raise UserPausedError()
        remaining = max(0.0, deadline - time.perf_counter())
        wait_for_game_time_update(timeout=min(0.01, remaining))
        current_frame = get_game_time()
        if current_frame != start_frame:
            logger.debug(
                f"pause verification saw frame advance "
                f"{start_frame}->{current_frame}"
            )
            return False
    return True


def _ensure_game_paused(user_paused: Callable[[], bool], label: str) -> None:
    """Verify pause state by checking that the frame counter is stable."""
    stable_time = float(
        getattr(actionconfig, "PAUSE_VERIFY_STABLE_TIME", 0.06)
    )
    retries = max(1, int(getattr(actionconfig, "PAUSE_VERIFY_RETRIES", 3)))

    for attempt in range(retries):
        if _frame_stable_for(stable_time, user_paused):
            return
        logger.warning(
            f"Game still advancing during {label}; "
            f"retrying pause ({attempt + 1}/{retries})"
        )
        _toggle_game_pause(settle=True)

    if _frame_stable_for(stable_time, user_paused):
        return
    raise PrecisePauseError(f"Unable to verify game pause before {label}")


def _wait_running_until(target_frame: int, user_paused: Callable[[], bool]) -> None:
    """Wait while the game is running until ``target_frame`` is reached."""
    while get_game_time() < target_frame:
        if user_paused():
            raise UserPausedError()
        wait_for_game_time_update(timeout=0.01)


def _frame_step_paused_until(target_frame: int, user_paused: Callable[[], bool]) -> None:
    """While paused, briefly resume every 8ms until the target frame arrives."""
    interval = float(getattr(actionconfig, "FRAME_STEP_INTERVAL", 0.008))
    while get_game_time() < target_frame:
        if user_paused():
            raise UserPausedError()
        _toggle_game_pause()
        time.sleep(interval)
        _toggle_game_pause(settle=True)
        wait_for_game_time_update(timeout=interval)
    _ensure_game_paused(user_paused, "target-frame action")


def _enter_precise_pause(
    target_frame: int,
    focus_pos: tuple[float, float] | None,
    user_paused: Callable[[], bool],
    on_pause_entered: Callable[[], None] | None = None,
) -> None:
    """Enter bullet-time, pause near target, then frame-step to target."""
    bullet_frames = int(getattr(actionconfig, "BULLET_TIME_FRAMES", 30))
    pause_frames = int(getattr(actionconfig, "PRECISE_PAUSE_FRAMES", 10))
    bullet_frame = max(0, target_frame - bullet_frames)
    pause_frame = max(bullet_frame, target_frame - pause_frames)

    _wait_running_until(bullet_frame, user_paused)
    if focus_pos is not None:
        mouseclick(focus_pos)
        time.sleep(actionconfig.MINIMUM_WAITTIME)

    _wait_running_until(pause_frame, user_paused)
    _toggle_game_pause(settle=True)
    _ensure_game_paused(user_paused, "precise-pause entry")
    if on_pause_entered is not None:
        on_pause_entered()
    _frame_step_paused_until(target_frame, user_paused)


def _resume_precise_pause(precise_paused: bool, label: str) -> None:
    if not precise_paused:
        return
    try:
        _toggle_game_pause(settle=True)
    except Exception:
        logger.debug(f"failed to resume after {label}", exc_info=True)


def _direction_end_pos(action: Action) -> tuple[float, float] | None:
    if action.direction == DirectionType.LEFT:
        return (
            max(0, action.view_pos_side[0] - ratioconfig.DIRECTION_RATIO),
            action.view_pos_side[1],
        )
    if action.direction == DirectionType.RIGHT:
        return (
            min(1, action.view_pos_side[0] + ratioconfig.DIRECTION_RATIO),
            action.view_pos_side[1],
        )
    if action.direction == DirectionType.UP:
        return (
            action.view_pos_side[0],
            max(0, action.view_pos_side[1] - ratioconfig.DIRECTION_RATIO),
        )
    if action.direction == DirectionType.DOWN:
        return (
            action.view_pos_side[0],
            min(1, action.view_pos_side[1] + ratioconfig.DIRECTION_RATIO),
        )
    return None


def perform_deploy(
    action: Action,
) -> None:
    """Deploy an operator. The game must already be paused at target frame."""
    current_drag_pos = action.avatar_pos
    dragging = False

    try:
        locate_avatar(action)
        if action.avatar_pos[1] >= ratioconfig.OPERATOR_SELECTED_RATIO:
            mouseclick(action.avatar_pos)
            time.sleep(actionconfig.MINIMUM_WAITTIME)
            locate_avatar(action)

        middle_pos = (
            action.avatar_pos[0],
            action.avatar_pos[1] - ratioconfig.DEPLOY_DRAG_RATIO,
        )
        deploy_pos = (
            action.view_pos_side[0],
            action.view_pos_side[1] + ratioconfig.DEPLOY_DELTA_RATIO,
        )

        _begin_drag(action.avatar_pos)
        dragging = True
        _move_drag(action.avatar_pos, deploy_pos, via=middle_pos)
        current_drag_pos = deploy_pos
        _end_drag(current_drag_pos)
        dragging = False

        dir_pos = _direction_end_pos(action)
        if dir_pos is not None:
            time.sleep(
                float(
                    getattr(
                        actionconfig,
                        "DEPLOY_TO_DIRECTION_WAIT",
                        actionconfig.MINIMUM_WAITTIME,
                    )
                )
            )
            _drag_mouse(action.view_pos_side, dir_pos)
    finally:
        if dragging:
            try:
                _end_drag(current_drag_pos)
            except Exception:
                logger.debug("failed to release mouse after deploy error", exc_info=True)


def perform_skill(action: Action) -> None:
    """Use skill. The game must already be paused at target frame."""
    mouseclick(ratioconfig.SKILL_RATIO)
    time.sleep(actionconfig.MINIMUM_WAITTIME)


def perform_retreat(action: Action) -> None:
    """Retreat an operator. The game must already be paused at target frame."""
    mouseclick(ratioconfig.RETREAT_RATIO)
    time.sleep(actionconfig.MINIMUM_WAITTIME)


def _preselect_pos(action: Action) -> tuple[float, float]:
    if action.action_type == ActionType.DEPLOY:
        return ratioconfig.LAST_OPER_RATIO
    if action.action_type in (ActionType.SKILL, ActionType.RETREAT):
        if action.view_pos_front is None:
            raise ValueError(f"Missing front-view position for {action.action_type}")
        return action.view_pos_front
    raise ValueError(f"Unsupported playback action type: {action.action_type}")


def perform_action(action: Action, user_paused: Callable[[], bool]) -> None:
    logger.debug(f"Performing action: {action}")

    target_frame = action.get_game_time()
    precise_paused = False
    action_completed = False

    def mark_precise_paused() -> None:
        nonlocal precise_paused
        precise_paused = True

    try:
        _enter_precise_pause(
            target_frame,
            _preselect_pos(action),
            user_paused,
            mark_precise_paused,
        )
        if user_paused():
            raise UserPausedError()

        if action.action_type == ActionType.DEPLOY:
            perform_deploy(action)
        elif action.action_type == ActionType.SKILL:
            perform_skill(action)
        elif action.action_type == ActionType.RETREAT:
            perform_retreat(action)
        else:
            raise ValueError(f"Unsupported playback action type: {action.action_type}")

        actual_frame = get_game_time()
        action_completed = True
    finally:
        _resume_precise_pause(precise_paused and action_completed, "action")

    if actual_frame == target_frame:
        logger.info(f"Performed action: {action}")
    elif actual_frame > target_frame:
        logger.warning(f"Performed action: {action} (not on time, frame {actual_frame} vs target {target_frame})")
    else:
        logger.warning(f"Performed action: {action} (unexpected time, frame {actual_frame} vs target {target_frame})")


if __name__ == "__main__":
    # Usage and testing
    from src.cache import get_map_by_code
    from src.logic.calc_view import transform_map_to_view
    from src.logic.action import DirectionType

    map = get_map_by_code("1-7")
    view_map_front = transform_map_to_view(map, False)
    view_map_side = transform_map_to_view(map, True)
    action = Action(
        frame=15,
        action_type=ActionType.DEPLOY,
        oper="斑点",
        pos="D2",
        direction=DirectionType.RIGHT,
        alias="",
        tile_pos=(1, 3),
        avatar_pos=None,
        view_pos_front=view_map_front[3][1],
        view_pos_side=view_map_side[3][1],
    )
    start_time = time.time()
    perform_action(action, lambda: False)
    end_time = time.time()
    logger.info(
        f"Action performed: {action} (time elapsed: {end_time - start_time:.3f} seconds)"
    )
