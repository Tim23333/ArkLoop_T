"""High-level MAA recognition API for prts-plus.

This module wraps ``Tasker.post_recognition`` so callers can detect game
state, run OCR on a region, or match a template without constructing the
low-level ``J*`` dataclasses themselves.
"""

from __future__ import annotations

import json5
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
from maa.context import JRecognitionType
from maa.pipeline import JOCR, JTemplateMatch
from maa.tasker import RecognitionDetail, Tasker

from src.logger import logger
from src.maa.core import get_tasker
from src.maa.slot_layout import compute_mouse_zones, deduplicate_slot_flags
from src.config import SlotDetectionConfig as slotconfig

__all__ = ["MaaRecognizer"]


# AAO pipeline JSON node names for state detection.
_STATE_NODES = {
    "paused": "BattlePaused",
    "battle_on": "Farm@BattleOn",
    "settlement": "Farm@Settlement",
    "stars3": "Farm@Stars3",
    "stars_non3": "Farm@StarsNo3",
    "mission_failed": "Farm@MissionFailed",
    "leak_detect": "Farm@LeakDetect",
    "settings_button": "Farm@Abandon",
}
_SPEED_NODES = {
    "speed_1x": "Speed1x",
    "speed_2x": "Speed2x",
}


def _nodes_dir() -> Path:
    return Path(__file__).resolve().parent / "nodes"


def _pipeline_path() -> Path:
    return _nodes_dir() / "pipeline"


def _load_pipeline_nodes() -> Dict[str, Dict[str, Any]]:
    """Load all AAO pipeline JSON files and merge into one node dictionary."""
    merged: Dict[str, Dict[str, Any]] = {}
    for json_path in _pipeline_path().glob("*.json"):
        try:
            data = json5.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                merged.update(data)
        except Exception as e:
            logger.warning(f"Failed to load pipeline {json_path}: {e}")
    return merged


def _override_path() -> Path:
    """Return the path to the project-specific ROI override file.

    This file lives outside the MAA resource bundle so MAA's own pipeline
    loader does not attempt to parse it (it would reject duplicate keys).
    """
    return Path(__file__).resolve().parent / "prts_plus_override.json"


def _load_override() -> Dict[str, Dict[str, Any]]:
    """Load project-specific node overrides (typically corrected ROIs)."""
    path = _override_path()
    if not path.exists():
        return {}
    try:
        data = json5.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.warning(f"Failed to load override {path}: {e}")
    return {}


# Lazy-loaded pipeline node definitions.
_pipeline_cache: Optional[Dict[str, Dict[str, Any]]] = None


def _get_pipeline() -> Dict[str, Dict[str, Any]]:
    global _pipeline_cache
    if _pipeline_cache is None:
        _pipeline_cache = _load_pipeline_nodes()
    return _pipeline_cache


def _ensure_list(value: Union[str, List[str], float, List[float], None]) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _as_tuple(value: Optional[List[int]]) -> Tuple[int, int, int, int]:
    if value is None:
        return (0, 0, 0, 0)
    return tuple(int(v) for v in value)  # type: ignore[return-value]


def _node_to_template_match_param(node: Dict[str, Any]) -> JTemplateMatch:
    """Translate a pipeline TemplateMatch node into a JTemplateMatch param."""
    template = node.get("template", [])
    threshold = node.get("threshold", 0.8)
    return JTemplateMatch(
        template=_ensure_list(template),
        roi=_as_tuple(node.get("roi")),
        roi_offset=_as_tuple(node.get("roi_offset")),
        threshold=[float(t) for t in _ensure_list(threshold)],
        order_by=node.get("order_by", "Horizontal"),
        index=int(node.get("index", 0)),
        method=int(node.get("method", 5)),
        green_mask=bool(node.get("green_mask", False)),
    )


def _node_to_ocr_param(node: Dict[str, Any]) -> JOCR:
    """Translate a pipeline OCR node into a JOCR param."""
    return JOCR(
        expected=_ensure_list(node.get("expected", [])),
        roi=_as_tuple(node.get("roi")),
        roi_offset=_as_tuple(node.get("roi_offset")),
        threshold=float(node.get("threshold", 0.3)),
        replace=node.get("replace", []),
        order_by=node.get("order_by", "Horizontal"),
        index=int(node.get("index", 0)),
        only_rec=bool(node.get("only_rec", False)),
        model=str(node.get("model", "")),
        color_filter=str(node.get("color_filter", "")),
    )


def _recognize(
    tasker: Tasker,
    reco_type: JRecognitionType,
    param: Any,
    image: np.ndarray,
) -> Optional[RecognitionDetail]:
    """Run a single recognition and return its RecognitionDetail."""
    job = tasker.post_recognition(reco_type, param, image)
    detail = job.wait().get()
    if detail is None or not detail.nodes:
        return None
    return detail.nodes[0].recognition


class MaaRecognizer:
    """High-level wrapper around MAA recognition for prts-plus.

    Usage::

        from src.maa import MaaRecognizer
        from src.mumu.mumu_vision import capture_game_window

        maa = MaaRecognizer()
        img = capture_game_window(ratio=None, color=True)
        state = maa.detect_state(img)
        print(state["paused"])  # True/False
    """

    def __init__(self, tasker: Optional[Tasker] = None) -> None:
        self.tasker = tasker or get_tasker()
        self._pipeline = _get_pipeline()
        self._override = _load_override()

    def _get_node(
        self,
        node_name: str,
        pipeline_override: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return the effective node definition, applying project override."""
        node = self._pipeline.get(node_name)
        if node is None:
            return None
        node = {**node}
        if node_name in self._override:
            node.update(self._override[node_name])
        if pipeline_override:
            node.update(pipeline_override)
        return node

    def _run_node(
        self,
        node_name: str,
        image: np.ndarray,
        pipeline_override: Optional[Dict[str, Any]] = None,
    ) -> Optional[RecognitionDetail]:
        """Run a pipeline node by name (with optional override) on the given image."""
        node = self._get_node(node_name, pipeline_override)
        if node is None:
            logger.error(f"Pipeline node not found: {node_name}")
            return None

        reco_type = node.get("recognition")
        if reco_type == "TemplateMatch":
            return _recognize(
                self.tasker,
                JRecognitionType.TemplateMatch,
                _node_to_template_match_param(node),
                image,
            )
        elif reco_type == "OCR":
            return _recognize(
                self.tasker,
                JRecognitionType.OCR,
                _node_to_ocr_param(node),
                image,
            )
        else:
            logger.error(f"Unsupported recognition type '{reco_type}' for node {node_name}")
            return None

    def detect_state(self, image: np.ndarray) -> Dict[str, Any]:
        """Detect multiple game states from a single screenshot.

        Returns a dictionary like::

            {
                "paused": False,
                "speed": "1x",  # "1x", "2x", or None
                "battle_on": False,
                "settlement": False,
                "stars3": False,
                "stars_non3": False,
                "mission_failed": False,
                "leak_detect": False,
                "settings_button": False,
            }
        """
        result: Dict[str, Any] = {}

        # Speed is special: run both 1x/2x detectors and report whichever hits.
        speed_hit: Optional[str] = None
        for key, node_name in _SPEED_NODES.items():
            detail = self._run_node(node_name, image)
            if detail is not None and detail.hit:
                speed_hit = "1x" if key == "speed_1x" else "2x"
                break
        result["speed"] = speed_hit

        # All other states.
        for key, node_name in _STATE_NODES.items():
            detail = self._run_node(node_name, image)
            result[key] = detail.hit if detail is not None else False

        return result

    def detect_pause_state(self, image: np.ndarray) -> Optional[bool]:
        """Return paused/running from pause-related image nodes only.

        ``None`` means neither the paused icon nor a running speed icon could
        be confirmed. Keeping that state distinct prevents a recognition miss
        from being treated as a successful resume.
        """
        paused_detail = self._run_node(_STATE_NODES["paused"], image)
        if paused_detail is not None and paused_detail.hit:
            return True

        for node_name in _SPEED_NODES.values():
            speed_detail = self._run_node(node_name, image)
            if speed_detail is not None and speed_detail.hit:
                return False
        return None

    def ocr_region(
        self,
        image: np.ndarray,
        roi: Tuple[int, int, int, int],
        expected: Optional[Union[str, List[str]]] = None,
        threshold: float = 0.3,
        return_all: bool = False,
    ) -> Optional[Union[str, List[Dict[str, Any]]]]:
        """Run OCR on a region and return recognized text.

        Args:
            image: BGR numpy array (H, W, 3).
            roi: (x, y, w, h) region.
            expected: Optional expected text(s) to improve accuracy.
            threshold: OCR confidence threshold.
            return_all: If True, return a list of all recognized text blocks
                instead of only the best one.

        Returns:
            The best recognized text when ``return_all=False`` (default), or
            a list of dicts with ``text``, ``box`` and ``score`` for each block
            when ``return_all=True``. Returns None / empty list if nothing is
            found.
        """
        param = JOCR(
            expected=_ensure_list(expected),
            roi=roi,
            threshold=threshold,
        )
        detail = _recognize(self.tasker, JRecognitionType.OCR, param, image)
        if detail is None or not detail.hit:
            return [] if return_all else None

        if return_all:
            results = []
            for r in detail.all_results:
                text = getattr(r, "text", None)
                if text is None:
                    continue
                results.append({
                    "text": str(text).strip(),
                    "box": list(getattr(r, "box", [])) or None,
                    "score": getattr(r, "score", None),
                })
            return results

        if detail.best_result is None:
            return None
        text = getattr(detail.best_result, "text", None)
        return str(text).strip() if text else None

    def match_template(
        self,
        image: np.ndarray,
        template_path: Union[str, Path],
        roi: Optional[Tuple[int, int, int, int]] = None,
        threshold: float = 0.8,
        method: int = 5,
    ) -> Dict[str, Any]:
        """Match a template image within a region.

        Args:
            image: BGR numpy array (H, W, 3).
            template_path: Path to the template PNG (relative to bundle image/
                or absolute path).
            roi: Optional (x, y, w, h) search region. Defaults to full image.
            threshold: Match threshold.
            method: OpenCV template matching method (MAA-specific).

        Returns:
            A dictionary with ``hit`` (bool), ``box`` ([x, y, w, h] or None),
            and ``score`` (float or None).
        """
        template = str(template_path)
        param = JTemplateMatch(
            template=[template],
            roi=roi if roi is not None else (0, 0, 0, 0),
            threshold=[float(threshold)],
            method=method,
        )
        detail = _recognize(self.tasker, JRecognitionType.TemplateMatch, param, image)
        if detail is None or not detail.hit:
            return {"hit": False, "box": None, "score": None}

        box = detail.box
        score = None
        if detail.best_result is not None:
            score = getattr(detail.best_result, "score", None)
        return {"hit": True, "box": list(box) if box else None, "score": score}

    def detect_slot_flags(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """Detect all operator slot flags in the deployment area.

        This is a convenience wrapper around the AAO ``DetectSlots`` node.
        Returns a list of detected boxes::

            [{"box": [x, y, w, h], "score": 0.95}, ...]
        """
        detail = self._run_node("DetectSlots", image)
        if detail is None or not detail.hit:
            return []
        results = []
        for r in detail.all_results:
            box = getattr(r, "box", None)
            score = getattr(r, "score", None)
            if box is not None:
                results.append({"box": list(box), "score": score})
        return results

    @staticmethod
    def _parse_int(text: Optional[str]) -> Optional[int]:
        """Strictly parse OCR text as an integer."""
        if text is None:
            return None
        try:
            return int(text.strip())
        except Exception:
            return None

    def ocr_cost_in_zone(
        self,
        image: np.ndarray,
        roi: Tuple[int, int, int, int],
    ) -> Optional[str]:
        """OCR a cost number in a slot zone, with a preprocessing fallback.

        The generic MAA OCR sometimes misses small white digits on dark or
        textured card backgrounds (e.g. it sees nothing for a clearly visible
        ``11``). When the first pass does not produce a parseable integer, we
        Otsu-threshold the ROI and lightly erode it to thicken the strokes, then
        run OCR again on the processed image.

        Returns the best text string, or ``None`` if both passes fail.
        """
        text = self.ocr_region(image, roi=roi)
        if self._parse_int(text) is not None:
            return text

        px1, py1, pw, ph = roi
        if pw <= 0 or ph <= 0:
            return text

        processed = image.copy()
        zone = processed[py1 : py1 + ph, px1 : px1 + pw]
        gray = cv2.cvtColor(zone, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        eroded = cv2.erode(binary, np.ones((2, 2), np.uint8), iterations=1)
        processed[py1 : py1 + ph, px1 : px1 + pw] = cv2.cvtColor(
            eroded, cv2.COLOR_GRAY2BGR
        )
        return self.ocr_region(processed, roi=roi)

    def detect_slot_layout(
        self,
        image: np.ndarray,
        operator_area_top: float = 0.8,
        slot_width_ratio: float = 0.0927,
        rightmost_slot_right: float = 0.999,
    ) -> Optional[Dict[str, Any]]:
        """Compute operator slot boxes from MAA BattleOpersFlag + OCR validation.

        The old fixed right-aligned layout has been replaced by a flag-driven,
        mouse-zone based approach:

        1. Detect all ``BattleOpersFlag`` markers.
        2. Deduplicate flags that are horizontally too close (keep the lowest
           one; flags at exactly the same y are kept).
        3. Compute right-to-left mouse-click judgment zones.
        4. Validate each remaining flag with a small cost-number OCR; only
           slots with a parseable integer cost are kept.

        Args:
            image: BGR numpy array (H, W, 3).
            operator_area_top, slot_width_ratio, rightmost_slot_right:
                Kept for backward compatibility but no longer used.

        Returns:
            A dict with ``count`` (int) and ``boxes`` (list of normalized
            ``(left, top, right, bottom)`` boxes, ordered left-to-right), or
            None if no valid slots were found.
        """
        flags = self.detect_slot_flags(image)
        if not flags:
            return None

        h, w = image.shape[:2]
        flags = deduplicate_slot_flags(
            flags, w, h, min_x_gap=slotconfig.MIN_FLAG_X_GAP
        )
        flags, zones = compute_mouse_zones(
            flags,
            w,
            h,
            midline_offset=slotconfig.MOUSE_ZONE_MIDLINE_OFFSET,
            bottom_offset=slotconfig.MOUSE_ZONE_BOTTOM_OFFSET,
            min_x_gap=slotconfig.MIN_FLAG_X_GAP,
        )
        if not flags:
            return None

        boxes: List[Tuple[float, float, float, float]] = []

        if slotconfig.OCR_FLAG_VALIDATION:
            half_width = slotconfig.OCR_ROI_HALF_WIDTH
            top_offset = slotconfig.OCR_ROI_TOP_OFFSET
            ocr_bottom = slotconfig.OCR_ROI_BOTTOM_OFFSET

            for zone in zones:
                cx, cy = zone["cx"], zone["cy"]
                x1 = max(0.0, cx - half_width)
                y1 = max(0.0, cy + top_offset)
                x2 = max(0.0, cx + half_width)
                y2 = max(0.0, cy + ocr_bottom)

                px1, py1 = int(x1 * w), int(y1 * h)
                px2, py2 = int(x2 * w), int(y2 * h)
                box = (zone["left"], zone["top"], zone["right"], zone["bottom"])
                if px2 <= px1 or py2 <= py1:
                    logger.warning(
                        f"Slot at ({cx:.4f},{cy:.4f}) passed flag+zone checks "
                        f"but OCR ROI too small; including box anyway"
                    )
                    boxes.append(box)
                    continue

                text = self.ocr_cost_in_zone(
                    image,
                    roi=(px1, py1, px2 - px1, py2 - py1),
                )
                cost = self._parse_int(text)
                if cost is not None:
                    logger.debug(
                        f"Slot at ({cx:.4f},{cy:.4f}) OCR cost={cost}"
                    )
                else:
                    reason = "no text" if not text else f"unparseable text '{text}'"
                    logger.warning(
                        f"Slot at ({cx:.4f},{cy:.4f}) passed flag+zone checks "
                        f"but OCR failed ({reason}); including box anyway"
                    )
                boxes.append(box)
        else:
            for zone in zones:
                boxes.append(
                    (zone["left"], zone["top"], zone["right"], zone["bottom"])
                )

        if not boxes:
            return None

        return {"count": len(boxes), "boxes": boxes}
