import dataclasses
from enum import Enum
from typing import Tuple, Optional

from src.utils.typecheck import is_valid_type
from src.logic.game_time import GameTime
from src.logger import logger


class ActionType(Enum):
    DEPLOY = "部署"
    SELECT = "选中"
    SKILL = "技能"
    RETREAT = "撤退"


class DirectionType(Enum):
    UP = "上"
    DOWN = "下"
    LEFT = "左"
    RIGHT = "右"
    NONE = "无"


@dataclasses.dataclass(order=True)
class Action:
    # Primary time field: absolute game frame count since battle start.
    # When set, `cycle` and `tick` are derived from it via TICK_MAX.
    frame: Optional[int] = None
    # Legacy cost-bar fields.  Still populated for backward compat with old
    # timelines and the perform_action/axis_runner internals that operate on
    # GameTime(cycle, tick).  New recordings set `frame` and derive these.
    cycle: Optional[int] = None
    tick: Optional[int] = None
    action_type: Optional[ActionType] = None
    oper: Optional[str] = None
    pos: Optional[str] = None
    direction: Optional[DirectionType] = None
    alias: Optional[str] = None
    tile_pos: Optional[Tuple[int, int]] = None
    avatar_pos: Optional[Tuple[float, float]] = None
    view_pos_front: Optional[Tuple[float, float]] = None
    view_pos_side: Optional[Tuple[float, float]] = None

    def get_game_time(self):
        """Return GameTime for this action.

        If `frame` is set (new format), decompose it via TICK_MAX.
        Otherwise fall back to the legacy cycle/tick pair.
        """
        if self.frame is not None:
            tick_max = GameTime.get_tick_max() or 30
            return GameTime(self.frame // tick_max, self.frame % tick_max)
        return GameTime(self.cycle or 0, self.tick or 0)

    def is_valid(self) -> bool:
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            if not is_valid_type(value, field.type):
                logger.warning(f"Invalid field: {field.name}={value}")
                return False
        # Accept either `frame` (new) or `cycle`+`tick` (legacy) as valid.
        has_frame = self.frame is not None and self.frame >= 0
        has_cycle_tick = (
            self.cycle is not None and self.cycle >= 0
            and self.tick is not None and self.tick >= 0
        )
        if not has_frame and not has_cycle_tick:
            return False
        if self.action_type is None:
            return False
        if self.oper is None and self.pos is None:
            return False
        if self.action_type == ActionType.DEPLOY:
            if self.pos is None:
                return False
            if self.direction is None:
                return False
        return True
