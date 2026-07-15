import time

from src.logger import logger
from src.config import GameRatioConfig as ratioconfig
from src.config import PerformActionConfig as actionconfig
from src.logic.action import Action, DirectionType
from src.logic.locate_avatar import locate_avatar
from src.mumu.mumu_controller import (
    mouseclick,
    mousedown,
    mouseup,
    mousemove,
)


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
