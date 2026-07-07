"""Action semantic recognizer.

Turns the low-level ``click`` / ``drag`` actions produced by the mouse recorder
into game-level actions (deploy, skill, retreat, ignore).  It also tracks which
operator has been deployed where, so follow-up clicks on the map can be
resolved to the correct operator.

A global view-state machine is maintained because the camera is not fixed:
- The game starts in side view.
- Clicking an operator card or a deployed operator on the map enters side view.
- Deploy drags are always evaluated in side view.
- Direction selection is a follow-up drag whose start lies inside the high-ground
diamond around the just-deployed tile.

Alternatively, a ``view_detector`` callable can be supplied.  When provided,
the recognizer queries it at the start of each action to determine whether the
current frame is in side view, and the legacy state-machine transitions are
ignored.
"""

from __future__ import annotations

import bisect
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from src.cache import OPERATOR_MAPPING, get_avatars, get_unit_metadata
from src.config import GameRatioConfig as ratioconfig
from src.config import ImageProcessingConfig as imgconfig
from src.config import InputRecordingConfig as inputconfig
from src.config import DebugConfig
from src.logic.calc_view import transform_map_to_view, transform_tile_to_view, transform_view_to_map
from src.logger import logger

__all__ = [
    "ActionType",
    "DirectionType",
    "SemanticAction",
    "AvatarMatcher",
    "ActionRecognizer",
]


class ActionType(Enum):
    DEPLOY = "部署"
    DIRECTION = "方向"
    SELECT = "选中"
    SKILL = "技能"
    RETREAT = "撤退"
    IGNORE = "忽略"


class DirectionType(Enum):
    UP = "上"
    DOWN = "下"
    LEFT = "左"
    RIGHT = "右"
    NONE = "无"


# Detection regions as quadrilaterals (ratio coordinates).
# Order does not matter; convex hull is computed internally.
RETREAT_QUAD = [
    (0.432, 0.251),
    (0.503, 0.235),
    (0.430, 0.351),
    (0.504, 0.338),
]

SKILL_QUAD = [
    (0.635, 0.515),
    (0.725, 0.504),
    (0.644, 0.672),
    (0.746, 0.662),
]

# Direction selection is a diamond (45° rotated square) in tile space, centered
# on the deployed tile.  The corners are ``RADIUS`` tiles away in the four
# cardinal directions; they are mapped to the high-ground plane
# (heightType=1) via ``transform_tile_to_view`` so the diamond is independent
# of whether the current view is the front or side camera.
DIRECTION_DRAG_TILE_RADIUS = 2.5


def _make_contour(points: List[Tuple[float, float]]) -> np.ndarray:
    """Build an ordered convex contour from a list of vertices."""
    pts = np.array(points, dtype=np.float32)
    return cv2.convexHull(pts)


RETREAT_CONTOUR = _make_contour(RETREAT_QUAD)
SKILL_CONTOUR = _make_contour(SKILL_QUAD)


def _direction_drag_quad(
    map_data: Dict[str, Any],
    tile_pos: Tuple[float, float],
    side: bool,
    radius: float = DIRECTION_DRAG_TILE_RADIUS,
) -> Optional[List[Tuple[float, float]]]:
    """
    Return the screen-ratio quadrilateral for the direction-drag diamond
    around ``tile_pos``.  The four corners are ``radius`` tiles away in the
    cardinal directions, projected on the high-ground plane.  This matches
    the in-game direction UI, which is defined on the high-ground plane
    rather than on the ground plane of the selected tile.
    """
    row, col = tile_pos
    # The diamond lives on the high-ground plane.  Use the largest
    # heightType present in the map, but never lower than 1.
    high_ground_type = _high_ground_type(map_data)

    corner_tiles = [
        (row, col - radius),  # left
        (row + radius, col),  # bottom
        (row, col + radius),  # right
        (row - radius, col),  # top
    ]
    return [
        transform_tile_to_view(map_data, side, r, c, high_ground_type)
        for r, c in corner_tiles
    ]


def _high_ground_type(map_data: Dict[str, Any]) -> int:
    """Return the highest heightType in the map, clamped to at least 1."""
    return max(
        1,
        max(
            tile["heightType"]
            for row_tiles in map_data["tiles"]
            for tile in row_tiles
        ),
    )


def _operator_action_regions(
    map_data: Dict[str, Any],
    tile_pos: Tuple[float, float],
    side: bool,
    half_diag_x: float = 0.77,
    half_diag_y: float = 0.81,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[np.ndarray]]:
    """
    Return screen-ratio contours for the dead-zone diamond and the two
    action squares around ``tile_pos``.

    - Dead zone: direction-drag diamond with radius 2.7 tiles.
    - Retreat square: axis-aligned square whose diagonal lies on the
      left-top edge of the dead-zone diamond, shifted right 0.015 and up
      0.01 tiles; ``half_diag_x`` is half the diagonal length in tiles.
    - Skill square: same on the right-bottom edge, shifted left 0.01 tiles.
    """
    dead_quad = _direction_drag_quad(map_data, tile_pos, side, radius=2.7)
    if dead_quad is None:
        return None, None, None

    row, col = tile_pos
    high_ground_type = _high_ground_type(map_data)

    # Diamond corners in tile coordinates: left, bottom, right, top.
    left = (row, col - 2.7)
    bottom = (row + 2.7, col)
    right = (row, col + 2.7)
    top = (row - 2.7, col)

    def _make_square(mid_r: float, mid_c: float, half_diag: float):
        d = half_diag / math.sqrt(2)
        corners = [
            (mid_r + d, mid_c + d),
            (mid_r - d, mid_c + d),
            (mid_r - d, mid_c - d),
            (mid_r + d, mid_c - d),
        ]
        return [
            transform_tile_to_view(map_data, side, r, c, high_ground_type)
            for r, c in corners
        ]

    # Left-top edge midpoint, shifted right 0.015 and up 0.01 tiles.
    m1_r = (left[0] + top[0]) / 2.0 - 0.01
    m1_c = (left[1] + top[1]) / 2.0 + 0.015
    retreat_quad = _make_square(m1_r, m1_c, half_diag_x)

    # Right-bottom edge midpoint, shifted left 0.01 tiles.
    m2_r = (right[0] + bottom[0]) / 2.0
    m2_c = (right[1] + bottom[1]) / 2.0 - 0.01
    skill_quad = _make_square(m2_r, m2_c, half_diag_y)

    return (
        _make_contour(dead_quad),
        _make_contour(retreat_quad),
        _make_contour(skill_quad),
    )


def _unshift_click_for_selected_camera(
    map_data: Dict[str, Any],
    click_ratio: Tuple[float, float],
    selected_tile: Tuple[int, int],
    side: bool,
) -> Tuple[float, float]:
    """
    When an operator is selected the game pans the map so that the selected
    tile's center aligns with the map's geometric center.  This function
    removes that pan from ``click_ratio`` so that ``transform_view_to_map``
    can be used with the original camera matrix.
    """
    height = map_data.get("height", 0)
    width = map_data.get("width", 0)
    if height == 0 or width == 0:
        return click_ratio

    center_tile = ((height - 1) / 2.0, (width - 1) / 2.0)
    hgt = _high_ground_type(map_data)
    center_ratio = transform_tile_to_view(map_data, side, *center_tile, hgt)
    selected_ratio = transform_tile_to_view(map_data, side, *selected_tile, hgt)
    return (
        click_ratio[0] - (center_ratio[0] - selected_ratio[0]),
        click_ratio[1] - (center_ratio[1] - selected_ratio[1]),
    )


@dataclass
class SemanticAction:
    action_type: ActionType
    oper: Optional[str] = None
    tile_pos: Optional[Tuple[int, int]] = None
    side: bool = False
    direction: DirectionType = DirectionType.NONE
    game_time: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)
    overwritten_oper: Optional[str] = None
    needs_direction: bool = False

    def to_axis_dict(self, height: int) -> Dict[str, Any]:
        """Convert to the dictionary shape used by ``recorder/axis_writer.py``."""
        out: Dict[str, Any] = {
            "action_type": self.action_type.value,
        }
        if self.game_time:
            fc = self.game_time.get("frame") or self.game_time.get("total_elapsed_frames")
            if fc is not None:
                out["frame"] = int(fc)
        if self.oper is not None:
            out["oper"] = self.oper
        if self.tile_pos is not None:
            row, col = self.tile_pos
            letter = chr(ord("A") + (height - 1 - row))
            number = col + 1
            out["pos"] = f"{letter}{number}"
        if self.direction != DirectionType.NONE:
            out["direction"] = self.direction.value
        return out


class AvatarMatcher:
    """Match a patch from the operator area against known avatar templates.

    When PyTorch + CUDA is available, matching runs as a single batched
    ``F.conv2d`` over ALL templates on the GPU — one kernel instead of N
    ``cv2.matchTemplate`` calls.  Otherwise it falls back to the original
    per-template CPU loop.  The matching metric (TM_CCOEFF_NORMED) and the
    threshold semantics are identical between the two paths.
    """

    def __init__(self, threshold: float = imgconfig.TEMPLATE_MATCH_THRESHOLD):
        self.threshold = threshold
        self._templates: Optional[Dict[str, List[np.ndarray]]] = None
        # Template (kernel) size — all avatars are cropped to AVATAR_CROP_SIZE.
        self._th, self._tw = imgconfig.AVATAR_CROP_SIZE
        # GPU batched-match state (built lazily on first match / prewarm).
        self._torch = None
        self._device = None
        self._gpu_checked = False
        self._gpu_ready = False
        self._T_zm = None            # (N,1,th,tw) float32 zero-mean templates
        self._norm_t = None          # (N,1,1)   float32 sqrt(sum((T-mean)^2))
        self._ones = None            # (1,1,th,tw) ones kernel for local sums
        self._templ_owner: List[str] = []  # operator name per template index

    def _load_templates(self) -> Dict[str, List[np.ndarray]]:
        if self._templates is None:
            templates: Dict[str, List[np.ndarray]] = {}
            for oper_name in OPERATOR_MAPPING:
                try:
                    templates[oper_name] = get_avatars(oper_name)
                except Exception as exc:
                    # Non-deployable summons (trap_*) / tokens (token_*) have no
                    # avatar file and never appear in the deploy bar — the miss
                    # is expected.  load_resource logs it at DEBUG so it doesn't
                    # spam ERROR during prewarm.
                    if DebugConfig.LOG_RESOURCE_LOAD:
                        logger.debug(f"Could not load avatar for {oper_name}: {exc}")
            self._templates = templates
        return self._templates

    def prewarm(self) -> int:
        """Force template load up front (call from init_app to avoid the first
        deploy paying the full OPERATOR_MAPPING cv2.imread cost). Returns the
        number of operators with at least one template loaded.

        Also stages the GPU tensors now so the first deploy doesn't pay the
        one-time stacking/upload cost either.
        """
        templates = self._load_templates()
        if self._try_init_gpu():
            self._ensure_gpu_tensors()
        return sum(1 for v in templates.values() if v)

    # ------------------------------------------------------------------
    # GPU batched matching (PyTorch + CUDA)
    # ------------------------------------------------------------------
    def _try_init_gpu(self) -> bool:
        """Lazy-import torch and detect CUDA.  Returns True if the GPU path is usable.

        Torch is optional: a missing import or no-CPU-only wheel simply keeps
        the matcher on the original CPU loop.
        """
        if self._gpu_checked:
            return self._gpu_ready
        self._gpu_checked = True
        try:
            import torch  # noqa: F401
            self._torch = torch
            if not torch.cuda.is_available():
                logger.info("AvatarMatcher: torch present but CUDA unavailable — CPU path")
                return False
            self._device = torch.device("cuda")
            self._gpu_ready = True
            logger.info("AvatarMatcher: CUDA available — batched GPU match enabled")
        except Exception as exc:
            logger.info(f"AvatarMatcher: torch unavailable ({exc!r}) — CPU path")
        return self._gpu_ready

    def _ensure_gpu_tensors(self) -> bool:
        """Stack all templates into GPU tensors once.  Returns False if there is
        nothing to match."""
        if self._T_zm is not None:
            return True
        torch = self._torch
        if torch is None or self._device is None:
            return False
        templates = self._load_templates()
        owners: List[str] = []
        templs: List[np.ndarray] = []
        for name, tlist in templates.items():
            for t in tlist:
                if t is None or t.ndim != 2 or t.shape[0] < 1 or t.shape[1] < 1:
                    continue
                # All processed avatars are (th,tw); resize defensively in case a
                # custom/skin variant slipped through at a different size.
                if t.shape != (self._th, self._tw):
                    t = cv2.resize(t, (self._tw, self._th))
                templs.append(t)
                owners.append(name)
        if not templs:
            return False
        T = np.stack(templs).astype(np.float32)             # (N,th,tw)
        T = torch.from_numpy(T).unsqueeze(1).to(self._device)  # (N,1,th,tw)
        mean_t = T.mean(dim=(2, 3), keepdim=True)           # (N,1,1,1)
        T_zm = T - mean_t                                    # zero-mean templates
        # Shape (1,N,1,1) so it broadcasts against num=(1,N,Ho,Wo) without
        # exploding into an N×N outer product.
        self._norm_t = torch.sqrt((T_zm * T_zm).sum(dim=(2, 3))).view(1, -1, 1, 1)
        self._ones = torch.ones(
            (1, 1, self._th, self._tw), dtype=T.dtype, device=self._device
        )
        self._T_zm = T_zm
        self._templ_owner = owners
        logger.info(
            f"AvatarMatcher: {len(owners)} templates staged on {self._device} "
            f"({len(set(owners))} operators)"
        )
        return True

    def _gpu_match(self, image_gray: np.ndarray) -> Tuple[Optional[str], float]:
        """Batched TM_CCOEFF_NORMED of one image against all templates on GPU.

        Implements the exact cv2 ``TM_CCOEFF_NORMED`` formula:
            result = conv(I, T_zm) / ( sqrt(sum((I-μ_I)^2)) * sqrt(sum((T-μ_T)^2)) )
        where ``T_zm = T - mean(T)`` and the patch-window stats come from a
        local sum / sum-of-squares via a ones-kernel conv.
        """
        torch = self._torch
        if image_gray.ndim == 3:
            image_gray = cv2.cvtColor(image_gray, cv2.COLOR_BGR2GRAY)
        h, w = image_gray.shape[:2]
        if h < self._th or w < self._tw:
            return None, 0.0
        P = torch.from_numpy(image_gray.astype(np.float32))
        P = P.unsqueeze(0).unsqueeze(0).to(self._device)    # (1,1,Hp,Wp)
        k = float(self._th * self._tw)
        # Local sum / sum-of-squares over the template window (for normalization).
        S = torch.nn.functional.conv2d(P, self._ones)        # (1,1,Ho,Wo)
        S2 = torch.nn.functional.conv2d(P * P, self._ones)
        local_mean = S / k
        local_var = (S2 / k) - local_mean * local_mean
        norm_patch = torch.sqrt(local_var.clamp(min=0.0) * k)  # sqrt(sum((I-μ)^2))
        # Numerator: cross-correlation with the zero-mean templates (one batched call).
        num = torch.nn.functional.conv2d(P, self._T_zm)      # (1,N,Ho,Wo)
        denom = norm_patch * self._norm_t                    # broadcast -> (1,N,Ho,Wo)
        result = num / denom.clamp(min=1e-8)                 # TM_CCOEFF_NORMED ∈ [-1,1]
        max_per_t = result.amax(dim=(2, 3)).squeeze(0)       # (N,) best per template
        best_idx = int(max_per_t.argmax().item())
        best_score = float(max_per_t[best_idx].item())
        best_name = self._templ_owner[best_idx]
        if best_score >= self.threshold:
            return best_name, best_score
        return None, best_score

    # ------------------------------------------------------------------
    # CPU fallback (original per-template loop)
    # ------------------------------------------------------------------
    def _cpu_match(
        self, image_gray: np.ndarray, templates: Dict[str, List[np.ndarray]]
    ) -> Tuple[Optional[str], float]:
        best_name: Optional[str] = None
        best_score = -1.0
        for oper_name, avatar_list in templates.items():
            for templ in avatar_list:
                if templ.shape[0] > image_gray.shape[0] or templ.shape[1] > image_gray.shape[1]:
                    continue
                try:
                    result = cv2.matchTemplate(image_gray, templ, cv2.TM_CCOEFF_NORMED)
                except Exception:
                    continue
                _, max_val, _, _ = cv2.minMaxLoc(result)
                if max_val > best_score:
                    best_score = max_val
                    best_name = oper_name
        if best_score >= self.threshold and best_name is not None:
            return best_name, float(best_score)
        return None, float(best_score)

    # ------------------------------------------------------------------
    # Patch cropping + public match entry points
    # ------------------------------------------------------------------
    def _crop_patch(
        self, frame: np.ndarray, center_ratio: Tuple[float, float]
    ) -> np.ndarray:
        """Crop a grayscale patch centered at ``center_ratio``."""
        if frame.ndim == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame

        h, w = gray.shape[:2]
        cx = int(w * center_ratio[0])
        cy = int(h * center_ratio[1])

        tw, th = imgconfig.AVATAR_STANDARD_SIZE
        x1 = max(0, cx - tw // 2)
        y1 = max(0, cy - th // 2)
        x2 = min(w, x1 + tw)
        y2 = min(h, y1 + th)
        patch = gray[y1:y2, x1:x2]

        # Pad if the crop went over the edge.
        if patch.shape[0] < th or patch.shape[1] < tw:
            padded = np.zeros((th, tw), dtype=np.uint8)
            padded[0 : patch.shape[0], 0 : patch.shape[1]] = patch
            patch = padded
        return patch

    def match(
        self,
        frame: np.ndarray,
        center_ratio: Tuple[float, float],
    ) -> Tuple[Optional[str], float]:
        """Return ``(operator_name, score)``; name is ``None`` if below threshold."""
        patch = self._crop_patch(frame, center_ratio)
        return self.match_patch(patch)

    def _match_gray(self, image_gray: np.ndarray) -> Tuple[Optional[str], float]:
        """Run the GPU batched match when available, else the CPU loop."""
        templates = self._load_templates()
        if not templates:
            return None, 0.0
        if image_gray.ndim == 3:
            image_gray = cv2.cvtColor(image_gray, cv2.COLOR_BGR2GRAY)
        if self._try_init_gpu() and self._ensure_gpu_tensors():
            try:
                return self._gpu_match(image_gray)
            except Exception as exc:
                logger.debug(f"GPU avatar match failed, falling back to CPU: {exc!r}")
        return self._cpu_match(image_gray, templates)

    def match_patch(
        self,
        patch: np.ndarray,
    ) -> Tuple[Optional[str], float]:
        """Match a pre-cropped patch against known avatar templates."""
        return self._match_gray(patch)

    def match_slot(
        self,
        slot_image: np.ndarray,
    ) -> Tuple[Optional[str], float]:
        """Match all templates inside a single slot image and return the best.

        This is the search-style matching used by ``src/logic/locate_avatar``,
        but evaluated within one slot instead of the whole operator bar.
        Functionally identical to ``match_patch`` (both delegate to the same
        GPU/CPU path); kept as a separate name for call-site clarity.
        """
        return self._match_gray(slot_image)


class ActionRecognizer:
    """
    Convert a stream of recorded actions into semantic actions.

    Args:
        map_data: Map dict from ``get_map_by_code`` / ``get_map_by_name``.
        avatar_matcher: Optional ``AvatarMatcher`` (or any callable) used to
            resolve operator avatars during deploy drags.
        frame_provider: Callable ``timestamp -> np.ndarray`` used to fetch the
            video frame closest to an action for avatar matching.
        event_callback: Optional callback ``(event_type, **kwargs)`` invoked for
            state-machine events such as view changes, operator selections, and
            deploy cancellations.  Useful for live debugging scripts.
        view_detector: Optional callable ``image -> bool`` that returns True when
            the current frame is in side view.  When supplied, the recognizer
            queries it at the start of each action and ignores the legacy view
            state-machine transitions.
        use_slot_layout: If True (default), use MAA ``BattleOpersFlag``
            detection to compute each operator slot's bounding box, then match
            avatars within the relevant slot. This is more robust than the old
            patch-based verification around the click point.
    """

    def __init__(
        self,
        map_data: Dict[str, Any],
        avatar_matcher: Optional[AvatarMatcher] = None,
        frame_provider: Optional[Callable[[float], Optional[np.ndarray]]] = None,
        event_callback: Optional[Callable[..., None]] = None,
        view_detector: Optional[Callable[[np.ndarray], bool]] = None,
        use_slot_layout: bool = True,
    ):
        self.map_data = map_data
        self.height = map_data.get("height", 0)
        self.width = map_data.get("width", 0)
        self.avatar_matcher = avatar_matcher
        self.frame_provider = frame_provider
        self.event_callback = event_callback
        self.view_detector = view_detector
        self.use_slot_layout = use_slot_layout and frame_provider is not None
        if self.use_slot_layout:
            logger.info("Slot-layout avatar matching enabled")
        else:
            logger.info("Slot-layout avatar matching disabled")

        # State tracked across actions.
        self.deployed: Dict[str, Tuple[int, int]] = {}  # oper -> tile_pos
        self.selected_oper: Optional[str] = None  # only deployed operators

        # Global camera view state.  True = side view, False = front view.
        # When ``view_detector`` is provided, this is refreshed for every action.
        self.current_view: bool = True
        # What put us into side view: "operator" (operator card) or "deployed"
        # (operator on the map).  None when in front view.
        self.side_source: Optional[str] = None

        # A deploy that still needs direction selection.
        self.pending_deploy: Optional[SemanticAction] = None
        # All semantic actions emitted so far (used for streaming API).
        self.semantic_actions: List[SemanticAction] = []

        # Cached MAA recognizer.  Constructing one re-reads the pipeline override
        # from disk, so we build it once and reuse it for every deploy drag —
        # this is the per-deploy latency the first deploy used to pay.
        self._maa_recognizer: Optional[Any] = None

    def _detect_slot_layout(self, frame: np.ndarray) -> Optional[Dict[str, Any]]:
        """Detect operator-slot layout using MAA BattleOpersFlag."""
        if not self.use_slot_layout or frame is None:
            return None
        try:
            if self._maa_recognizer is None:
                from src.maa import MaaRecognizer
                self._maa_recognizer = MaaRecognizer()
            return self._maa_recognizer.detect_slot_layout(frame)
        except Exception as exc:
            logger.warning(f"Slot layout detection failed: {exc}")
            return None

    def _detect_slot_flags(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        """Detect raw BattleOpersFlag markers for warning visualization."""
        if frame is None:
            return []
        try:
            if self._maa_recognizer is None:
                from src.maa import MaaRecognizer
                self._maa_recognizer = MaaRecognizer()
            return self._maa_recognizer.detect_slot_flags(frame)
        except Exception:
            return []

    @staticmethod
    def _draw_recognition_warning(
        frame: np.ndarray,
        ratio: Tuple[float, float],
        layout: Optional[Dict[str, Any]],
        flags: List[Dict[str, Any]],
    ) -> np.ndarray:
        """Draw slot boxes, flag centers and the trigger click for a warning frame."""
        canvas = frame.copy()
        h, w = canvas.shape[:2]

        # Slot boxes from the recognized layout.
        for i, (left, top, right, bottom) in enumerate((layout or {}).get("boxes", [])):
            x1, y1 = int(left * w), int(top * h)
            x2, y2 = int(right * w), int(bottom * h)
            cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(
                canvas,
                f"slot{i}",
                (x1 + 2, y1 - 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                1,
                cv2.LINE_AA,
            )

        # Raw flag centers.
        for flag in flags:
            x, y, fw, fh = flag.get("box", [0, 0, 0, 0])
            cx = int((x + fw / 2.0))
            cy = int((y + fh / 2.0))
            cv2.circle(canvas, (cx, cy), 5, (0, 255, 255), -1)

        # Trigger click.
        cx = int(ratio[0] * w)
        cy = int(ratio[1] * h)
        cv2.circle(canvas, (cx, cy), 8, (0, 0, 255), -1)
        cv2.circle(canvas, (cx, cy), 8, (0, 0, 0), 2)
        label = f"click ({ratio[0]:.4f}, {ratio[1]:.4f})"
        cv2.putText(
            canvas,
            label,
            (cx + 10, cy - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )

        return canvas

    def _archive_recognition_warning(
        self,
        frame: np.ndarray,
        ratio: Tuple[float, float],
        layout: Optional[Dict[str, Any]],
        reason: str,
    ) -> Optional[Path]:
        """Save the recognition keyframe annotated with slots/flags/click.

        Only the action metadata and image are saved; no semantic result.
        """
        if not DebugConfig.SAVE_RECOGNITION_WARNINGS:
            return None

        flags = self._detect_slot_flags(frame)
        annotated = self._draw_recognition_warning(frame, ratio, layout, flags)

        session_dir = Path(DebugConfig.RECOGNITION_WARNING_DIR) / datetime.now().strftime(
            "%Y%m%d_%H%M%S_%f"
        )
        session_dir.mkdir(parents=True, exist_ok=True)

        action_info = {
            "warning": reason,
            "ratio": list(ratio),
            "timestamp": datetime.now().isoformat(),
            "slot_count": len((layout or {}).get("boxes", [])),
            "flag_count": len(flags),
        }
        with open(session_dir / "action.json", "w", encoding="utf-8") as f:
            json.dump(action_info, f, ensure_ascii=False, indent=2)

        cv2.imwrite(str(session_dir / "frame.png"), annotated)
        logger.debug(f"Archived recognition warning to {session_dir}")
        return session_dir

    def _reset_state(self) -> None:
        """Reset recognizer state before processing a new batch from scratch."""
        self.deployed.clear()
        self.selected_oper = None
        self.current_view = True
        self.side_source = None
        self.pending_deploy = None
        self.semantic_actions.clear()

    # ------------------------------------------------------------------
    # State export / import
    # ------------------------------------------------------------------
    def state_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable snapshot of the state machine."""
        pending = self.pending_deploy
        return {
            "current_view": self.current_view,
            "selected_oper": self.selected_oper,
            "side_source": self.side_source,
            "deployed": dict(self.deployed),
            "pending_deploy": self._pending_to_dict(pending),
        }

    @staticmethod
    def _pending_to_dict(pending: Optional[SemanticAction]) -> Optional[Dict[str, Any]]:
        if pending is None:
            return None
        return {
            "action_type": pending.action_type.value,
            "oper": pending.oper,
            "tile_pos": pending.tile_pos,
            "side": pending.side,
            "direction": pending.direction.value,
            "game_time": pending.game_time,
            "raw": pending.raw,
            "overwritten_oper": pending.overwritten_oper,
            "needs_direction": pending.needs_direction,
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        """Restore the state machine from a snapshot produced by ``state_dict``."""
        self.current_view = bool(state.get("current_view", True))
        self.selected_oper = state.get("selected_oper")
        self.side_source = state.get("side_source")
        deployed = state.get("deployed") or {}
        self.deployed = {
            str(k): (int(v[0]), int(v[1]))
            for k, v in deployed.items()
            if isinstance(v, (list, tuple)) and len(v) == 2
        }
        pending = state.get("pending_deploy")
        self.pending_deploy = self._pending_from_dict(pending) if pending else None
        self.semantic_actions.clear()

    @staticmethod
    def _pending_from_dict(data: Optional[Dict[str, Any]]) -> Optional[SemanticAction]:
        if not data:
            return None
        try:
            action_type = ActionType(str(data.get("action_type", "部署")))
        except ValueError:
            action_type = ActionType.DEPLOY
        try:
            direction = DirectionType(str(data.get("direction", "无")))
        except ValueError:
            direction = DirectionType.NONE
        tile_pos = data.get("tile_pos")
        if tile_pos is not None:
            tile_pos = (int(tile_pos[0]), int(tile_pos[1]))
        return SemanticAction(
            action_type=action_type,
            oper=data.get("oper"),
            tile_pos=tile_pos,
            side=bool(data.get("side", True)),
            direction=direction,
            game_time=dict(data.get("game_time", {})),
            raw=dict(data.get("raw", {})),
            overwritten_oper=data.get("overwritten_oper"),
            needs_direction=bool(data.get("needs_direction", False)),
        )

    def _emit(self, event_type: str, **kwargs: Any) -> None:
        """Emit a debug/state-machine event if a callback is attached."""
        if self.event_callback is not None:
            try:
                self.event_callback(event_type, **kwargs)
            except Exception:
                logger.exception("ActionRecognizer event callback failed")

    def _set_view(self, view: bool, source: Optional[str] = None) -> None:
        """Switch camera view and emit an event only when the view flips."""
        changed = self.current_view != view
        self.current_view = view
        self.side_source = source
        if changed:
            self._emit(
                "view_change",
                view="side" if view else "front",
                source=source,
            )

    def _update_view_from_frame(self, ts: float = 0.0) -> None:
        """Refresh ``current_view`` from the OCR detector when available."""
        if self.view_detector is None or self.frame_provider is None:
            return
        frame = self.frame_provider(ts)
        if frame is None:
            return
        self._set_view(bool(self.view_detector(frame)), source="ocr")

    def update_view(self, frame: np.ndarray) -> None:
        """Refresh ``current_view`` from an externally supplied frame.

        This lets callers run OCR view detection out-of-band (e.g. in an
        async worker) instead of blocking inside ``process_single``.
        """
        if self.view_detector is None:
            return
        if not hasattr(frame, 'ndim'):
            logger.warning(f"update_view: expected ndarray, got {type(frame).__name__}")
            return
        self._set_view(bool(self.view_detector(frame)), source="ocr")

    def _select_oper(self, oper: str, source: str) -> None:
        """Record that ``oper`` is now selected while in side view."""
        self.selected_oper = oper
        self.side_source = source
        if self.view_detector is None:
            # Legacy state machine: selecting an operator forces side view.
            self.current_view = True
        self._emit("select_oper", oper=oper, source=source)

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _in_operator_area(ratio: Tuple[float, float]) -> bool:
        left, top, right, bottom = ratioconfig.OPERATOR_AREA_RATIO
        return left <= ratio[0] <= right and top <= ratio[1] <= bottom

    @staticmethod
    def _in_map_area(ratio: Tuple[float, float]) -> bool:
        # The map is everything above the operator area, excluding UI buttons.
        return ratio[1] < ratioconfig.OPERATOR_AREA_RATIO[1]

    @staticmethod
    @staticmethod
    def _in_box(
        ratio: Tuple[float, float],
        box: Tuple[float, float, float, float],
    ) -> bool:
        """Return True if ``ratio`` lies inside the normalized ``box``.

        Box format is ``(left, top, right, bottom)`` in normalized coordinates.
        """
        x, y = ratio
        left, top, right, bottom = box
        return left <= x <= right and top <= y <= bottom

    def _is_ui_button_click(self, pos: Tuple[float, float]) -> bool:
        """Return True if ``pos`` is inside the pause or speed button box.

        The start button is intentionally NOT ignored; clicks on it may be
        recorded as part of the action sequence.
        """
        return (
            self._in_box(pos, ratioconfig.PAUSE_BUTTON_BOX)
            or self._in_box(pos, ratioconfig.SPEED_BUTTON_BOX)
        )

    def _near(
        ratio: Tuple[float, float],
        target: Tuple[float, float],
        tol: float = ratioconfig.DIRECTION_RATIO,
    ) -> bool:
        return abs(ratio[0] - target[0]) <= tol and abs(ratio[1] - target[1]) <= tol

    @staticmethod
    def _point_in_quad(
        ratio: Tuple[float, float], contour: np.ndarray
    ) -> bool:
        """Return True if ``ratio`` lies inside the given quadrilateral contour."""
        return cv2.pointPolygonTest(contour, (float(ratio[0]), float(ratio[1])), False) >= 0

    def _tile_at(
        self,
        ratio: Tuple[float, float],
        side: Optional[bool] = None,
    ) -> Tuple[Optional[Tuple[int, int]], bool]:
        """Return the nearest tile position and the view used to obtain it."""
        if side is None:
            side = self.current_view
        tile = transform_view_to_map(self.map_data, ratio, side=side)
        return tile, side

    def _tile_center_ratio(
        self,
        tile: Tuple[int, int],
        side: Optional[bool] = None,
    ) -> Tuple[float, float]:
        """Return the screen-ratio center of ``tile`` in the given view."""
        if side is None:
            side = self.current_view
        view_positions = transform_map_to_view(self.map_data, side)
        return view_positions[tile[0]][tile[1]]

    def _get_action_regions(
        self,
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[np.ndarray]]:
        """
        Return (dead_zone, retreat, skill) contours when an operator is selected.
        Returns (None, None, None) if no operator is selected.

        Because the game centers the selected operator at the map's geometric
        center, the action UI (retreat/skill/dead zone) is also centered there
        on screen.  We therefore generate the regions around the map's
        geometric center.
        """
        if self.selected_oper is None:
            return None, None, None
        # The action UI (retreat/skill/dead zone) only appears when a deployed
        # operator on the map is selected.  Operator cards in the deploy bar
        # must not activate these regions.
        if self.selected_oper not in self.deployed:
            return None, None, None
        height = self.map_data.get("height", 0)
        width = self.map_data.get("width", 0)
        if height == 0 or width == 0:
            return None, None, None
        center_tile = ((height - 1) / 2.0, (width - 1) / 2.0)
        return _operator_action_regions(
            self.map_data, center_tile, side=self.current_view
        )

    def _find_deployed_at(
        self,
        ratio: Tuple[float, float],
        side: Optional[bool] = None,
    ) -> Optional[str]:
        """Find the deployed operator whose tile is closest to ``ratio``."""
        tile, side = self._tile_at(ratio, side)
        if tile is None:
            return None
        best_oper: Optional[str] = None
        best_dist = float("inf")
        for oper, oper_tile in self.deployed.items():
            if oper_tile == tile:
                return oper
            dy = oper_tile[0] - tile[0]
            dx = oper_tile[1] - tile[1]
            dist = dx * dx + dy * dy
            if dist < best_dist:
                best_dist = dist
                best_oper = oper
        # Only accept if the neighbor is within one tile.
        if best_oper is not None and best_dist <= 2.0:
            return best_oper
        return None

    def _operator_at(
        self,
        ratio: Tuple[float, float],
        ts: float = 0.0,
    ) -> Optional[str]:
        """Identify the operator card at ``ratio`` in the operator area."""
        if self.avatar_matcher is None or not self._in_operator_area(ratio):
            return None

        frame = None
        if self.frame_provider is not None:
            frame = self.frame_provider(ts)
        if frame is None:
            return None

        return self._match_avatar_with_fallback(frame, ratio)

    def _match_avatar_with_fallback(
        self,
        frame: np.ndarray,
        ratio: Tuple[float, float],
    ) -> Optional[str]:
        """Try slot-layout matching, fall back to patch-based matching."""
        # Slot-layout based search (more robust for operator bar clicks).
        layout = self._detect_slot_layout(frame)
        if layout is not None:
            try:
                from src.maa.slot_layout import crop_slot, slot_index_at

                idx = slot_index_at(layout, ratio)
                if idx is None:
                    reason = f"点击 {ratio} 不在任何 operator slot 内"
                    logger.warning(f"部署区识别：{reason}")
                    self._archive_recognition_warning(frame, ratio, layout, reason)
                    return None
                slot_img = crop_slot(frame, layout, idx)
                if slot_img is not None:
                    oper, score = self.avatar_matcher.match_slot(slot_img)
                    logger.debug(
                        f"Slot-layout avatar match at slot {idx}: {oper} "
                        f"(score={score:.2f})"
                    )
                    if oper is not None:
                        return oper
                    reason = (
                        f"slot {idx} 已知，但模板匹配未识别出干员 "
                        f"(score={score:.2f})"
                    )
                    logger.warning(f"部署区识别：{reason}")
                    self._archive_recognition_warning(frame, ratio, layout, reason)
                    return None
            except Exception:
                logger.debug("Slot-layout avatar match failed", exc_info=True)

        # Legacy patch-based verification around the click point.
        try:
            oper, score = self.avatar_matcher.match(frame, ratio)
            logger.debug(f"Patch avatar match at {ratio}: {oper} (score={score:.2f})")
            return oper
        except Exception:
            logger.debug("Avatar matcher failed", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Avatar / direction helpers
    # ------------------------------------------------------------------
    def _recognize_avatar(self, action: Dict[str, Any]) -> Optional[str]:
        if self.avatar_matcher is None:
            return None

        # Prefer a pre-captured patch saved during recording.  This avoids the
        # mouse cursor occluding the avatar and avoids decoding the video.
        patch_path = action.get("avatar_patch")
        if patch_path and os.path.isfile(patch_path):
            try:
                patch = cv2.imread(patch_path, cv2.IMREAD_GRAYSCALE)
                if patch is not None and patch.size > 0:
                    oper, score = self.avatar_matcher.match_patch(patch)
                    logger.debug(
                        f"Avatar match from pre-captured patch {patch_path}: "
                        f"{oper} (score={score:.2f})"
                    )
                    return oper
            except Exception as exc:
                logger.warning(f"Failed to match pre-captured avatar patch: {exc}")

        start_ratio = action.get("start_ratio")
        if not start_ratio:
            return None
        ratio = (start_ratio["x"], start_ratio["y"])

        frame = None
        if self.frame_provider is not None:
            frame = self.frame_provider(action.get("start_ts", 0.0))
        if frame is None:
            return None

        return self._match_avatar_with_fallback(frame, ratio)

    @staticmethod
    def _vector_to_direction(
        start_ratio: Tuple[float, float], end_ratio: Tuple[float, float]
    ) -> DirectionType:
        """
        Convert a drag vector to a cardinal direction.

        Angles are measured clockwise from the +Y axis (up on screen):
        - 330° ~ 30°  -> UP
        - 30°  ~ 150° -> RIGHT
        - 150° ~ 210° -> DOWN
        - 210° ~ 330° -> LEFT
        """
        dx = end_ratio[0] - start_ratio[0]
        dy = end_ratio[1] - start_ratio[1]
        # -dy is the upward-pointing component because screen y grows downward.
        angle = math.degrees(math.atan2(dx, -dy))
        angle = (angle + 360.0) % 360.0

        if angle > 330.0 or angle <= 30.0:
            return DirectionType.UP
        if angle <= 150.0:
            return DirectionType.RIGHT
        if angle <= 210.0:
            return DirectionType.DOWN
        return DirectionType.LEFT

    # ------------------------------------------------------------------
    # Main recognition
    # ------------------------------------------------------------------
    def recognize(
        self,
        actions: List[Dict[str, Any]],
        frames: List[Dict[str, Any]],
    ) -> List[SemanticAction]:
        """Return a list of semantic actions, one per meaningful game action.

        State (``deployed``, ``selected_oper``, view, etc.) is **not** reset
        automatically; callers should use a fresh instance or call
        ``_reset_state()`` explicitly before a new batch.
        """
        self.semantic_actions.clear()

        # Pre-build timestamp index for nearest-frame lookup.
        frame_ts = [f.get("timestamp", 0.0) for f in frames]

        def game_time(action: Dict[str, Any]) -> Dict[str, Any]:
            ts = action.get("start_ts", action.get("ts", 0.0))
            idx = bisect.bisect_left(frame_ts, ts)
            if idx >= len(frames):
                idx = len(frames) - 1
            elif idx > 0:
                if abs(frame_ts[idx] - ts) >= abs(frame_ts[idx - 1] - ts):
                    idx -= 1
            return dict(frames[idx]) if frames else {}

        for action in actions:
            # In batch mode the view can be refreshed from an external frame
            # provider, but only for click actions.  Drags are assumed to occur
            # in side view and must not block on OCR.
            if self.view_detector is not None and action.get("type") == "click":
                ts = action.get("start_ts", action.get("ts", 0.0))
                self._update_view_from_frame(ts)
            self.process_single(action, game_time)

        return list(self.semantic_actions)

    def process_single(
        self,
        action: Dict[str, Any],
        game_time: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        view: Optional[bool] = None,
    ) -> Optional[SemanticAction]:
        """
        Process one raw action and update the internal state machine.

        This is the streaming entry point used by live test/debug scripts.
        View detection is intentionally *not* performed here; callers that use
        an OCR view detector should refresh ``current_view`` beforehand (e.g.
        via ``update_view(frame)``) and/or pass the known ``view`` value.

        Args:
            action: Raw action dict produced by ``ActionRecorder``.
            game_time: Optional callable that returns game-time metadata for
                the action timestamp.
            view: Optional explicit view override. If ``True``/``False`` the
                internal ``current_view`` is set to this value before handling
                the action. If ``None`` the existing ``current_view`` is used.

        Returns:
            The produced semantic action, or ``None`` if the action was
            consumed (e.g. a direction-selection drag) or should be ignored.
        """
        if game_time is None:
            game_time = lambda _: {}

        action_type = action.get("type")
        start_ratio = action.get("start_ratio") or {}
        start = (start_ratio.get("x", 0.0), start_ratio.get("y", 0.0))
        end_ratio = action.get("end_ratio") or {}
        end = (end_ratio.get("x", 0.0), end_ratio.get("y", 0.0))

        if view is not None:
            self._set_view(view, source="explicit")

        if action_type == "drag":
            semantic = self._handle_deploy_drag(action, start, end, game_time)
            if semantic is not None:
                self.semantic_actions.append(semantic)
                self._emit("action", semantic=semantic.to_axis_dict(self.height))
                return semantic

            # Not a deploy: try direction selection.
            semantic = self._handle_direction_drag(action, start, end, game_time)
            if semantic is not None:
                self.semantic_actions.append(semantic)
                self._emit("action", semantic=semantic.to_axis_dict(self.height))
                return semantic

            # Anything else while a deploy is pending cancels that deploy.
            if self.pending_deploy is not None:
                self._cancel_pending_deploy()
            return SemanticAction(action_type=ActionType.IGNORE, raw=action)

        if action_type == "click":
            # Pending deploy cancellation for clicks.
            if self.pending_deploy is not None:
                quad = _direction_drag_quad(
                    self.map_data,
                    self.pending_deploy.tile_pos,
                    self.pending_deploy.side,
                )
                contour = _make_contour(quad) if quad else None
                if contour is None or not self._point_in_quad(start, contour):
                    self._cancel_pending_deploy()
                    # Continue to process this click normally.
                else:
                    # A click inside the diamond does not confirm direction;
                    # consume it without cancelling the pending deploy.
                    return SemanticAction(action_type=ActionType.IGNORE, raw=action)

            semantic = self._handle_click(action, start, game_time)
            if semantic is not None and semantic.action_type != ActionType.IGNORE:
                self.semantic_actions.append(semantic)
                self._emit("action", semantic=semantic.to_axis_dict(self.height))
            return semantic

        return SemanticAction(action_type=ActionType.IGNORE, raw=action)

    def _cancel_pending_deploy(self) -> None:
        """Cancel the deploy waiting for direction and revert state."""
        if self.pending_deploy is None:
            return

        oper = self.pending_deploy.oper
        # Remove the pending DEPLOY from the output list if it is still last.
        if self.semantic_actions and self.semantic_actions[-1] is self.pending_deploy:
            self.semantic_actions.pop()

        # Remove the operator from the deployed table only if it came from this
        # pending deploy (it may have overwritten an older entry otherwise).
        if self.deployed.get(oper) == self.pending_deploy.tile_pos:
            self.deployed.pop(oper, None)

        # If the pending deploy had overwritten another operator on the same
        # tile, restore that operator because the deploy was cancelled.
        overwritten = self.pending_deploy.overwritten_oper
        if overwritten is not None and self.pending_deploy.tile_pos is not None:
            self.deployed[overwritten] = self.pending_deploy.tile_pos

        self.pending_deploy = None
        self.selected_oper = None
        # After cancelling a deploy the UI returns to the front view regardless
        # of whether an OCR view detector is in use.  The next OCR call will
        # correct this if it is still needed.
        self._set_view(False, None)
        self._emit("cancel_deploy", oper=oper)

    def _handle_deploy_drag(
        self,
        action: Dict[str, Any],
        start: Tuple[float, float],
        end: Tuple[float, float],
        game_time: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> Optional[SemanticAction]:
        """Deploy: drag starts in operator area and ends on the map."""
        if not self._in_operator_area(start):
            logger.info(f"Not a deploy drag: start {start} is outside operator area")
            return None
        if not self._in_map_area(end):
            logger.info(f"Not a deploy drag: end {end} is outside map area")
            return None

        # Deploy drags are always evaluated in side view.
        tile = transform_view_to_map(self.map_data, end, side=True)
        if tile is None:
            logger.warning(f"Deploy drag ended outside map tiles: {end}")
            return None

        oper = self._recognize_avatar(action)
        if oper is None:
            logger.warning(
                f"Deploy to tile {tile}: operator not recognized; "
                "recording as '???'"
            )
            oper = "???"

        meta = get_unit_metadata(oper)
        needs_direction = meta.get("needs_direction", False)

        # Overwrite any operator already occupying this tile, but remember who
        # was there in case the deploy is later cancelled.
        overwritten_oper: Optional[str] = None
        for other, other_tile in list(self.deployed.items()):
            if other_tile == tile:
                overwritten_oper = other
                del self.deployed[other]
                break

        semantic = SemanticAction(
            action_type=ActionType.DEPLOY,
            oper=oper,
            tile_pos=tile,
            side=True,
            direction=DirectionType.NONE,
            game_time=game_time(action),
            raw=action,
            overwritten_oper=overwritten_oper,
            needs_direction=needs_direction,
        )

        self.deployed[oper] = tile
        # Deploy drag should never leave an operator selected; selection only
        # happens by clicking a deployed unit on the map.
        self.selected_oper = None

        if needs_direction:
            self.pending_deploy = semantic
            # The deploy is not complete until the direction drag finishes.
            # Leave game_time empty; it will be filled by the DIRECTION
            # semantic action produced at direction-selection time.
            semantic.game_time = {}
        return semantic

    def _handle_direction_drag(
        self,
        action: Dict[str, Any],
        start: Tuple[float, float],
        end: Tuple[float, float],
        game_time: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> Optional[SemanticAction]:
        """
        A drag whose **start** lies inside the direction-drag diamond around
        the previously deployed tile is interpreted as a direction selection.

        Returns a DIRECTION semantic action if the drag completed a pending
        deploy, or ``None`` if it was not a direction-selection drag.
        """
        if self.pending_deploy is None:
            return None
        last = self.pending_deploy
        if last.direction != DirectionType.NONE:
            return None

        # The diamond is projected using the same side view as the deploy.
        quad = _direction_drag_quad(self.map_data, last.tile_pos, last.side)
        if quad is None:
            return None
        contour = _make_contour(quad)
        if not self._point_in_quad(start, contour):
            return None

        direction = self._vector_to_direction(start, end)
        self.pending_deploy = None
        logger.debug(f"Updated direction for {last.oper} to {direction.value}")
        return SemanticAction(
            action_type=ActionType.DIRECTION,
            oper=last.oper,
            tile_pos=last.tile_pos,
            side=last.side,
            direction=direction,
            game_time=game_time(action),
            raw=action,
        )

    def _handle_click(
        self,
        action: Dict[str, Any],
        pos: Tuple[float, float],
        game_time: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> SemanticAction:
        """Click on skill/retreat buttons, operator cards, or deployed operators."""
        # Dynamic action regions only exist when an operator is selected.
        dead_zone, retreat_contour, skill_contour = self._get_action_regions()

        # Retreat button (dynamic square when selected, otherwise ignore).
        if retreat_contour is not None and self._point_in_quad(pos, retreat_contour):
            return self._handle_retreat_click(action, pos, game_time)

        # Skill button (dynamic square when selected, otherwise ignore).
        if skill_contour is not None and self._point_in_quad(pos, skill_contour):
            return self._handle_skill_click(action, pos, game_time)

        # Dead zone: ignore clicks inside the deploy-drag diamond.
        if dead_zone is not None and self._point_in_quad(pos, dead_zone):
            return SemanticAction(action_type=ActionType.IGNORE, raw=action)

        # Pause / speed / start buttons are ignored.
        if self._is_ui_button_click(pos):
            return SemanticAction(action_type=ActionType.IGNORE, raw=action)

        # Operator card area.
        if self._in_operator_area(pos):
            oper = self._operator_at(pos, action.get("start_ts", 0.0))
            return self._handle_operator_area_click(action, oper, game_time)

        # Map area.
        if self._in_map_area(pos):
            return self._handle_map_click(action, pos, game_time)

        return SemanticAction(action_type=ActionType.IGNORE, raw=action)

    def _handle_retreat_click(
        self,
        action: Dict[str, Any],
        pos: Tuple[float, float],
        game_time: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> SemanticAction:
        oper = self.selected_oper
        if oper is None:
            return SemanticAction(action_type=ActionType.IGNORE, raw=action)

        tile_pos = self.deployed.get(oper)
        self.deployed.pop(oper, None)
        self.selected_oper = None
        self.side_source = None

        # Retreat clears the selection and returns to front view.
        self._set_view(False, None)

        return SemanticAction(
            action_type=ActionType.RETREAT,
            oper=oper,
            tile_pos=tile_pos,
            side=False,
            direction=DirectionType.NONE,
            game_time=game_time(action),
            raw=action,
        )

    def _handle_skill_click(
        self,
        action: Dict[str, Any],
        pos: Tuple[float, float],
        game_time: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> SemanticAction:
        oper = self.selected_oper
        if oper is None:
            return SemanticAction(action_type=ActionType.IGNORE, raw=action)

        tile_pos = self.deployed.get(oper)
        self.selected_oper = None
        self.side_source = None

        # Using a skill clears the selection and returns to front view.
        self._set_view(False, None)

        return SemanticAction(
            action_type=ActionType.SKILL,
            oper=oper,
            tile_pos=tile_pos,
            side=False,
            direction=DirectionType.NONE,
            game_time=game_time(action),
            raw=action,
        )

    def _handle_operator_area_click(
        self,
        action: Dict[str, Any],
        oper: Optional[str],
        game_time: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> SemanticAction:
        """Handle a click inside the bottom operator card area."""
        if oper is not None:
            logger.info(f"Operator area click recognized as: {oper}")
        else:
            logger.warning(
                f"Operator area click: could not recognize operator at "
                f"{action.get('start_ratio')}"
            )

        # Operator-area clicks are intentionally NOT recorded as "select_oper".
        # "选中干员" only refers to selecting a deployed unit on the map.
        # We only log the recognition result here for debugging.  We do NOT
        # modify selected_oper, so an existing map selection is unaffected.
        if self.view_detector is not None:
            return SemanticAction(action_type=ActionType.IGNORE, raw=action)

        if not self.current_view:
            # Front view -> enter side view.
            self._set_view(True, "operator")
            return SemanticAction(action_type=ActionType.IGNORE, raw=action)

        # Already in side view.
        if self.side_source == "operator":
            # Clicking any operator card again toggles back to front view.
            if oper is not None:
                self._set_view(False, None)
            return SemanticAction(action_type=ActionType.IGNORE, raw=action)

        # Entered side view via a deployed operator; clicking a card keeps side
        # view but the selection source becomes the operator area.
        self.side_source = "operator"
        return SemanticAction(action_type=ActionType.IGNORE, raw=action)

    def _handle_map_click(
        self,
        action: Dict[str, Any],
        pos: Tuple[float, float],
        game_time: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> SemanticAction:
        """Handle a click on the map (above the operator area)."""
        # When an operator is selected the camera pans to center it.  Map clicks
        # must be unshifted before resolving tiles.
        if self.selected_oper is not None:
            selected_tile = self.deployed.get(self.selected_oper)
            if selected_tile is not None:
                adjusted_pos = _unshift_click_for_selected_camera(
                    self.map_data, pos, selected_tile, self.current_view
                )
                tile, side = self._tile_at(adjusted_pos)
                if tile is not None:
                    oper = self._find_deployed_at(adjusted_pos, side)
                    if oper is not None and oper != self.selected_oper:
                        self._select_oper(oper, "deployed")
                        return SemanticAction(
                            action_type=ActionType.SELECT,
                            oper=oper,
                            tile_pos=self.deployed.get(oper),
                            side=self.current_view,
                            direction=DirectionType.NONE,
                            game_time=game_time(action),
                            raw=action,
                        )

                # Empty map click or same operator: deselect.
                self.selected_oper = None
                if self.view_detector is None and self.current_view:
                    self._set_view(False, None)
                return SemanticAction(action_type=ActionType.IGNORE, raw=action)

        # No operator selected: use the original behavior.
        tile, side = self._tile_at(pos)
        if tile is None:
            if self.view_detector is None and self.current_view:
                self._set_view(False, None)
                self.selected_oper = None
            return SemanticAction(action_type=ActionType.IGNORE, raw=action)

        oper = self._find_deployed_at(pos, side)
        if oper is not None:
            # When in side view via an operator card, deployed operators whose
            # tile center is far to the left (under the operator panel) cannot
            # be selected.
            if self.current_view and self.side_source == "operator":
                cx, _ = self._tile_center_ratio(self.deployed[oper], side)
                if cx < 0.370:
                    return SemanticAction(action_type=ActionType.IGNORE, raw=action)

            self._select_oper(oper, "deployed")
            return SemanticAction(
                action_type=ActionType.SELECT,
                oper=oper,
                tile_pos=self.deployed.get(oper),
                side=self.current_view,
                direction=DirectionType.NONE,
                game_time=game_time(action),
                raw=action,
            )

        # Empty map click while in side view -> front view.
        if self.view_detector is None and self.current_view:
            self._set_view(False, None)
            self.selected_oper = None
        return SemanticAction(action_type=ActionType.IGNORE, raw=action)
