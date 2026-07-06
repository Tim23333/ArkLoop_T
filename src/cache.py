import cv2
import json
import os
import glob
import numpy as np
from typing import List, Dict, Any, Callable

from src.logger import logger
from src.config import DebugConfig
from src.config import ImageProcessingConfig as imgconfig

__all__ = [
    "load_avatars", "get_avatars", "replace_avatar",
    "load_map_by_code", "get_map_by_code", "load_map_by_name", "get_map_by_name",
    "load_unit_metadata", "get_unit_metadata",
]

RESOURCE_PATH = os.path.join(os.path.dirname(__file__), "..", "resource")
NEW_RESOURCE_PATH = os.path.join(os.path.dirname(__file__), "..", "new_resource")


def load_mapping(mapping_file: str) -> Dict[str, str]:
    """
    Load a mapping from a JSON file.

    Args:
        mapping_file: The name of the mapping file.

    Returns:
        The loaded mapping.

    Raises:
        FileNotFoundError: If the mapping file is not found.
    """
    try:
        with open(
            os.path.join(RESOURCE_PATH, mapping_file), "r", encoding="utf-8"
        ) as file:
            return json.load(file)
    except FileNotFoundError:
        logger.error(f"{mapping_file} not found")
        raise FileNotFoundError(f"{mapping_file} not found")


def load_resource(
    resource_name: str,
    resource_mapping: Dict[str, str],
    resource_path: str,
    load_func: Callable[[List[str]], Any],
) -> Any:
    """
    Load a resource given its name, mapping, path, and a function to load the resource.

    Args:
        resource_name: The name of the resource.
        resource_mapping: The mapping from resource names to filenames.
        resource_path: The path where the resource is stored.
        load_func: The function to load the resource.

    Returns:
        The loaded resource.

    Raises:
        ValueError: If the resource name is not in the mapping.
        FileNotFoundError: If the resource file is not found.
    """
    if resource_name not in resource_mapping:
        logger.error(
            f"No resource found for name: {resource_name}, please check if the name is valid"
        )
        raise ValueError(
            f"No resource found for name: {resource_name}, please check if the name is valid"
        )
    resource_filename = resource_mapping[resource_name]
    filepaths = glob.glob(f"{resource_path}/*{resource_filename}*")
    if not filepaths:
        # Expected for non-deployable summons (trap_*) / tokens (token_*):
        # they're in the mapping but have no avatar file and never appear in
        # the deploy bar.  The caller (AvatarMatcher._load_templates) catches
        # FileNotFoundError and skips silently — log at DEBUG, not ERROR, so
        # prewarm doesn't spam ~335 "missing resource" lines.
        logger.debug(f"No resource file for name: {resource_name} ({resource_filename})")
        raise FileNotFoundError(f"No resource found for name: {resource_name}")
    try:
        return load_func(filepaths)
    except Exception as e:
        logger.error(f"Error occurred when loading resource: {e}")
        raise


def get_resource(resource_name: str, resource_dict: Dict[str, Any], load_func) -> Any:
    """
    Get a resource from a dictionary. If the resource is not in the dictionary, load it using a function.

    Args:
        resource_name: The name of the resource.
        resource_dict: The dictionary where the resource is stored.
        load_func: The function to load the resource.

    Returns:
        The resource.
    """
    if resource_name not in resource_dict:
        load_func(resource_name)
    return resource_dict[resource_name]


OPERATOR_MAPPING: Dict[str, str] = load_mapping("operator_mapping.json")
LEVEL_CODE_MAPPING: Dict[str, str] = load_mapping("level_code_mapping.json")
LEVEL_NAME_MAPPING: Dict[str, str] = load_mapping("level_name_mapping.json")

avatars: Dict[str, List[np.ndarray]] = {}
maps: Dict[str, Dict[str, Any]] = {}
unit_metadata: Dict[str, Dict[str, Any]] = {}


def _resolve_metadata_path(filename: str) -> str:
    """优先从 resource/ 读取，不存在则回退到 new_resource/。"""
    primary = os.path.join(RESOURCE_PATH, filename)
    if os.path.isfile(primary):
        return primary
    fallback = os.path.join(NEW_RESOURCE_PATH, filename)
    if os.path.isfile(fallback):
        return fallback
    raise FileNotFoundError(f"{filename} not found in resource/ or new_resource/")


def process_avatar(path: str) -> np.ndarray:
    # Note: This may cause problem since the img is in RGBA format, but since we are cropping it, it should be fine
    avatar = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if avatar is None:
        raise FileNotFoundError(f"Image file {path} could not be loaded")
    avatar = cv2.resize(avatar, imgconfig.AVATAR_STANDARD_SIZE)

    # crop center
    y, x = avatar.shape
    cropx, cropy = imgconfig.AVATAR_CROP_SIZE
    startx = x // 2 - cropx // 2
    starty = y // 2 - cropy // 2
    return avatar[starty : starty + cropy, startx : startx + cropx]


def load_avatars(oper_name: str) -> None:
    def load_func(paths: List[str]) -> List[np.ndarray]:
        return [process_avatar(path) for path in paths]

    avatars[oper_name] = load_resource(
        oper_name, OPERATOR_MAPPING, os.path.join(RESOURCE_PATH, "avatar"), load_func
    )
    if DebugConfig.LOG_RESOURCE_LOAD:
        logger.info(f"Loaded avatars for {oper_name}")


def load_map_by_code(map_code: str) -> None:
    def load_func(paths: List[str]) -> Dict[str, Any]:
        with open(paths[0], "r", encoding="utf-8") as file:
            return json.load(file)

    maps[map_code] = load_resource(
        map_code, LEVEL_CODE_MAPPING, os.path.join(RESOURCE_PATH, "map"), load_func
    )
    if DebugConfig.LOG_RESOURCE_LOAD:
        logger.info(f"Loaded map data for {map_code}")


def load_map_by_name(map_name: str) -> None:
    def load_func(paths: List[str]) -> Dict[str, Any]:
        with open(paths[0], "r", encoding="utf-8") as file:
            return json.load(file)

    maps[map_name] = load_resource(
        map_name, LEVEL_NAME_MAPPING, os.path.join(RESOURCE_PATH, "map"), load_func
    )
    if DebugConfig.LOG_RESOURCE_LOAD:
        logger.info(f"Loaded map data for {map_name}")


def get_avatars(oper_name: str) -> List[np.ndarray]:
    return get_resource(oper_name, avatars, load_avatars)


def get_map_by_code(map_code: str) -> Dict[str, Any]:
    return get_resource(map_code, maps, load_map_by_code)


def get_map_by_name(map_name: str) -> Dict[str, Any]:
    return get_resource(map_name, maps, load_map_by_name)


def replace_avatar(oper_name: str, avatar: np.ndarray) -> None:
    avatars[oper_name] = [avatar]


def load_unit_metadata() -> None:
    """Load unit metadata from resource/unit_metadata.json (or new_resource fallback)."""
    path = _resolve_metadata_path("unit_metadata.json")
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("unit_metadata.json must contain a JSON object")
    unit_metadata.update(data)
    if DebugConfig.LOG_RESOURCE_LOAD:
        logger.info(f"Loaded unit metadata from {path} ({len(data)} entries)")


def get_unit_metadata(oper_name: str) -> Dict[str, Any]:
    if not unit_metadata:
        load_unit_metadata()
    return unit_metadata.get(oper_name, {})


if __name__ == "__main__":
    # Usage and Testing
    avatars = get_avatars("弦惊")
    for avatar in avatars:
        cv2.imshow("avatar", avatar)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    map = get_map_by_code("LS-5")
    print(map["view"])

    map2 = get_map_by_name("淤浊沼泽")
    print(map2["view"])
