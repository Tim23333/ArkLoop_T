"""Visual calibration for pause/speed button detection boxes.

Run with::

    .venv/Scripts/python -m scripts.calibrate_ui_buttons

The script captures the current game window (or loads ``debug/maa_capture.png``),
shows the two UI button detection boxes, and lets you redefine each box by
dragging the mouse.

Keys / mouse:
- Click a button label on the left, or press ``1``/``2`` to select which
  box to edit.
- Drag on the image to draw a new detection box for the selected button.
- Press ``s`` to write the new boxes to ``src/config.py``.
- Press ``r`` to reload the boxes from ``src/config.py``.
- Press ``q`` or ``Esc`` to quit.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from src.config import GameRatioConfig as ratioconfig
from src.logger import logger

try:
    from src.mumu.mumu_vision import capture_game_window
except Exception as exc:
    logger.warning(f"Cannot import capture_game_window: {exc}")
    capture_game_window = None


_CONFIG_PATH = Path(__file__).resolve().parent.parent / "src" / "config.py"
_BUTTONS: List[Tuple[str, str, Tuple[int, int, int]]] = [
    ("pause", "PAUSE_BUTTON_BOX", (0, 255, 255)),
    ("speed", "SPEED_BUTTON_BOX", (255, 0, 255)),
]


class ButtonCalibrator:
    def __init__(self, image: np.ndarray) -> None:
        self.image = image
        self.h, self.w = image.shape[:2]

        # Display scale so the window fits on a normal screen.
        self.scale = min(1.0, 1280 / self.w, 720 / self.h)
        self.display_w = int(self.w * self.scale)
        self.display_h = int(self.h * self.scale)

        self.boxes: Dict[str, Tuple[float, float, float, float]] = {
            name: getattr(ratioconfig, box_attr)
            for name, box_attr, _ in _BUTTONS
        }

        self.selected = 0  # index in _BUTTONS
        self.drawing = False
        self.drag_start_norm: Optional[Tuple[float, float]] = None
        self.drag_current_norm: Optional[Tuple[float, float]] = None

    def _to_norm(self, px: int, py: int) -> Tuple[float, float]:
        x = px / self.scale / self.w
        y = py / self.scale / self.h
        return max(0.0, min(1.0, x)), max(0.0, min(1.0, y))

    @staticmethod
    def _box_to_px(
        box: Tuple[float, float, float, float], w: int, h: int
    ) -> Tuple[int, int, int, int]:
        left, top, right, bottom = box
        return (
            int(left * w),
            int(top * h),
            int(right * w),
            int(bottom * h),
        )

    def _draw(self) -> np.ndarray:
        canvas = cv2.resize(self.image, (self.display_w, self.display_h))

        for idx, (name, box_attr, color) in enumerate(_BUTTONS):
            box = self.boxes[name]
            x1, y1, x2, y2 = self._box_to_px(box, self.display_w, self.display_h)
            thickness = 3 if idx == self.selected else 1
            cv2.rectangle(canvas, (x1, y1), (x2, y2), color, thickness)
            label = f"{idx + 1}. {name}"
            cv2.putText(
                canvas,
                label,
                (x1, y1 - 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
            )

        # Draw the live drag box if active.
        if self.drag_start_norm is not None and self.drag_current_norm is not None:
            x1, y1, x2, y2 = self._box_to_px(
                (
                    min(self.drag_start_norm[0], self.drag_current_norm[0]),
                    min(self.drag_start_norm[1], self.drag_current_norm[1]),
                    max(self.drag_start_norm[0], self.drag_current_norm[0]),
                    max(self.drag_start_norm[1], self.drag_current_norm[1]),
                ),
                self.display_w,
                self.display_h,
            )
            cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 255, 0), 2)

        # Instructions.
        lines = [
            "1/2: select box  |  drag: draw box  |  s: save  |  r: reload  |  q/esc: quit",
            f"selected: {_BUTTONS[self.selected][0]}",
        ]
        for i, line in enumerate(lines):
            cv2.putText(
                canvas,
                line,
                (10, 25 + i * 25),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 0, 255),
                2,
            )

        return canvas

    def _mouse_callback(self, event: int, x: int, y: int, *_flags) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            self.drawing = True
            self.drag_start_norm = self._to_norm(x, y)
            self.drag_current_norm = self.drag_start_norm
            return

        if event == cv2.EVENT_MOUSEMOVE and self.drawing:
            self.drag_current_norm = self._to_norm(x, y)
            return

        if event == cv2.EVENT_LBUTTONUP and self.drawing:
            self.drawing = False
            end_norm = self._to_norm(x, y)
            start_norm = self.drag_start_norm or end_norm
            name, _box_attr, _color = _BUTTONS[self.selected]
            self.boxes[name] = (
                min(start_norm[0], end_norm[0]),
                min(start_norm[1], end_norm[1]),
                max(start_norm[0], end_norm[0]),
                max(start_norm[1], end_norm[1]),
            )
            self.drag_start_norm = None
            self.drag_current_norm = None
            logger.info(f"Updated {name} box to {self.boxes[name]}")
            return

    def _reload(self) -> None:
        # Reload by re-importing. Because the module may already be loaded,
        # re-read the constants manually from the file.
        text = _CONFIG_PATH.read_text(encoding="utf-8")
        for name, box_attr, _color in _BUTTONS:
            match = re.search(rf"{box_attr}\s*=\s*\(([^)]+)\)", text)
            if match:
                values = tuple(float(v.strip()) for v in match.group(1).split(","))
                if len(values) == 4:
                    self.boxes[name] = values
        logger.info("Reloaded boxes from config")

    def _save(self) -> None:
        """Write only the detection boxes back to src/config.py.

        The point ratios used by execution scripts are left
        untouched so this calibration only affects recognition, not clicking.
        """
        text = _CONFIG_PATH.read_text(encoding="utf-8")
        for name, box_attr, _color in _BUTTONS:
            box = self.boxes[name]
            pattern = rf"{box_attr}\s*=\s*\([^)]+\)"
            replacement = f"{box_attr} = ({box[0]:.4f}, {box[1]:.4f}, {box[2]:.4f}, {box[3]:.4f})"
            if re.search(pattern, text):
                text = re.sub(pattern, replacement, text, count=1)
            else:
                # Insert after the corresponding *_BUTTON_RATIO line if missing.
                ratio_name = name.upper() + "_BUTTON_RATIO"
                text = re.sub(
                    rf"({ratio_name}\s*=\s*\([^)]+\).*\n)",
                    rf"\1    {replacement}\n",
                    text,
                    count=1,
                )

        _CONFIG_PATH.write_text(text, encoding="utf-8")
        logger.info(f"Saved updated detection boxes to {_CONFIG_PATH}")

    def run(self) -> int:
        window = "UI Button Calibration"
        cv2.namedWindow(window)
        cv2.setMouseCallback(window, self._mouse_callback)

        while True:
            canvas = self._draw()
            cv2.imshow(window, canvas)
            key = cv2.waitKey(20) & 0xFF

            if key in (ord("q"), 27):
                break
            if key == ord("s"):
                self._save()
            if key == ord("r"):
                self._reload()
            if key in (ord("1"), ord("2")):
                self.selected = int(chr(key)) - 1
                logger.info(f"Selected: {_BUTTONS[self.selected][0]}")

        cv2.destroyWindow(window)
        return 0


def _load_image() -> Optional[np.ndarray]:
    existing = Path("debug") / "maa_capture.png"
    if existing.exists():
        logger.info(f"Loading existing capture: {existing.resolve()}")
        img = cv2.imread(str(existing))
        if img is not None:
            return img
    if capture_game_window is not None:
        logger.info("Capturing game window...")
        try:
            return capture_game_window(ratio=None, color=True)
        except Exception as exc:
            logger.exception(f"Failed to capture: {exc}")
    return None


def main() -> int:
    image = _load_image()
    if image is None:
        print("无法加载截图。请确保游戏窗口可用或存在 debug/maa_capture.png")
        return 1
    return ButtonCalibrator(image).run()


if __name__ == "__main__":
    sys.exit(main())
