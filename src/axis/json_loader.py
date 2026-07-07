import json
import os
from typing import List, Tuple, Dict, Any, Optional

from src.logic.action import Action, ActionType, DirectionType
from src.logger import logger

__all__ = ["load_axis_from_json"]

# Map Chinese action/direction strings from Excel to enums
_ACTION_TYPE_MAP = {
    "部署": ActionType.DEPLOY,
    "选中": ActionType.SELECT,
    "技能": ActionType.SKILL,
    "撤退": ActionType.RETREAT,
}

_DIRECTION_TYPE_MAP = {
    "上": DirectionType.UP,
    "下": DirectionType.DOWN,
    "左": DirectionType.LEFT,
    "右": DirectionType.RIGHT,
    "无": DirectionType.NONE,
}

# Settings keys supported at the top level of a JSON axis file
_SETTING_KEYS = {
    "map_code",
    "map_name",
    "max_tick",
    "wait_time1",
    "wait_time2",
    "wait_time3",
    "bullet_threshold",
    "frame_threshold",
    "calibration_path",
    # Per-timeline breakpoints: list of {"cycle": int, "tick": int}.
    # Runner pauses on reaching each. Ignored by the recorder.
    "breakpoints",
}


def _parse_action(raw: Dict[str, Any], row: int, max_tick: int = 30) -> Action:
    """Convert a raw action dict into an Action dataclass instance.

    Supports two time formats:
    - New: ``{"frame": 1190, ...}`` — absolute frame count.
    - Legacy: ``{"cycle": 10, "tick": 0, ...}`` — cost-bar decomposition.

    When ``frame`` is present, ``cycle`` and ``tick`` are derived from it
    (using ``max_tick``) so that all downstream code can rely on either field.
    """
    try:
        action_type_str = raw.get("action_type")
        if action_type_str is None:
            raise ValueError("Missing action_type")
        action_type = _ACTION_TYPE_MAP.get(action_type_str)
        if action_type is None:
            raise ValueError(f"Unknown action_type: {action_type_str}")

        direction_str = raw.get("direction", "无")
        direction = _DIRECTION_TYPE_MAP.get(direction_str)
        if direction is None:
            raise ValueError(f"Unknown direction: {direction_str}")

        frame: Optional[int] = raw.get("frame")
        cycle: Optional[int] = raw.get("cycle")
        tick: Optional[int] = raw.get("tick")

        # Derive cycle/tick from frame when the legacy fields are absent.
        if frame is not None:
            if cycle is None:
                cycle = frame // max_tick
            if tick is None:
                tick = frame % max_tick
        # Derive frame from cycle/tick when only legacy fields are present.
        elif cycle is not None and tick is not None:
            frame = cycle * max_tick + tick

        return Action(
            frame=frame,
            cycle=cycle,
            tick=tick,
            action_type=action_type,
            oper=raw.get("oper"),
            pos=raw.get("pos"),
            direction=direction,
            alias=raw.get("alias"),
        )
    except Exception as e:
        logger.error(f"Failed to parse action at row {row}: {raw} - {e}")
        raise


def load_axis_from_json(file_path: str) -> Tuple[List[Action], Dict[str, Any]]:
    """
    Load an axis from a JSON file.

    Supported JSON structures::

        // New format (frame-based)
        {
            "settings": { "map_code": "1-7", "max_tick": 30, ... },
            "actions": [
                { "frame": 1190, "action_type": "部署", "oper": "...", ... }
            ]
        }

        // Legacy format (cycle/tick-based)
        {
            "settings": { "map_code": "1-7", "max_tick": 119, ... },
            "actions": [
                { "cycle": 10, "tick": 0, "action_type": "部署", "oper": "...", ... }
            ]
        }

    Returns:
        Tuple of (list of Action instances, settings dict).
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Axis file not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("Axis JSON must be an object")

    settings = data.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("Axis 'settings' must be an object")

    # Only keep supported setting keys
    settings = {k: v for k, v in settings.items() if k in _SETTING_KEYS}

    max_tick = int(settings.get("max_tick") or 30)

    raw_actions = data.get("actions", [])
    if not isinstance(raw_actions, list):
        raise ValueError("Axis 'actions' must be a list")

    actions: List[Action] = []
    for idx, raw in enumerate(raw_actions, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"Action at index {idx} must be an object")
        actions.append(_parse_action(raw, idx, max_tick=max_tick))

    logger.info(f"Loaded {len(actions)} actions from {file_path}")
    return actions, settings


if __name__ == "__main__":
    # Simple sanity test
    actions, settings = load_axis_from_json(
        os.path.join(os.path.dirname(__file__), "..", "..", "sample-1-7.json")
    )
    print(settings)
    for a in actions[:3]:
        print(a)
