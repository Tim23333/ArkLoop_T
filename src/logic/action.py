import dataclasses
from enum import Enum
from typing import Tuple, Optional

from src.utils.typecheck import is_valid_type
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
    # Primary and only time field: absolute game frame count since battle start.
    # All timing (waiting, skipping, breakpoints) uses this directly.
    frame: Optional[int] = None
    action_type: Optional[ActionType] = None
    oper: Optional[str] = None
    pos: Optional[str] = None
    direction: Optional[DirectionType] = None
    alias: Optional[str] = None
    tile_pos: Optional[Tuple[int, int]] = None
    avatar_pos: Optional[Tuple[float, float]] = None
    view_pos_front: Optional[Tuple[float, float]] = None
    view_pos_side: Optional[Tuple[float, float]] = None

    def get_game_time(self) -> int:
        """Return the absolute frame for this action."""
        return self.frame if self.frame is not None else 0

    def is_valid(self) -> bool:
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            if not is_valid_type(value, field.type):
                logger.warning(f"Invalid field: {field.name}={value}")
                return False
        if self.frame is None or self.frame < 0:
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
