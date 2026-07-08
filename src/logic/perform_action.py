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


def wait_until_threshold(
    target_frame: int, threshold: int, user_paused: Callable[[], bool]
) -> None:
    while get_game_time() + threshold < target_frame:
        if user_paused():
            pause()
            raise UserPausedError()
        # Block until the WS feed delivers a new frame instead of busy-polling.
        wait_for_game_time_update(timeout=0.01)


def perform_deploy(
    action: Action,
    user_paused: Callable[[], bool],
    BULLET_THRESHOLD: int,
    FRAME_THRESHOLD: int,
) -> int:
    target_frame = action.get_game_time()
    # Note: Pause invariant: Here the game is paused
    # First, Proceed until we reach the frame threshold
    if get_game_time() + BULLET_THRESHOLD < target_frame:
        # When we have too much time, first resume, then enter bullet time when appropriate
        logger.debug(f"Too much time, resuming and entering bullet time")
        pause()
        wait_until_threshold(target_frame, BULLET_THRESHOLD, user_paused)
        mouseclick(ratioconfig.LAST_OPER_RATIO)
        time.sleep(actionconfig.GENERAL_WAITTIME)
        wait_until_threshold(target_frame, FRAME_THRESHOLD, user_paused)
        esc()
        time.sleep(actionconfig.GENERAL_WAITTIME)
    elif get_game_time() + FRAME_THRESHOLD < target_frame:
        # When we are within the bullet threshold, directly enter bullet time, then resume
        logger.debug(f"Within bullet threshold, entering bullet time")
        mouseclick(ratioconfig.LAST_OPER_RATIO)
        time.sleep(actionconfig.GENERAL_WAITTIME)
        pause()
        wait_until_threshold(target_frame, FRAME_THRESHOLD, user_paused)
        esc()
        time.sleep(actionconfig.GENERAL_WAITTIME)
    else:
        # When we are already within the frame threshold, directly enter bullet time, and don't resume at all
        logger.debug(f"Within frame threshold, entering bullet time")
        mouseclick(ratioconfig.LAST_OPER_RATIO)
        time.sleep(actionconfig.GENERAL_WAITTIME)

    # Note: Pause invariant: Here the game is paused
    # and also, we have selected the last operator to be under bullet time
    # Now, proceed frame by frame until we reach the target time.
    # Each cycle: unpause (pause) → wait one frame → re-pause (esc).
    # Verify pause state: if the frame is still advancing, the game is NOT
    # paused — send esc() until it stops.
    for i in range(5):
        f1 = get_game_time()
        time.sleep(0.03)
        f2 = get_game_time()
        if f2 <= f1:
            logger.debug(f"Game confirmed paused at frame {f2} (attempt {i})")
            break  # game is paused
        logger.warning(f"Game not paused (frame {f1}->{f2}), sending esc (attempt {i})")
        esc()
        time.sleep(actionconfig.MINIMUM_WAITTIME)

    while get_game_time() < target_frame:
        pause()                          # unpause
        wait_for_game_time_update(timeout=0.05)  # wait for next frame
        esc()                            # re-pause
        time.sleep(actionconfig.MINIMUM_WAITTIME)  # let game register

    # Finally, do the action — game is paused at exactly target_frame.
    # Find the avatar position
    locate_avatar(action)

    # Check if we have actually already selected the operator
    # This may happen when the target operator is the last operator
    if action.avatar_pos[1] < ratioconfig.OPERATOR_SELECTED_RATIO:
        logger.debug(f"Operator {action.oper} is already selected")
    else:
        # Select the operator
        mouseclick(action.avatar_pos)
        time.sleep(actionconfig.GENERAL_WAITTIME)

        # Now the operator is selected, find avatar position again since it may have changed
        locate_avatar(action)

    # Calculate the middle position for dragging
    middle_pos = (
        action.avatar_pos[0],
        action.avatar_pos[1] - ratioconfig.DEPLOY_DRAG_RATIO,
    )

    # Note: Pause invariant: Here the game is paused

    # Final check if user paused
    if user_paused():
        raise UserPausedError()

    # Deploy the operator
    pause()
    mousedown(action.avatar_pos)
    mousemove(middle_pos)
    time.sleep(actionconfig.MINIMUM_WAITTIME)
    esc()
    time.sleep(actionconfig.GENERAL_WAITTIME)

    # Check if we are on time
    actual_frame = get_game_time()
    if actual_frame != target_frame:
        logger.warning(
            f"Game time mismatch, performed action at frame {actual_frame} "
            f"instead of frame {target_frame}"
        )

    # Do the rest of the deploy
    mousemove((action.view_pos_side[0], action.view_pos_side[1] + ratioconfig.DEPLOY_DELTA_RATIO))
    time.sleep(actionconfig.GENERAL_WAITTIME)
    mouseup((action.view_pos_side[0], action.view_pos_side[1] + ratioconfig.DEPLOY_DELTA_RATIO))
    time.sleep(actionconfig.GENERAL_WAITTIME)

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
    if dir_pos:
        mousedown(action.view_pos_side)
        time.sleep(actionconfig.GENERAL_WAITTIME)
        mousemove(dir_pos)
        time.sleep(actionconfig.GENERAL_WAITTIME)
        mouseup(dir_pos)
        time.sleep(actionconfig.GENERAL_WAITTIME)

    # Note: Pause invariant: Here the game is paused
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

    # Finally, do the action
    if action.action_type == ActionType.SKILL:
        mouseclick(ratioconfig.SKILL_RATIO)
        time.sleep(actionconfig.GENERAL_WAITTIME)
    elif action.action_type == ActionType.RETREAT:
        mouseclick(ratioconfig.RETREAT_RATIO)
        time.sleep(actionconfig.GENERAL_WAITTIME)
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
