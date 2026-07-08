from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, Dict, List

from src.cache import OPERATOR_MAPPING
from src.logger import logger


class ResourceService:
    """Map/operator/avatar resources used by the desktop UI."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self._avatar_cache: Dict[str, str] = {}

    def list_maps(self) -> List[Dict[str, str]]:
        resource_dir = self.project_root / "resource"
        code_file = resource_dir / "level_code_mapping.json"
        name_file = resource_dir / "level_name_mapping.json"
        code_map: Dict[str, str] = {}
        name_map: Dict[str, str] = {}
        try:
            with open(code_file, encoding="utf-8") as f:
                code_map = json.load(f)
        except Exception:
            pass
        try:
            with open(name_file, encoding="utf-8") as f:
                name_map = json.load(f)
        except Exception:
            pass
        filename_to_name = {v: k for k, v in name_map.items()}
        return [
            {"code": code, "name": filename_to_name.get(filename, "")}
            for code, filename in code_map.items()
        ]

    def list_operators(self) -> List[Dict[str, str]]:
        return [{"id": k, "name": k} for k in OPERATOR_MAPPING.keys()]

    def prewarm_avatars(self, limit: int = 30) -> int:
        avatar_dir = self.project_root / "resource" / "avatar"
        count = 0
        if avatar_dir.is_dir():
            for path in sorted(avatar_dir.glob("*.png"))[:limit]:
                self._file_to_data_uri(path)
                count += 1
        return count

    def get_avatar_url(self, oper: str) -> str:
        if not oper:
            return ""
        if oper in self._avatar_cache:
            return self._avatar_cache[oper]

        base = OPERATOR_MAPPING.get(oper, oper)
        avatar_dir = self.project_root / "resource" / "avatar"
        candidates = [
            avatar_dir / f"{base}.png",
            avatar_dir / f"{base}_1.png",
            avatar_dir / f"{base}_1+.png",
            avatar_dir / f"{oper}.png",
        ]
        for candidate in candidates:
            if candidate.is_file():
                url = self._file_to_data_uri(candidate)
                self._avatar_cache[oper] = url
                return url
        if avatar_dir.is_dir():
            for path in sorted(avatar_dir.glob(f"{base}*.png")):
                url = self._file_to_data_uri(path)
                self._avatar_cache[oper] = url
                return url
            for path in sorted(avatar_dir.glob(f"{oper}*.png")):
                url = self._file_to_data_uri(path)
                self._avatar_cache[oper] = url
                return url
        self._avatar_cache[oper] = ""
        return ""

    def capture_with_grid(self, map_code: str) -> str:
        try:
            import io

            import cv2
            from PIL import Image, ImageDraw, ImageFont

            from src.cache import get_map_by_code
            from src.logic.calc_view import transform_map_to_view
            from src.mumu.mumu_vision import capture_game_window

            map_data = get_map_by_code(str(map_code or "").strip())
            if not map_data:
                logger.warning(f"capture_with_grid: unknown map_code {map_code!r}")
                return ""

            frame_bgr = capture_game_window(ratio=None, color=True)
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(frame_rgb)
            draw = ImageDraw.Draw(pil_img)

            height = int(map_data.get("height", 0) or 0)
            width = int(map_data.get("width", 0) or 0)
            if height <= 0 or width <= 0:
                return self._image_to_data_uri(pil_img)

            view_positions = transform_map_to_view(map_data, side=False)
            font = None
            for candidate in (
                "C:/Windows/Fonts/arialbd.ttf",
                "C:/Windows/Fonts/arial.ttf",
                "C:/Windows/Fonts/segoeuib.ttf",
            ):
                try:
                    font = ImageFont.truetype(candidate, 18)
                    break
                except Exception:
                    continue

            img_w, img_h = pil_img.size
            for row in range(height):
                for col in range(width):
                    vx, vy = view_positions[row][col]
                    cx = int(vx * img_w)
                    cy = int(vy * img_h)
                    label = f"{chr(ord('A') + (height - 1 - row))}{col + 1}"
                    if font is not None:
                        try:
                            bbox = draw.textbbox((0, 0), label, font=font)
                            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
                        except Exception:
                            tw, th = (len(label) * 10, 16)
                    else:
                        tw, th = (len(label) * 8, 12)
                    tx = cx - tw // 2
                    ty = cy - th // 2
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            if dx or dy:
                                draw.text((tx + dx, ty + dy), label, fill=(0, 0, 0), font=font)
                    draw.text((tx, ty), label, fill=(255, 40, 40), font=font)
            return self._image_to_data_uri(pil_img)
        except Exception as exc:
            logger.exception(f"capture_with_grid failed: {exc}")
            return ""

    @staticmethod
    def _file_to_data_uri(path: Path) -> str:
        mime, _ = mimetypes.guess_type(str(path))
        mime = mime or "application/octet-stream"
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime};base64,{data}"

    @staticmethod
    def _image_to_data_uri(image: Any) -> str:
        import io

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        data = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{data}"
