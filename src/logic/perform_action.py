import time
from typing import Callable

from src.logger import logger
from src.config import GameRatioConfig as ratioconfig
from src.config import PerformActionConfig as actionconfig
from src.logic.action import Action, ActionType, DirectionType
from src.logic.locate_avatar import locate_avatar
from src.logic.analyze_time import get_game_time, wait_for_game_time_update
from src.mumu.mumu_controller import (
    pause,
    esc,
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


def wait_until_threshold(
    target_frame: int, threshold: int, user_paused: Callable[[], bool]
) -> None:
    while get_game_time() + threshold < target_frame:
        if user_paused():
            pause()
            raise UserPausedError()
        # Block until the WS feed delivers a new frame instead of busy-polling.
        wait_for_game_time_update(timeout=0.01)


def _step_paused_until(target_frame: int, user_paused: Callable[[], bool]) -> None:
    """Frame-step a paused game until ``target_frame`` is reached."""
    while get_game_time() < target_frame:
        if user_paused():
            raise UserPausedError()
        pause()
        wait_for_game_time_update(timeout=0.05)
        esc()
        time.sleep(actionconfig.MINIMUM_WAITTIME)


def _wait_running_until(target_frame: int, user_paused: Callable[[], bool]) -> None:
    """Wait while the game is running until ``target_frame`` is reached."""
    while get_game_time() < target_frame:
        if user_paused():
            pause()
            raise UserPausedError()
        wait_for_game_time_update(timeout=0.01)


def _ensure_running(timeout: float = 0.5) -> None:
    """Verify the game is running (frame advancing). If not, toggle pause."""
    f1 = get_game_time()
    time.sleep(0.05)
    f2 = get_game_time()
    if f2 > f1:
        return  # already running
    logger.warning(f"Game appears paused (frame {f1}->{f2}), toggling pause")
    pause()
    time.sleep(actionconfig.MINIMUM_WAITTIME)


def perform_deploy(
    action: Action,
    user_paused: Callable[[], bool],
    BULLET_THRESHOLD: int,
    FRAME_THRESHOLD: int,
) -> int:
    target_frame = action.get_game_time()
    prepare_frames = int(getattr(actionconfig, "DEPLOY_PREPARE_FRAMES", 60))
    direction_frames = int(getattr(actionconfig, "DEPLOY_DIRECTION_FRAMES", 30))
    prepare_frame = max(0, target_frame - prepare_frames)
    direction_frame = max(prepare_frame, target_frame - direction_frames)

    # ── Step 1: Resume game and wait until near prepare_frame ──
    # The game must be running before we can locate avatars in the deploy bar.
    actual_frame = get_game_time()
    if actual_frame < prepare_frame:
        logger.debug(f"Resuming game (frame {actual_frame}, prepare at {prepare_frame})")
        pause()
        _ensure_running()
        _wait_running_until(prepare_frame, user_paused)

    # ── Step 2: Pause, locate avatar, and prepare drag ──
    pause()
    time.sleep(actionconfig.MINIMUM_WAITTIME)

    # Find the avatar position (game is paused, deploy bar is visible)
    locate_avatar(action)

    # Check if we have actually already selected the operator
    if action.avatar_pos[1] < ratioconfig.OPERATOR_SELECTED_RATIO:
        logger.debug(f"Operator {action.oper} is already selected")
    else:
        mouseclick(action.avatar_pos)
        time.sleep(actionconfig.MINIMUM_WAITTIME)
        locate_avatar(action)

    # Calculate positions
    middle_pos = (
        action.avatar_pos[0],
        action.avatar_pos[1] - ratioconfig.DEPLOY_DRAG_RATIO,
    )
    deploy_pos = (
        action.view_pos_side[0],
        action.view_pos_side[1] + ratioconfig.DEPLOY_DELTA_RATIO,
    )

    # Set the direction
    dir_pos = None
    if action.direction == DirectionType.LEFT:
        dir_pos = (
            max(0, action.view_pos_side[0] - ratioconfig.DIRECTION_RATIO),
            action.view_pos_side[1],
        )
    elif action.direction == DirectionType.RIGHT:
        dir_pos = (
            min(1, action.view_pos_side[0] + ratioconfig.DIRECTION_RATIO),
            action.view_pos_side[1],
        )
    elif action.direction == DirectionType.UP:
        dir_pos = (
            action.view_pos_side[0],
            max(0, action.view_pos_side[1] - ratioconfig.DIRECTION_RATIO),
        )
    elif action.direction == DirectionType.DOWN:
        dir_pos = (
            action.view_pos_side[0],
            min(1, action.view_pos_side[1] + ratioconfig.DIRECTION_RATIO),
        )

    # ── Step 3: Start drag, resume game, wait, release at target_frame ──
    current_drag_pos = action.avatar_pos
    dragging = False
    try:
        _begin_drag(action.avatar_pos)
        dragging = True
        _move_drag(action.avatar_pos, deploy_pos, via=middle_pos)
        current_drag_pos = deploy_pos

        # Resume game so the deploy registers
        pause()
        _ensure_running()

        if dir_pos is not None:
            _wait_running_until(direction_frame, user_paused)
            _move_drag(current_drag_pos, dir_pos, via=action.view_pos_side)
            current_drag_pos = dir_pos

        _wait_running_until(target_frame, user_paused)
        _end_drag(current_drag_pos)
        dragging = False
        actual_frame = get_game_time()

        # Pause game after deploy
        pause()
        time.sleep(actionconfig.MINIMUM_WAITTIME)
    finally:
        if dragging:
            try:
                _end_drag(current_drag_pos)
            except Exception:
                logger.debug("failed to release mouse after deploy error", exc_info=True)
        # Ensure game is paused on exit
        try:
            pause()
        except Exception:
            logger.debug("failed to pause game after deploy error", exc_info=True)

    return actual_frame


def perform_select(
    action: Action,
    user_paused: Callable[[], bool],
    BULLET_THRESHOLD: int,
    FRAME_THRESHOLD: int,
) -> int:
    """Select a deployed operator (enter side view) at the action time."""
    target_frame = action.get_game_time()
    # Note: Pause invariant: Here the game is paused
    # First, Proceed until we reach the bullet threshold
    if get_game_time() + BULLET_THRESHOLD < target_frame:
        # When we have too much time, first resume, then enter bullet time when appropriate
        logger.debug(f"Too much time, resuming and entering bullet time")
        pause()
        _ensure_running()
        wait_until_threshold(target_frame, BULLET_THRESHOLD, user_paused)
        mouseclick(action.view_pos_front)
        time.sleep(actionconfig.GENERAL_WAITTIME)
        wait_until_threshold(target_frame, FRAME_THRESHOLD, user_paused)
        esc()
        time.sleep(actionconfig.GENERAL_WAITTIME)
    elif get_game_time() + FRAME_THRESHOLD < target_frame:
        # When we are within the bullet threshold, resume and enter bullet time, quickly
        logger.debug(f"Within bullet threshold, entering bullet time")
        pause()
        _ensure_running()
        mouseclick(action.view_pos_front)
        time.sleep(actionconfig.GENERAL_WAITTIME)
        wait_until_threshold(target_frame, FRAME_THRESHOLD, user_paused)
        esc()
        time.sleep(actionconfig.GENERAL_WAITTIME)
    else:
        # When we are already within the frame threshold, enter side view first, then try to click
        # Note: Here the click may fail, since it is not guaranteed that the operator can be selected from side view
        # Ex. the leftmost deployable position in the middle row of 1-7
        logger.debug(f"Within frame threshold, entering side view")
        mouseclick(ratioconfig.LAST_OPER_RATIO)
        time.sleep(actionconfig.GENERAL_WAITTIME)
        pause()
        _ensure_running()
        mouseclick(action.view_pos_side)
        time.sleep(actionconfig.MINIMUM_WAITTIME)
        esc()
        time.sleep(actionconfig.GENERAL_WAITTIME)

    # Note: Pause invariant: Here the game is paused
    # and also, we have selected the target operator to be under bullet time
    # Now, proceed frame by frame until we reach the target time.
    # Verify pause state first.
    for _ in range(5):
        f1 = get_game_time()
        time.sleep(0.03)
        f2 = get_game_time()
        if f2 <= f1:
            break
        esc()
        time.sleep(actionconfig.MINIMUM_WAITTIME)

    while get_game_time() < target_frame:
        pause()                          # unpause
        wait_for_game_time_update(timeout=0.05)  # wait for next frame
        esc()                            # re-pause
        time.sleep(actionconfig.MINIMUM_WAITTIME)  # 20ms — let game register pause

    # Check if we are on time
    actual_frame = get_game_time()
    if actual_frame != target_frame:
        logger.warning(
            f"Game time mismatch, performed action at frame {actual_frame} "
            f"instead of frame {target_frame}"
        )

    return actual_frame


def perform_skill_or_retreat(
    action: Action,
    user_paused: Callable[[], bool],
    BULLET_THRESHOLD: int,
    FRAME_THRESHOLD: int,
) -> int:
    """Use skill or retreat an already-selected operator."""
    actual_frame = perform_select(action, user_paused, BULLET_THRESHOLD, FRAME_THRESHOLD)

    # Final check if user paused
    if user_paused():
        raise UserPausedError()

    # Finally, do the action (game is paused — click works in pause mode)
    if action.action_type == ActionType.SKILL:
        mouseclick(ratioconfig.SKILL_RATIO)
        time.sleep(actionconfig.MINIMUM_WAITTIME)
    elif action.action_type == ActionType.RETREAT:
        mouseclick(ratioconfig.RETREAT_RATIO)
        time.sleep(actionconfig.MINIMUM_WAITTIME)
    else:
        raise ValueError(f"Invalid action type: {action.action_type}")

    # Note: Pause invariant: Here the game is paused
    return actual_frame


def perform_action(action: Action, user_paused: Callable[[], bool]) -> None:
    logger.debug(f"Performing action: {action}")
    # Note: Pause invariant: Here the game is paused

    BULLET_THRESHOLD = actionconfig.BULLET_THRESHOLD
    FRAME_THRESHOLD = actionconfig.FRAME_THRESHOLD

    target_frame = action.get_game_time()
    if action.action_type == ActionType.DEPLOY:
        actual_frame = perform_deploy(action, user_paused, BULLET_THRESHOLD, FRAME_THRESHOLD)
    elif action.action_type == ActionType.SELECT:
        actual_frame = perform_select(action, user_paused, BULLET_THRESHOLD, FRAME_THRESHOLD)
    elif (
        action.action_type == ActionType.SKILL
        or action.action_type == ActionType.RETREAT
    ):
        actual_frame = perform_skill_or_retreat(
            action, user_paused, BULLET_THRESHOLD, FRAME_THRESHOLD
        )
    else:
        raise ValueError(f"Invalid action type: {action.action_type}")

    # Note: Pause invariant: Here the game is paused
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
