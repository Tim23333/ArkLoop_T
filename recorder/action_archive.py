"""Archive consumed actions together with their keyframes and semantic results.

This module supports offline debugging and replay by writing each action to a
separate folder under ``recordings/actions/<session_id>/``.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from src.logger import logger
from src.utils.image_io import write_image

__all__ = ["ActionArchive"]


@dataclass
class ActionArchive:
    """Write action items into per-session, per-action folders.

    Folder layout::

        recordings/
        └── actions/
            └── <session_id>/
                ├── 00001_click/
                │   ├── action.json
                │   ├── frame.png
                │   └── semantic.json
                └── 00002_drag/
                    ├── action.json
                    ├── frame.png
                    └── semantic.json
    """

    base_dir: Path = field(default_factory=lambda: Path("recordings/actions"))
    session_id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S"))
    archive_all: bool = False

    def __post_init__(self):
        self._counter = 0
        self._session_dir = Path(self.base_dir) / self.session_id
        self._session_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Action archive directory: {self._session_dir}")

    def _next_folder(self, action_type: str) -> Path:
        """Return a unique folder path for the next action."""
        while True:
            self._counter += 1
            folder = self._session_dir / f"{self._counter:05d}_{action_type}"
            if not folder.exists():
                return folder

    def _semantic_to_dict(self, semantic) -> Optional[Dict[str, Any]]:
        """Convert a SemanticAction to a JSON-serializable dict."""
        if semantic is None:
            return None
        out = {
            "action_type": semantic.action_type.name,
        }
        for key in ("oper", "tile_pos", "side", "direction", "raw"):
            value = getattr(semantic, key, None)
            if value is None:
                continue
            if key == "direction":
                value = value.name
            elif key == "action_type":
                value = semantic.action_type.name
            out[key] = value
        if semantic.game_time:
            out["game_time"] = dict(semantic.game_time)
        return out

    def save(
        self,
        action: Dict[str, Any],
        frame: Optional[np.ndarray],
        frame_ts: float,
        tick_state: Optional[Dict[str, Any]],
        semantic,
        final_state: Dict[str, Any],
    ) -> Optional[Path]:
        """Archive one processed action.

        Args:
            action: The raw action dict from ``ActionRecorder``.
            frame: Keyframe captured near the action time, if any.
            frame_ts: Timestamp of the keyframe.
            tick_state: Optional tick/cycle/paused snapshot at action time.
            semantic: The resulting ``SemanticAction``, or ``None``.
            final_state: Recognizer state after processing the action.

        Returns:
            The folder path if archived, or ``None`` if skipped.
        """
        action_type = action.get("type", "unknown")
        semantic_dict = self._semantic_to_dict(semantic)

        if not self.archive_all:
            if semantic is None or semantic.action_type.name == "IGNORE":
                return None

        folder = self._next_folder(action_type)
        folder.mkdir(parents=True, exist_ok=False)

        action_info = {
            "action": dict(action),
            "frame_ts": frame_ts,
            "tick_state": tick_state,
        }
        if semantic_dict is not None and semantic_dict.get("action_type") == "SELECT":
            action_info["note"] = (
                "test-only: frame captured at action time for operator "
                "selection judgment"
            )
        with open(folder / "action.json", "w", encoding="utf-8") as f:
            json.dump(action_info, f, ensure_ascii=False, indent=2)

        if frame is not None:
            write_image(folder / "frame.png", frame)

        archive_state = {
            "semantic": semantic_dict,
            "final_state": {
                "current_view": final_state.get("current_view"),
                "selected_oper": final_state.get("selected_oper"),
                "side_source": final_state.get("side_source"),
                "deployed": {
                    oper: {"row": row, "col": col}
                    for oper, (row, col) in (final_state.get("deployed") or {}).items()
                },
            },
        }
        with open(folder / "semantic.json", "w", encoding="utf-8") as f:
            json.dump(archive_state, f, ensure_ascii=False, indent=2)

        logger.debug(f"Archived action to {folder}")
        return folder
