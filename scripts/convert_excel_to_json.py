"""
Convert an existing Excel axis (.xlsm) into the new JSON axis format.

Usage:
    python scripts/convert_excel_to_json.py "sample 1-7.xlsm"

Output:
    sample-1-7.json (in the same directory as the Excel file)
"""

import json
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.excel import Excel
from src.logic.action import ActionType, DirectionType


_ACTION_TYPE_REVERSE = {
    ActionType.DEPLOY: "部署",
    ActionType.SKILL: "技能",
    ActionType.RETREAT: "撤退",
}

_DIRECTION_TYPE_REVERSE = {
    DirectionType.UP: "上",
    DirectionType.DOWN: "下",
    DirectionType.LEFT: "左",
    DirectionType.RIGHT: "右",
    DirectionType.NONE: "无",
}

_SETTING_NAMES = [
    "map_code",
    "map_name",
    "max_tick",
    "wait_time1",
    "wait_time2",
    "wait_time3",
]


def action_to_dict(action):
    """Convert an Action dataclass to a JSON-serializable dict."""
    data = {
        "cost": action.cost,
        "tick": action.tick,
        "action_type": _ACTION_TYPE_REVERSE.get(action.action_type),
        "oper": action.oper,
        "pos": action.pos,
        "direction": _DIRECTION_TYPE_REVERSE.get(action.direction),
        "alias": action.alias,
    }
    # Drop None values for cleaner JSON
    return {k: v for k, v in data.items() if v is not None}


def convert_excel_to_json(xlsm_path: str, json_path: str = None):
    xlsm_path = os.path.abspath(xlsm_path)
    if not os.path.isfile(xlsm_path):
        raise FileNotFoundError(f"Excel file not found: {xlsm_path}")

    excel = Excel(xlsm_path)

    settings = {}
    for name in _SETTING_NAMES:
        try:
            value = excel.get_setting(name)
            if value is not None:
                settings[name] = value
        except Exception as e:
            print(f"Warning: could not read setting '{name}': {e}")

    actions = []
    while True:
        action = excel.get_current_action()
        if not action.is_valid():
            break
        if action.action_type != ActionType.SELECT:
            actions.append(action_to_dict(action))
        excel.next_action()

    output = {
        "settings": settings,
        "actions": actions,
    }

    if json_path is None:
        base, _ = os.path.splitext(xlsm_path)
        json_path = base + ".json"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Converted {len(actions)} actions from {xlsm_path} to {json_path}")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert Excel axis to JSON axis")
    parser.add_argument("xlsm", help="Path to the Excel axis file (.xlsm)")
    parser.add_argument("--output", "-o", help="Output JSON path (default: same name with .json extension)")
    args = parser.parse_args()

    convert_excel_to_json(args.xlsm, args.output)
