"""Record and persist mouse actions synchronized to video recording."""

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.input.mouse_listener import MouseEvent, MouseListener
from src.input.coordinate_mapper import CoordinateMapper, MappedCoordinates
from src.config import InputRecordingConfig as inputconfig
from src.logger import logger

__all__ = ["ActionRecorder", "RecordedAction"]


@dataclass
class RecordedAction:
    """A semantically interpreted action from raw mouse events."""

    type: str  # "click" | "drag" | "scroll" | "unknown"
    start_ts: float
    end_ts: float
    start_ratio: Tuple[float, float]
    end_ratio: Tuple[float, float]
    button: Optional[str] = None
    raw_events: List[Dict[str, Any]] = field(default_factory=list)


class ActionRecorder:
    """
    Aggregate raw mouse events into high-level actions and export them to a
    JSON file aligned with the shared timestamp origin used by
    the live recorder).

    Step 4 aggregation is intentionally lightweight: clicks, drags and
    scrolls are detected, but mapping to ``deploy`` / ``skill`` /
    ``retreat`` is left for later offline analysis.
    """

    def __init__(
        self,
        mouse_listener: Optional[MouseListener] = None,
        mapper: Optional[CoordinateMapper] = None,
        start_ts: Optional[float] = None,
        record_moves: bool = False,
        debug: bool = False,
    ):
        self.mouse = (
            mouse_listener if mouse_listener is not None else MouseListener(record_moves=record_moves)
        )
        self.mapper = mapper if mapper is not None else CoordinateMapper()
        self.start_ts = start_ts
        self._debug = debug
        self._debug_logged_count = 0
        self._stopped = False
        self._last_mapped: Optional[MappedCoordinates] = None
        # Cache the client rect at recording start so that coordinates are
        # mapped using the same geometry that existed while capturing.  If the
        # window is moved/resized before export(), late mapping would otherwise
        # clamp every event to (1, 1) or produce nonsense ratios.
        self._client_rect: Optional[Tuple[int, int, int, int]] = None

    def start(self) -> "ActionRecorder":
        """Start recording.  If no timestamp origin is provided, use now."""
        if self.start_ts is None:
            self.start_ts = time.perf_counter()
        self._stopped = False
        self._debug_logged_count = 0
        try:
            self._client_rect = self.mapper.get_client_rect_on_screen()
        except Exception as exc:
            logger.warning(f"Could not cache MuMu client rect: {exc}")
            self._client_rect = None
        self.mouse.start()
        return self

    def stop(self) -> List[MouseEvent]:
        """Stop recording and return the captured raw events."""
        self._stopped = True
        return self.mouse.stop()

    def _map_event(self, event: MouseEvent) -> MappedCoordinates:
        """Map a screen event to normalized game coordinates.

        Uses the client rectangle captured at recording start.  Falls back to
        a live query if no cached rectangle is available.
        """
        if self._client_rect is None:
            return self.mapper.map_point(event.x, event.y, clamp=True)

        left, top, width, height = self._client_rect
        if width <= 0 or height <= 0:
            return self.mapper.map_point(event.x, event.y, clamp=True)

        client_x = event.x - left
        client_y = event.y - top
        ratio_x = client_x / width
        ratio_y = client_y / height
        valid = 0.0 <= ratio_x <= 1.0 and 0.0 <= ratio_y <= 1.0

        # Clamp just like CoordinateMapper does, so points slightly outside the
        # window still produce a usable ratio instead of invalid values.
        ratio_x = max(0.0, min(1.0, ratio_x))
        ratio_y = max(0.0, min(1.0, ratio_y))

        std_w, std_h = inputconfig.SCREEN_STANDARD_SIZE
        return MappedCoordinates(
            screen_x=event.x,
            screen_y=event.y,
            client_x=float(client_x),
            client_y=float(client_y),
            ratio_x=ratio_x,
            ratio_y=ratio_y,
            game_x=ratio_x * std_w,
            game_y=ratio_y * std_h,
            valid=valid,
        )

    def _event_to_dict(self, event: MouseEvent, event_index: int = 0) -> Dict[str, Any]:
        mapped = self._map_event(event)
        self._last_mapped = mapped
        if self._debug and event_index >= self._debug_logged_count:
            logger.info(
                f"[mouse-debug] {event.type} raw=({event.x},{event.y}) "
                f"ratio=({mapped.ratio_x:.4f},{mapped.ratio_y:.4f}) "
                f"client=({mapped.client_x:.1f},{mapped.client_y:.1f}) "
                f"valid={mapped.valid}"
            )
            self._debug_logged_count = event_index + 1
        return {
            "type": event.type,
            "ts": event.ts,
            "screen": {"x": event.x, "y": event.y},
            "client": {"x": round(mapped.client_x, 2), "y": round(mapped.client_y, 2)},
            "ratio": {
                "x": round(mapped.ratio_x, 6),
                "y": round(mapped.ratio_y, 6),
            },
            "game": {
                "x": round(mapped.game_x, 2),
                "y": round(mapped.game_y, 2),
            },
            "button": event.button,
            "pressed": event.pressed,
            "valid": mapped.valid,
            "extra": event.extra,
        }

    def _build_actions(self, raw_events: List[MouseEvent]) -> List[Dict[str, Any]]:
        """
        Lightweight aggregation: mousedown / mouseup into click or drag.
        """
        actions: List[Dict[str, Any]] = []
        pending: Optional[Dict[str, Any]] = None

        for idx, raw in enumerate(raw_events):
            ev = self._event_to_dict(raw, event_index=idx)
            if ev["type"] == "mousedown":
                pending = {
                    "type": "click",
                    "button": ev.get("button"),
                    "start_ts": ev["ts"],
                    "start_ratio": ev["ratio"],
                    "start_game": ev["game"],
                    "end_ts": ev["ts"],
                    "end_ratio": ev["ratio"],
                    "end_game": ev["game"],
                    "raw_events": [ev],
                }
            elif ev["type"] == "mouseup" and pending is not None:
                pending["end_ts"] = ev["ts"]
                pending["end_ratio"] = ev["ratio"]
                pending["end_game"] = ev["game"]
                pending["raw_events"].append(ev)

                dx = abs(pending["end_ratio"]["x"] - pending["start_ratio"]["x"])
                dy = abs(pending["end_ratio"]["y"] - pending["start_ratio"]["y"])
                distance = (dx ** 2 + dy ** 2) ** 0.5

                if distance >= inputconfig.DRAG_THRESHOLD_RATIO:
                    pending["type"] = "drag"
                    pending["drag_distance_ratio"] = distance
                actions.append(pending)
                pending = None
            elif ev["type"] in ("scroll",):
                actions.append(
                    {
                        "type": "scroll",
                        "start_ts": ev["ts"],
                        "end_ts": ev["ts"],
                        "start_ratio": ev["ratio"],
                        "end_ratio": ev["ratio"],
                        "start_game": ev["game"],
                        "end_game": ev["game"],
                        "extra": ev.get("extra", {}),
                        "raw_events": [ev],
                    }
                )

        # Only emit a pending (incomplete) action when recording has stopped.
        # While recording is still active, an unfinished mousedown simply means
        # the user is holding the button; emitting it as a click here would
        # create phantom clicks before the real mouseup arrives.
        if pending is not None and self._stopped:
            actions.append(pending)

        return actions

    def export(
        self,
        raw_events: Optional[List[MouseEvent]] = None,
        duration: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Export captured events to a dict suitable for JSON serialization."""
        if raw_events is None:
            raw_events = self.mouse.events
        actions = self._build_actions(raw_events)
        return {
            "start_ts": self.start_ts,
            "duration": duration,
            "raw_event_count": len(raw_events),
            "action_count": len(actions),
            "actions": actions,
        }

    def save(
        self,
        path: str,
        raw_events: Optional[List[MouseEvent]] = None,
        duration: Optional[float] = None,
    ) -> str:
        """Export captured events to a JSON file and return the path."""
        data = self.export(raw_events=raw_events, duration=duration)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved action recording {path}")
        return path

    def __enter__(self) -> "ActionRecorder":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
