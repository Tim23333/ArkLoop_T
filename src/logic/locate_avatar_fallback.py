import time
from typing import Optional

from src.config import LocateAvatarFallbackConfig as fallbackconfig
from src.config import SlotDetectionConfig as slotconfig
from src.logic.action import Action
from src.logger import logger
from src.maa.recognizer import MaaRecognizer
from src.maa.slot_layout import compute_mouse_zones, deduplicate_slot_flags
from src.mumu.mumu_controller import mouseclick
from src.mumu.mumu_vision import capture_game_window
from src.utils.error_to_log import ErrorToLog


def _detect_slots(image):
    """Detect operator slot flags and compute click zones.

    Returns a list of zones ordered left-to-right, each a dict with keys:
        cx, cy: normalized center coordinates
        left, top, right, bottom: normalized zone bounds
    """
    h, w = image.shape[:2]
    maa = MaaRecognizer()
    raw_flags = maa.detect_slot_flags(image)
    if not raw_flags:
        return []

    all_flags = sorted(raw_flags, key=lambda f: f["box"][0])
    dedup_flags = deduplicate_slot_flags(all_flags, w, h, min_x_gap=slotconfig.MIN_FLAG_X_GAP)
    _, zones = compute_mouse_zones(
        dedup_flags,
        w,
        h,
        midline_offset=slotconfig.MOUSE_ZONE_MIDLINE_OFFSET,
        bottom_offset=slotconfig.MOUSE_ZONE_BOTTOM_OFFSET,
    )
    return zones


def _ocr_oper_name(image: object) -> Optional[str]:
    """OCR the operator name from the detail page.

    Uses the ROI configured in ``LocateAvatarFallbackConfig.OCR_OPER_NAME_ROI``.
    """
    maa = MaaRecognizer()
    ox, oy, ow, oh = fallbackconfig.OCR_OPER_NAME_ROI
    return maa.ocr_region(image, roi=(ox, oy, ow, oh))


def _normalize_name(name: str) -> str:
    """Strip whitespace and common OCR artifacts from a name."""
    return name.strip()


def locate_avatar_fallback(action: Action) -> None:
    """Locate operator by clicking each deployment slot and OCR-ing the detail page.

    This is used as a fallback when ``cv2.matchTemplate`` fails to find the
    operator avatar in the deployment area (e.g. due to occlusion).

    This may run during playback while the battle is still advancing, so it
    must not rely on ``perform_deploy`` pausing the game first.
    """
    if not fallbackconfig.ENABLED:
        raise ErrorToLog(f"未在待部署区找到干员{action.oper}。")

    oper_name = action.oper
    if not oper_name:
        raise ErrorToLog("action.oper is empty, cannot locate operator.")

    logger.warning(f"Template match failed for {oper_name}, trying OCR fallback...")

    # 1. Detect slot zones from the current paused frame.
    image = capture_game_window(ratio=None, color=True)
    zones = _detect_slots(image)
    if not zones:
        logger.error("OCR fallback: no deployment slots detected")
        raise ErrorToLog(f"未在待部署区找到干员{oper_name}。")

    logger.info(f"OCR fallback: detected {len(zones)} slot(s)")

    # 2. Try each slot: click -> wait -> screenshot -> OCR -> close detail.
    for i, zone in enumerate(zones):
        cx, cy = zone["cx"], zone["cy"]
        click_pos = (cx, (cy + 1.0) / 2.0)

        # Open detail page.
        logger.debug(f"OCR fallback: clicking slot {i} at ({cx:.4f}, {cy:.4f})")
        mouseclick(click_pos)
        time.sleep(fallbackconfig.DETAIL_WAIT_TIME)

        # Capture detail page.
        detail_img = capture_game_window(ratio=None, color=True)

        # OCR name.
        raw_name = _ocr_oper_name(detail_img)
        logger.debug(f"OCR fallback: slot {i} raw name = {raw_name!r}")

        # Close detail page by clicking the same slot again.
        mouseclick(click_pos)
        time.sleep(fallbackconfig.CLOSE_WAIT_TIME)

        if not raw_name:
            continue

        name = _normalize_name(raw_name)
        if name == oper_name:
            logger.info(f"OCR fallback found {oper_name} at slot {i} ({cx:.4f}, {cy:.4f})")
            action.avatar_pos = click_pos
            return

    logger.error(f"OCR fallback: could not find {oper_name} in any slot")
    raise ErrorToLog(f"未在待部署区找到干员{oper_name}。")
