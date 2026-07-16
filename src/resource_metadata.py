from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def _write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=4),
        encoding="utf-8",
    )


def _profession_display(profession: str) -> str:
    return {
        "WARRIOR": "近卫",
        "TANK": "重装",
        "PIONEER": "先锋",
        "SPECIAL": "特种",
        "SNIPER": "狙击",
        "CASTER": "术师",
        "MEDIC": "医疗",
        "SUPPORT": "辅助",
        "TOKEN": "召唤物",
        "TRAP": "装置",
    }.get(profession, profession)


def _range_is_symmetric(grids: list[dict[str, Any]]) -> bool:
    cells = {(grid.get("row"), grid.get("col")) for grid in grids}
    return all((row, -col) in cells for row, col in cells if isinstance(col, int))


def _needs_direction(char_entry: dict[str, Any], range_table: dict[str, Any]) -> bool:
    profession = str(char_entry.get("profession", ""))
    if profession == "TRAP":
        return False
    if profession != "TOKEN":
        return True

    phases = char_entry.get("phases") or []
    range_id = phases[0].get("rangeId") if phases else None
    range_data = range_table.get(range_id, {}) if range_id else {}
    grids = range_data.get("grids") or []
    return bool(grids) and not _range_is_symmetric(grids)


def generate_resource_indexes(resource_dir: Path) -> Dict[str, int]:
    """Regenerate the lookup JSON files derived from synchronized resources."""
    root = Path(resource_dir)

    overview = json.loads((root / "map" / "overview.json").read_text(encoding="utf-8"))
    if not isinstance(overview, dict):
        raise ValueError("map/overview.json must contain a JSON object")
    level_code_mapping = {
        value["code"]: value["filename"]
        for value in overview.values()
        if isinstance(value, dict) and value.get("code") and value.get("filename")
    }
    level_name_mapping = {
        value["name"]: value["filename"]
        for value in overview.values()
        if isinstance(value, dict) and value.get("name") and value.get("filename")
    }
    _write_json(root / "level_code_mapping.json", level_code_mapping)
    _write_json(root / "level_name_mapping.json", level_name_mapping)

    battle_data = json.loads((root / "battle_data.json").read_text(encoding="utf-8"))
    chars = battle_data.get("chars", {}) if isinstance(battle_data, dict) else {}
    if not isinstance(chars, dict):
        raise ValueError("battle_data.json does not contain a chars object")
    operator_mapping: dict[str, str] = {}
    for char_id, value in chars.items():
        if not isinstance(value, dict) or not value.get("name"):
            continue
        name = str(value["name"])
        operator_mapping[name] = str(char_id)
        quoteless = name.replace("“", "").replace("”", "")
        if quoteless != name:
            operator_mapping[quoteless] = str(char_id)
    _write_json(root / "operator_mapping.json", operator_mapping)

    character_table = json.loads((root / "character_table.json").read_text(encoding="utf-8"))
    range_table = json.loads((root / "range_table.json").read_text(encoding="utf-8"))
    if not isinstance(character_table, dict) or not isinstance(range_table, dict):
        raise ValueError("character_table.json and range_table.json must contain JSON objects")

    unit_metadata: dict[str, dict[str, Any]] = {}
    for name, char_id in operator_mapping.items():
        char_entry = character_table.get(char_id)
        if not isinstance(char_entry, dict):
            continue
        profession = str(char_entry.get("profession", ""))
        unit_metadata[name] = {
            "char_id": char_id,
            "name": name,
            "profession": profession,
            "profession_display": _profession_display(profession),
            "sub_profession_id": char_entry.get("subProfessionId", ""),
            "needs_direction": _needs_direction(char_entry, range_table),
        }
    _write_json(root / "unit_metadata.json", unit_metadata)

    return {
        "maps": len(level_code_mapping),
        "operators": len(operator_mapping),
        "unit_metadata": len(unit_metadata),
    }
