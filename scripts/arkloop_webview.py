"""ArkLoop desktop entry point — PyWebview + React.

Loads the React frontend from ``ui/dist/index.html`` and exposes the action
recognition backend via ``pywebview.api``.

Usage:
    .venv\Scripts\python scripts/arkloop_webview.py
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import webview

if getattr(sys, "frozen", False):
    # PyInstaller onedir: bundled datas (ui/dist, resource, calibration,
    # Tesseract-OCR, ...) live under sys._MEIPASS (== _internal/).  User-
    # writable state (timelines/, config.json) lives next to the EXE so it
    # survives reinstalls and isn't hidden inside _internal.
    project_root = Path(sys._MEIPASS)
    user_root = Path(sys.executable).parent
else:
    project_root = Path(__file__).parent.parent
    user_root = project_root
timelines_dir = user_root / "timelines"
sys.path.insert(0, str(project_root))

from recorder.backend import ActionBackend, write_axis_json
from recorder.action_recognizer import AvatarMatcher
from src.cache import OPERATOR_MAPPING
from src.logger import logger
from src.logic.ws_time_source import DEFAULT_WS_URL, get_ws_time_source

try:
    from src.maa import create_side_view_detector
except Exception as exc:  # pragma: no cover - optional dependency
    create_side_view_detector = None  # type: ignore[assignment, misc]
    logger.warning(f"MAA side-view detector unavailable: {exc}")


class ArkLoopApi:
    """API exposed to the React frontend through pywebview."""

    def __init__(self, window: webview.Window, mouse_debug: bool = False) -> None:
        self.window = window
        self.backend: Optional[ActionBackend] = None
        self._lock = threading.Lock()
        self._avatar_cache: Dict[str, str] = {}
        # Pre-warmed resources (populated in init_app)
        self._cached_matcher: Optional[AvatarMatcher] = None
        self._cached_view_detector: Optional[Any] = None
        # Playback
        self._playback_thread: Optional[threading.Thread] = None
        self._playback_stop = threading.Event()
        self._last_playback_cycle: int = 0
        self._last_playback_state: Dict[str, Any] = {}
        self._mouse_debug = mouse_debug
        # True when the most recent playback breakpoint left the game paused
        # via the in-game cost-bar toggle. Cleared on the next start_recording
        # / start_playback so the game is resumed before fresh control starts.
        self._game_paused_by_runner = False

    # ------------------------------------------------------------------
    # Recording lifecycle
    # ------------------------------------------------------------------
    def start_recording(
        self,
        map_code: str = "1-7",
        max_tick: Optional[int] = None,
        calibration_path: Optional[str] = None,
        fake_avatar: bool = False,
        cycle_offset: int = 0,
        recognizer_state: Optional[Dict[str, Any]] = None,
        devices: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        with self._lock:
            if self.backend is not None:
                return

            # If the previous playback (or user click) paused the game via
            # the esc menu (wait_until_threshold's esc() call), close that
            # menu so recording observes a live game rather than a frozen
            # menu overlay. perform_action's first action handles this for
            # playback resume, but recording has no equivalent indirection.
            if self._game_paused_by_runner:
                try:
                    from src.mumu.mumu_controller import esc as game_esc
                    game_esc()
                    logger.info("[recording] dismissed pause menu from breakpoint")
                except Exception as exc:
                    logger.warning(f"Failed to dismiss pause menu: {exc}")
                self._game_paused_by_runner = False

            self.backend = ActionBackend(
                map_code=map_code,
                max_tick=max_tick,
                calibration_path=Path(calibration_path) if calibration_path else None,
                event_callback=self._on_backend_event,
                fake_avatar=fake_avatar,
                cycle_offset=int(cycle_offset or 0),
                recognizer_state=recognizer_state,
                devices=devices,
                _matcher=self._cached_matcher,
                _view_detector=self._cached_view_detector,
                mouse_debug=self._mouse_debug,
            ).start()
            logger.info(
                f"[recording] started cycle_offset={cycle_offset} "
                f"recognizer_state={recognizer_state} devices={devices}"
            )

    def stop_recording(self) -> List[Dict[str, Any]]:
        with self._lock:
            if self.backend is None:
                return []
            axis = self.backend.stop()
            self.backend = None
        self._push_event("axis", axis)
        return axis

    def pause_recording(self) -> Dict[str, int]:
        """Stop the recorder and return the (cycle, tick) at pause time.

        Used by the frontend Pause button: caller then sets cycle_offset for
        the next session.  Emits a 'paused' event so the UI can also pick it
        up out-of-band.
        """
        with self._lock:
            if self.backend is None:
                return {"cycle": 0, "tick": 0}
            gt = self.backend.latest_game_time or {"cycle": 0, "tick": 0}
            axis = self.backend.stop()
            self.backend = None
        cycle = int(gt.get("cycle", 0))
        tick = int(gt.get("tick", 0))
        self._push_event("axis", axis)
        self._push_event("paused", {"source": "recording", "cycle": cycle, "tick": tick})
        return {"cycle": cycle, "tick": tick, "axis": axis}

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            if self.backend is None:
                return {}
            return self.backend.latest_state

    def get_axis(self) -> List[Dict[str, Any]]:
        with self._lock:
            if self.backend is None:
                return []
            return self.backend.get_axis()

    def save_axis(self, path: str) -> bool:
        with self._lock:
            if self.backend is None:
                return False
            try:
                axis = self.backend.get_axis()
                write_axis_json(
                    axis_actions=axis,
                    map_code=self.backend.map_code,
                    max_tick=self.backend.max_tick,
                    output_path=Path(path),
                    map_name=self.backend.map_data.get("name"),
                )
                return True
            except Exception as exc:
                logger.exception(f"Failed to save axis: {exc}")
                return False

    def list_calibrations(self) -> List[str]:
        calibration_dir = project_root / "calibration"
        if not calibration_dir.is_dir():
            return []
        return [
            str(p.relative_to(project_root))
            for p in calibration_dir.glob("*.json")
        ]

    def get_calibration_info(self, path: str) -> Dict[str, Any]:
        """Return metadata from a calibration JSON file.

        Reads the calibration file relative to the project root and returns
        the total frame count along with screen dimensions. Returns zeros on
        error so the frontend can fall back to a default tick count.
        """
        try:
            target = (project_root / path.strip().replace("\\", "/")).resolve()
            if target.parent.resolve() != (project_root / "calibration").resolve():
                logger.warning(f"Rejected calibration outside calibration dir: {path}")
                return {"total_frames": 0, "screen_width": 0, "screen_height": 0}
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            profiles = data.get("profiles", [])
            total = 0
            if profiles and isinstance(profiles, list):
                total = profiles[0].get("total_frames", 0) or 0
            return {
                "total_frames": int(total),
                "screen_width": int(data.get("screen_width", 0) or 0),
                "screen_height": int(data.get("screen_height", 0) or 0),
            }
        except Exception as exc:
            logger.exception(f"Failed to read calibration info for {path}: {exc}")
            return {"total_frames": 0, "screen_width": 0, "screen_height": 0}

    def capture_with_grid(self, map_code: str) -> str:
        """Capture a MuMu screenshot and overlay chess-style tile labels.

        Each tile center in the **front view** is annotated in red with its
        chess label (e.g. ``A6``). The returned value is a PNG data URI ready
        to drop into an ``<img src>``. Empty string on failure.
        """
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
                logger.warning(
                    f"capture_with_grid: map {map_code} has invalid size "
                    f"({width}x{height})"
                )
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"

            view_positions = transform_map_to_view(map_data, side=False)

            # Bold red text large enough to be legible against busy screenshots.
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
                    letter = chr(ord("A") + (height - 1 - row))
                    number = col + 1
                    label = f"{letter}{number}"
                    # Measure for centering — fall back to a constant offset
                    # if the font wasn't loaded.
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
                    # Black outline for readability over any background.
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            if dx == 0 and dy == 0:
                                continue
                            draw.text((tx + dx, ty + dy), label, fill=(0, 0, 0), font=font)
                    draw.text((tx, ty), label, fill=(255, 40, 40), font=font)

            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
        except Exception as exc:
            logger.exception(f"capture_with_grid failed: {exc}")
            return ""

    def get_avatar_url(self, oper: str) -> str:
        """Return a data URI for an operator avatar, or empty string."""
        if not oper:
            return ""
        if oper in self._avatar_cache:
            return self._avatar_cache[oper]

        base = OPERATOR_MAPPING.get(oper, oper)
        avatar_dir = project_root / "resource" / "avatar"
        # Prefer exact/base match, then any skin variant.
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
        # Fallback to any file starting with the resolved base name.
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

    @staticmethod
    def _file_to_data_uri(path: Path) -> str:
        """Read a local file and return a base64 data URI."""
        mime, _ = mimetypes.guess_type(str(path))
        mime = mime or "application/octet-stream"
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime};base64,{data}"

    def init_app(self) -> dict:
        """Initialize app resources (avatar cache, MAA, directories). Called once on startup."""
        try:
            timelines_dir.mkdir(parents=True, exist_ok=True)

            # Start the WebSocket time source using the URL configured in
            # config.json (time_source.ws_url).  This is the sole game-time
            # provider for both recording and playback; cost-bar detection is
            # retired.  Started here so the feed is live before any record /
            # playback session, and the UI can show connection status.
            try:
                cfg_path = user_root / "config.json"
                ws_url = DEFAULT_WS_URL
                if cfg_path.is_file():
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    ts_cfg = cfg.get("time_source") or {}
                    if isinstance(ts_cfg, dict) and ts_cfg.get("ws_url"):
                        ws_url = str(ts_cfg["ws_url"])
                ws = get_ws_time_source()
                ws.start(url=ws_url)
                logger.info(f"WS time source started (url={ws_url})")
            except Exception as exc:
                logger.warning(f"Failed to start WS time source: {exc}")

            # Pre-warm avatar data URIs
            avatar_dir = project_root / "resource" / "avatar"
            count = 0
            if avatar_dir.is_dir():
                for p in sorted(avatar_dir.glob("*.png"))[:30]:
                    self._file_to_data_uri(p)
                    count += 1

            # Pre-warm avatar matcher.  Constructing it only sets
            # ``_templates = None``; the actual cv2.imread of every operator
            # template is lazy — happens on the first match() call, which is
            # the first deploy.  Forcing prewarm() here pays that cost at
            # startup so the first deploy lands on the timeline without lag.
            try:
                self._cached_matcher = AvatarMatcher()
                n = self._cached_matcher.prewarm()
                logger.info(f"Avatar matcher pre-warmed ({n} operators)")
            except Exception as exc:
                logger.warning(f"Avatar matcher pre-warm failed: {exc}")

            # Pre-warm MAA side-view detector.  Constructor only builds a
            # closure; the underlying OCR engine doesn't load until the first
            # call.  Capture a single frame and invoke the detector once so
            # the first deploy's view check is fast.
            if create_side_view_detector is not None:
                try:
                    self._cached_view_detector = create_side_view_detector()
                    try:
                        from src.mumu.mumu_vision import capture_game_window
                        warm_frame = capture_game_window(ratio=None, color=True)
                        self._cached_view_detector(warm_frame)
                        logger.info("MAA side-view detector pre-warmed (OCR engine loaded)")
                    except Exception as exc:
                        logger.debug(f"Side-view OCR warm call skipped: {exc}")
                        logger.info("MAA side-view detector constructed (OCR will warm lazily)")
                except Exception as exc:
                    logger.warning(f"MAA pre-warm failed: {exc}")

            # Pre-warm the MAA slot-layout recognition node by running one
            # detection on a live frame.  The first ``post_recognition`` call is
            # the slow one; doing it here means the first in-game deploy commits
            # to the axis without the cold-start lag.
            try:
                from src.maa import MaaRecognizer
                from src.mumu.mumu_vision import capture_game_window
                frame = capture_game_window(ratio=None, color=True)
                MaaRecognizer().detect_slot_layout(frame)
                logger.info("MAA slot-layout detection pre-warmed")
            except Exception as exc:
                logger.debug(f"Slot-layout pre-warm skipped: {exc}")

            return {"ok": True, "avatars_loaded": count}
        except Exception as exc:
            logger.exception(f"init_app error: {exc}")
            return {"ok": False, "error": str(exc)}

    def create_timeline(self) -> str:
        """Create an empty timeline file with a timestamp name. Returns the file name."""
        from datetime import datetime
        name = f"timeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            timelines_dir.mkdir(parents=True, exist_ok=True)
            path = timelines_dir / name
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"settings": {}, "actions": []}, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.exception(f"Failed to create timeline: {exc}")
        return name

    def save_timeline(self, name: str, actions: list, settings: dict) -> bool:
        """Save (or overwrite) a timeline in the timelines/ folder."""
        try:
            timelines_dir.mkdir(parents=True, exist_ok=True)
            safe = name.strip().replace("/", "_").replace("\\", "_")
            if not safe.endswith(".json"):
                safe += ".json"
            path = timelines_dir / safe
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"settings": settings, "actions": actions}, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.exception(f"Failed to save timeline {name}: {exc}")
            return False

    def delete_timeline(self, name: str) -> bool:
        """Delete a timeline file."""
        try:
            path = (timelines_dir / name.strip()).resolve()
            if path.parent.resolve() != timelines_dir.resolve():
                logger.warning(f"Rejected delete outside timelines dir: {name}")
                return False
            if path.is_file():
                path.unlink()
                return True
        except Exception as exc:
            logger.exception(f"Failed to delete timeline {name}: {exc}")
        return False

    def duplicate_timeline(self, name: str) -> str:
        """Copy an existing timeline file to ``<stem>(N).json``.

        ``N`` starts at 1 and increments until the candidate file name is
        free. Returns the created file's name, or '' on failure.
        """
        try:
            src_path = (timelines_dir / name.strip()).resolve()
            if src_path.parent.resolve() != timelines_dir.resolve() or not src_path.is_file():
                return ""
            stem = src_path.stem
            # Strip any existing "(N)" suffix so duplicates of duplicates
            # don't snowball into "name(1)(1)(1)".
            import re
            base = re.sub(r"\((\d+)\)$", "", stem).rstrip()
            n = 1
            while True:
                candidate = timelines_dir / f"{base}({n}).json"
                if not candidate.exists():
                    break
                n += 1
            # Read+write rather than file copy so we preserve the JSON shape
            # (and pick up any normalization the loader would apply).
            with open(src_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            with open(candidate, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return candidate.name
        except Exception as exc:
            logger.exception(f"Failed to duplicate timeline {name}: {exc}")
            return ""

    def rename_timeline(self, old_name: str, new_name: str) -> str:
        """Rename a timeline file; returns the actual new file name."""
        try:
            old_path = (timelines_dir / old_name.strip()).resolve()
            if old_path.parent.resolve() != timelines_dir.resolve():
                return old_name
            safe = new_name.strip().replace("/", "_").replace("\\", "_")
            if not safe.endswith(".json"):
                safe += ".json"
            new_path = timelines_dir / safe
            # Avoid collision
            stem, counter = new_path.stem, 1
            while new_path.exists() and new_path.resolve() != old_path:
                new_path = timelines_dir / f"{stem}_{counter}.json"
                counter += 1
            if old_path.is_file():
                old_path.rename(new_path)
            return new_path.name
        except Exception as exc:
            logger.exception(f"Failed to rename timeline: {exc}")
            return old_name

    def get_app_config(self) -> Dict[str, Any]:
        """Return the contents of ``config.json`` (MuMu install path etc.).

        Returns ``{}`` on read failure so the UI can still render its form.
        """
        try:
            cfg_path = user_root / "config.json"
            if cfg_path.is_file():
                with open(cfg_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as exc:
            logger.warning(f"Failed to read config.json: {exc}")
        return {}

    def get_ws_status(self) -> Dict[str, Any]:
        """Return the WebSocket time source connection status + latest reading."""
        try:
            return get_ws_time_source().status()
        except Exception as exc:
            logger.warning(f"get_ws_status failed: {exc}")
            return {"connected": False, "url": DEFAULT_WS_URL}

    def restart_ws_source(self, url: Optional[str] = None) -> bool:
        """(Re)start the WS time source with a new URL.

        Called after the user edits the WS URL in Settings.  Persists the URL
        to config.json so it survives restarts, then reconnects immediately.
        """
        try:
            if url:
                clean = url.strip()
                if clean:
                    # Persist to config.json so init_app picks it up next launch.
                    self.update_app_config({"time_source": {"ws_url": clean}})
                    get_ws_time_source().start(url=clean)
                    logger.info(f"WS time source restarted (url={clean})")
                    return True
            # No URL: just restart with whatever is in config / default.
            get_ws_time_source().start()
            return True
        except Exception as exc:
            logger.exception(f"restart_ws_source failed: {exc}")
            return False

    def update_app_config(self, patch: Dict[str, Any]) -> bool:
        """Deep-merge ``patch`` into ``config.json`` and persist.

        Settings only apply to NEW captures: the MuMu DLL handle is cached
        the first time ``capture_game_window`` runs, so a path change here
        needs an app restart to take effect. The UI is expected to warn.
        """
        try:
            cfg_path = user_root / "config.json"
            current: Dict[str, Any] = {}
            if cfg_path.is_file():
                with open(cfg_path, "r", encoding="utf-8") as f:
                    current = json.load(f)

            def _merge(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
                for k, v in src.items():
                    if isinstance(v, dict) and isinstance(dst.get(k), dict):
                        _merge(dst[k], v)
                    else:
                        dst[k] = v

            _merge(current, patch or {})
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(current, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.exception(f"Failed to update config.json: {exc}")
            return False

    def list_timeline_presets(self) -> List[Dict[str, Any]]:
        """Return saved new-timeline presets in insertion order."""
        try:
            meta = timelines_dir / ".meta.json"
            if meta.is_file():
                with open(meta, "r", encoding="utf-8") as f:
                    data = json.load(f)
                raw = data.get("presets", []) or []
                # Normalize so the frontend always sees {name, settings}.
                result: List[Dict[str, Any]] = []
                for entry in raw:
                    if isinstance(entry, dict) and "name" in entry:
                        result.append({
                            "name": str(entry["name"]),
                            "settings": entry.get("settings", {}) or {},
                        })
                return result
        except Exception as exc:
            logger.warning(f"Failed to read presets: {exc}")
        return []

    def save_timeline_preset(self, name: str, settings: Dict[str, Any]) -> bool:
        """Insert or replace a preset by name. Persists to .meta.json."""
        clean = (name or "").strip()
        if not clean:
            return False
        try:
            timelines_dir.mkdir(parents=True, exist_ok=True)
            meta = timelines_dir / ".meta.json"
            data: Dict[str, Any] = {}
            if meta.is_file():
                with open(meta, "r", encoding="utf-8") as f:
                    data = json.load(f)
            presets = data.get("presets", []) or []
            presets = [p for p in presets if not (isinstance(p, dict) and p.get("name") == clean)]
            presets.append({"name": clean, "settings": settings or {}})
            data["presets"] = presets
            with open(meta, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.exception(f"Failed to save preset {name}: {exc}")
            return False

    def delete_timeline_preset(self, name: str) -> bool:
        """Remove a preset by name."""
        clean = (name or "").strip()
        if not clean:
            return False
        try:
            meta = timelines_dir / ".meta.json"
            if not meta.is_file():
                return False
            with open(meta, "r", encoding="utf-8") as f:
                data = json.load(f)
            presets = data.get("presets", []) or []
            before = len(presets)
            presets = [p for p in presets if not (isinstance(p, dict) and p.get("name") == clean)]
            if len(presets) == before:
                return False
            data["presets"] = presets
            with open(meta, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.exception(f"Failed to delete preset {name}: {exc}")
            return False

    def get_pinned_timelines(self) -> list:
        """Return list of pinned timeline names."""
        try:
            meta = timelines_dir / ".meta.json"
            if meta.is_file():
                with open(meta, "r", encoding="utf-8") as f:
                    return json.load(f).get("pinned", [])
        except Exception:
            pass
        return []

    def set_pinned_timelines(self, pinned: list) -> bool:
        """Persist the pinned timelines list."""
        try:
            timelines_dir.mkdir(parents=True, exist_ok=True)
            meta = timelines_dir / ".meta.json"
            data: dict = {}
            if meta.is_file():
                with open(meta, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data["pinned"] = pinned
            with open(meta, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.exception(f"Failed to save pinned: {exc}")
            return False

    def list_timelines(self) -> List[str]:
        """Return timeline JSON file names, newest first."""
        if not timelines_dir.is_dir():
            return []
        files = [p for p in timelines_dir.glob("*.json") if not p.name.startswith(".")]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [p.name for p in files]

    def list_maps(self) -> List[Dict[str, str]]:
        """Return [{code, name}] for all known maps."""
        resource_dir = project_root / "resource"
        code_file = resource_dir / "level_code_mapping.json"
        name_file = resource_dir / "level_name_mapping.json"
        code_map: Dict[str, str] = {}
        name_map: Dict[str, str] = {}
        try:
            with open(code_file, encoding="utf-8") as f:
                code_map = json.load(f)  # {code: filename}
        except Exception:
            pass
        try:
            with open(name_file, encoding="utf-8") as f:
                name_map = json.load(f)  # {name: filename}
        except Exception:
            pass
        # Build reverse: filename -> name
        filename_to_name: Dict[str, str] = {v: k for k, v in name_map.items()}
        result: List[Dict[str, str]] = []
        for code, filename in code_map.items():
            result.append({"code": code, "name": filename_to_name.get(filename, "")})
        return result

    def list_operators(self) -> List[Dict[str, str]]:
        """Return all operators as [{id, name}] for the search dialog."""
        return [{"id": k, "name": k} for k in OPERATOR_MAPPING.keys()]

    def start_playback(
        self,
        name: str,
        autoenter: bool = False,
        cycle_offset: int = 0,
        breakpoints: Optional[List[Dict[str, int]]] = None,
        calibration_path: Optional[str] = None,
    ) -> bool:
        """Start playing a timeline file in a background thread.

        ``cycle_offset`` shifts where in the timeline playback starts (resume
        from pause).  ``breakpoints`` is a list of {cycle, tick} dicts — the
        runner pauses the game and stops when reaching one.
        """
        if self._playback_thread is not None and self._playback_thread.is_alive():
            return False
        path = timelines_dir / name.strip()
        if not path.is_file():
            return False

        from src.axis.json_loader import load_axis_from_json
        try:
            actions, _settings = load_axis_from_json(str(path))
        except Exception as exc:
            logger.exception(f"Failed to parse axis for playback: {exc}")
            return False

        if calibration_path:
            _settings = {**_settings, "calibration_path": calibration_path}

        bp_tuples: List[tuple] = []
        for bp in breakpoints or []:
            try:
                bp_tuples.append((int(bp.get("cycle", 0)), int(bp.get("tick", 0))))
            except (TypeError, ValueError):
                continue

        from src.axis.axis_runner import AxisRunner
        self._playback_stop.clear()
        # The runner's first perform_action will toggle pause itself; clear
        # the flag so a subsequent start_recording doesn't double-toggle.
        self._game_paused_by_runner = False
        cycle_offset_int = int(cycle_offset or 0)

        # A fresh playback (no resume offset) starts from a clean slate so a
        # stale deployed set from a previous timeline/run can't leak in. A
        # resume (offset > 0) carries the deployed set forward so operators
        # placed in earlier segments stay known.
        if cycle_offset_int <= 0:
            self._last_playback_state = {}
        seed_state = dict(self._last_playback_state) if cycle_offset_int > 0 else None

        # The runner reads (cycle, tick) from the screen at high frequency inside
        # its timing-critical loop.  The callback it invokes must be cheap, so it
        # only stores the latest value; a separate low-rate publisher thread ships
        # it to the UI.  This keeps webview I/O out of the frame-stepping hot loop.
        #
        # The runner emits cycle directly from PlaybackTimeSource (cost-bar wrap
        # counter) — no wrap counting needed here.
        gt_holder = {"cycle": 0, "tick": 0}
        gt_lock = threading.Lock()
        gt_changed = threading.Event()

        def _on_game_time(cycle: int, tick: int) -> None:
            # cycle from runner is the in-game wrap counter (offset-subtracted
            # from the perspective of perform_action); bias it back to the
            # timeline cycle the UI shows.
            with gt_lock:
                gt_holder["cycle"] = cycle + cycle_offset_int
                gt_holder["tick"] = tick
            gt_changed.set()

        def _publisher(stop: threading.Event) -> None:
            last: Optional[tuple] = None
            while not stop.is_set():
                if not gt_changed.wait(0.1):
                    continue
                gt_changed.clear()
                with gt_lock:
                    cur = (gt_holder["cycle"], gt_holder["tick"])
                if cur != last:
                    self._push_event("game_time", {"cycle": cur[0], "tick": cur[1]})
                    last = cur

        def _run() -> None:
            pub_stop = threading.Event()
            pub_thread = threading.Thread(target=_publisher, args=(pub_stop,), daemon=True)
            pub_thread.start()
            try:
                def _on_runner_pause(cycle: int, tick: int) -> None:
                    # Breakpoint fired — runner already paused the game.
                    # Remember so the next start_recording knows to resume it.
                    self._game_paused_by_runner = True
                    state = runner.get_state() if runner is not None else {}
                    logger.info(
                        f"[playback] breakpoint pause cycle={cycle} tick={tick} "
                        f"state={state}"
                    )
                    self._push_event(
                        "paused",
                        {"source": "playback", "cycle": cycle, "tick": tick, "state": state},
                    )

                runner = AxisRunner(
                    actions=actions,
                    settings=_settings,
                    # Wire stop_event into is_paused so the runner aborts
                    # bullet-time / frame-step inner loops immediately on Stop,
                    # not just at action boundaries.
                    is_paused=self._playback_stop.is_set,
                    autoenter=autoenter,
                    show_error=lambda msg: logger.error(f"Playback error: {msg}"),
                    tick_callback=_on_game_time,
                    stop_event=self._playback_stop,
                    cycle_offset=cycle_offset_int,
                    breakpoints=bp_tuples,
                    on_pause=_on_runner_pause,
                    initial_state=seed_state,
                )
                runner.run()
            except Exception as exc:
                logger.exception(f"Playback error: {exc}")
            finally:
                pub_stop.set()
                with gt_lock:
                    cur = (gt_holder["cycle"], gt_holder["tick"])
                self._last_playback_cycle = int(cur[0])
                if runner is not None:
                    self._last_playback_state = runner.get_state()
                # Reaching the end of the timeline is treated like a pause: the
                # carried-forward state and cycle are KEPT so the user can keep
                # recording / playing onward from where the run finished. State
                # is only cleared by an explicit reset (timeline switch / red ■).
                logger.info(
                    f"[playback] ended cycle={self._last_playback_cycle} "
                    f"state={self._last_playback_state}"
                )
                self._push_event("game_time", {"cycle": cur[0], "tick": cur[1]})
                self._push_event(
                    "playback_done",
                    {"cycle": cur[0], "state": self._last_playback_state},
                )

        self._playback_thread = threading.Thread(target=_run, daemon=True)
        self._playback_thread.start()
        return True

    def reset_playback_state(self) -> None:
        """Forget the carried-forward deployed/recognizer state.

        Called when the user starts over: switching to a different timeline or
        fully stopping playback (the red ■ button). Clears the snapshot that
        a *resume* would otherwise inherit, so the next fresh session begins
        with an empty deployed set.
        """
        self._last_playback_state = {}
        self._last_playback_cycle = 0

    def stop_playback(self, reset_state: bool = True) -> None:
        """Stop a running playback.

        ``reset_state`` clears the carried-forward state (full stop / red ■).
        Pause passes ``reset_state=False`` so a subsequent resume can inherit
        the deployed set.
        """
        self._playback_stop.set()
        if self._playback_thread is not None:
            self._playback_thread.join(timeout=2.0)
            self._playback_thread = None
        if reset_state:
            self.reset_playback_state()

    def pause_playback(self) -> Dict[str, int]:
        """Stop playback and return the last known cycle — frontend
        uses it to set cycle_offset for resume. Emits 'paused' event too."""
        # Reuse stop_playback but keep the carried-forward state: the latest
        # game_time was already pushed during the runner's finally block and
        # the cycle/state saved.
        self.stop_playback(reset_state=False)
        logger.info(
            f"[playback] paused cycle={self._last_playback_cycle} "
            f"state={self._last_playback_state}"
        )
        self._push_event(
            "paused",
            {
                "source": "playback",
                "cycle": self._last_playback_cycle,
                "state": self._last_playback_state,
            },
        )
        return {"ok": True}

    def append_to_timeline(self, name: str, new_actions: list) -> bool:
        """Append actions to an existing timeline file (used after resume-record).

        Assumes ``new_actions`` already carry the correct (offset-biased) cycle
        values — the recorder backend handles that when started with
        ``cycle_offset > 0``.
        """
        try:
            path = (timelines_dir / name.strip()).resolve()
            if path.parent.resolve() != timelines_dir.resolve() or not path.is_file():
                return False
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            existing = data.get("actions", [])
            existing.extend(new_actions or [])
            data["actions"] = existing
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.exception(f"Failed to append to timeline {name}: {exc}")
            return False

    def save_breakpoints(self, name: str, breakpoints: list) -> bool:
        """Persist breakpoints into a timeline's settings."""
        try:
            path = (timelines_dir / name.strip()).resolve()
            if path.parent.resolve() != timelines_dir.resolve() or not path.is_file():
                return False
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            settings = data.get("settings", {}) or {}
            settings["breakpoints"] = breakpoints or []
            data["settings"] = settings
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.exception(f"Failed to save breakpoints for {name}: {exc}")
            return False

    def load_timeline(self, name: str) -> Dict[str, Any]:
        """Load a timeline JSON from the timelines folder."""
        path = timelines_dir / name
        if not path.is_file():
            return {"settings": {}, "actions": []}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            actions = data.get("actions", [])
            for action in actions:
                if "cycle" not in action:
                    action["cycle"] = 0
            return {
                "settings": data.get("settings", {}),
                "actions": actions,
            }
        except Exception as exc:
            logger.warning(f"Failed to load timeline {name}: {exc}")
            return {"settings": {}, "actions": []}

    def get_window_bounds(self) -> Dict[str, int]:
        """Return current window geometry."""
        try:
            return {
                "x": int(getattr(self.window, "x", 0) or 0),
                "y": int(getattr(self.window, "y", 0) or 0),
                "width": int(getattr(self.window, "width", 0) or 0),
                "height": int(getattr(self.window, "height", 0) or 0),
            }
        except Exception:
            return {"x": 0, "y": 0, "width": 0, "height": 0}

    def set_bounds(self, x: int, y: int, width: int, height: int) -> None:
        """Move and resize the window (used by custom resize handles)."""
        try:
            if width > 0 and height > 0:
                self.window.resize(int(width), int(height))
            if x >= 0 and y >= 0:
                self.window.move(int(x), int(y))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Window controls (called from frontend title bar)
    # ------------------------------------------------------------------
    def minimize_window(self) -> None:
        self.window.minimize()

    def maximize_window(self) -> None:
        try:
            if getattr(self.window, 'state', None) == 'maximized':
                self.window.restore()
            else:
                self.window.maximize()
        except Exception:
            pass

    def close_window(self) -> None:
        self._shutdown()
        try:
            self.window.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _on_backend_event(self, event_type: str, **kwargs: Any) -> None:
        self._push_event(event_type, kwargs)
        if event_type in ("action", "select_oper", "cancel_deploy"):
            self._push_state()

    def _push_state(self) -> None:
        with self._lock:
            if self.backend is None:
                return
            state = dict(self.backend.latest_state)
            # Inject current game time if available
            game_time = self.backend.latest_game_time
            if game_time:
                state["current_cycle"] = game_time["cycle"]
                state["current_tick"] = game_time["tick"]
        self._push_event("state", state)

    def _push_event(self, event_type: str, data: Any) -> None:
        try:
            payload = json.dumps(
                {"event_type": event_type, "data": data},
                ensure_ascii=False,
                default=str,
            )
            self.window.evaluate_js(f"window.__onBackendEvent?.({payload})")
        except Exception as exc:
            logger.debug(f"Failed to push event to frontend: {exc}")

    def _shutdown(self) -> None:
        with self._lock:
            if self.backend is not None:
                try:
                    self.backend.stop()
                except Exception:
                    logger.exception("Error stopping backend during shutdown")
                self.backend = None
        # Stop any running playback thread.
        try:
            self.stop_playback(reset_state=True)
        except Exception:
            pass
        # Stop the process-wide WS time source.
        try:
            get_ws_time_source().stop()
        except Exception:
            logger.debug("WS time source stop failed during shutdown", exc_info=True)


def main() -> None:
    # --dev-tools: enable right-click → Inspect and bypass WebView2's UDF
    # cache (private mode).  Use during frontend iteration so a fresh
    # ``npm run build`` is always loaded.  Off by default for end users.
    dev_tools = "--dev-tools" in sys.argv
    # --debug-mouse: print every raw mouse event and its mapped ratio while
    # recording, to diagnose coordinate mismatches.
    debug_mouse = "--debug-mouse" in sys.argv

    dist_dir = project_root / "ui" / "dist"
    index_html = dist_dir / "index.html"
    if not index_html.is_file():
        print(
            "Frontend build not found. Please run:\n"
            "  cd ui && npm install && npm run build"
        )
        sys.exit(1)

    window = webview.create_window(
        title="ArkLoop",
        url=str(index_html),
        width=946,
        height=666,
        resizable=True,
        frameless=False,
        on_top=True,
        min_size=(946, 666),
        background_color="#0B0F13",
    )

    api = ArkLoopApi(window, mouse_debug=debug_mouse)

    # Expose API methods to the frontend as window.pywebview.api.*
    window.expose(*[
        getattr(api, name)
        for name in dir(api)
        if not name.startswith('_') and callable(getattr(api, name))
    ])

    # OS-native title-bar X bypasses any frontend JS — wire the pywebview
    # closing event to ``_shutdown()`` so the backend (pynput mouse listener,
    # AnalysisWorker, FrameSource) actually gets stopped.  Without this, a
    # post-recording exit leaves pynput's non-daemon Listener thread holding
    # the interpreter alive even after the window has disappeared.
    try:
        window.events.closing += lambda: api._shutdown()
    except Exception as exc:
        logger.warning(f"Failed to attach closing handler: {exc}")

    # Allow Ctrl+C to exit cleanly
    signal.signal(signal.SIGINT, lambda _s, _f: api.close_window())

    # Periodically push state while recording.
    #
    # Two rates: the WS time source pushes (cycle, tick) at the game's native
    # rate, so we ship a *lightweight* `game_time` event at 30 Hz (only when it
    # actually changes) for a smooth playhead — and the heavier full `state` +
    # axis only at ~10 Hz, which is plenty for recognizer status.  The WS feed
    # is live even when not recording, so the playhead shows whenever the game
    # time service is connected.
    def _state_publisher() -> None:
        last_axis_len: int = 0
        last_fc: int = -1
        last_connected: Optional[bool] = None
        last_ws_status: Optional[Dict[str, Any]] = None
        # Held-last-good values: when the server's memory read briefly reports
        # not-OK (mem_ok=false), hold the last good frame_count/game_time so the
        # readout doesn't flash to 0 or "未连接" on a transient blip. The WS
        # server pushes ~every 10ms; a single mem_ok=false must not flip the UI.
        good_fc: int = -1
        good_game_time: float = 0.0
        good_cycle: int = 0
        good_tick: int = 0
        slow_counter = 0
        while True:
            time.sleep(1.0 / 30.0)  # ~33 ms, 30 Hz
            try:
                # Fast lane: lightweight cycle/tick + WS game_time/frame_count,
                # pushed only on change.  Available with or without an active
                # backend — the playhead + time readout show whenever the game
                # time service is connected.
                try:
                    ws = get_ws_time_source()
                    gt = ws.get_game_time()
                    fc, game_time, mem_ok = ws.latest()
                    # Transport-level connection (stable) — NOT the per-message
                    # mem_ok flag, which toggles on transient memory-read blips.
                    connected = ws.is_connected()
                    if mem_ok:
                        good_fc = int(fc)
                        good_game_time = float(game_time)
                        good_cycle = int(gt.cycle)
                        good_tick = int(gt.tick)
                    disp_fc = good_fc if good_fc >= 0 else int(fc)
                    if disp_fc != last_fc or connected != last_connected:
                        api._push_event("game_time", {
                            "cycle": good_cycle if good_fc >= 0 else int(gt.cycle),
                            "tick": good_tick if good_fc >= 0 else int(gt.tick),
                            "frame_count": disp_fc,
                            "game_time": good_game_time if good_fc >= 0 else float(game_time),
                            "connected": connected,
                            "mem_ok": bool(mem_ok),
                        })
                        last_fc = disp_fc
                        last_connected = connected
                except Exception:
                    pass

                # Surface WS connection status changes (for the settings UI).
                try:
                    status = get_ws_time_source().status()
                    ws_view = {
                        "connected": status.get("connected"),
                        "mem_ok": status.get("mem_ok"),
                        "url": status.get("url"),
                    }
                    if ws_view != last_ws_status:
                        api._push_event("ws_status", status)
                        last_ws_status = ws_view
                except Exception:
                    pass

                # Slow lane: full state + axis only while recording.
                if api.backend is None:
                    continue
                slow_counter += 1
                if slow_counter >= 3:
                    slow_counter = 0
                    api._push_state()
                    axis = api.backend.get_axis()
                    if len(axis) != last_axis_len:
                        api._push_event("axis", axis)
                        last_axis_len = len(axis)
            except Exception:
                pass

    threading.Thread(target=_state_publisher, daemon=True).start()

    # ``private_mode=True`` is always on: WebView2 caches ``index.html`` by
    # file:// path, and since the path is stable across rebuilds, any new
    # hashed asset on disk is masked by the cached old index.html (which
    # still references the old hash).  We have no cookies / localStorage to
    # preserve, so the only "cost" is a sub-100ms re-parse on each launch.
    #
    # ``debug=True`` only when ``--dev-tools`` is passed — enables right-click
    # → Inspect for the developer.
    webview.start(private_mode=True, debug=dev_tools)

    # Belt-and-suspenders shutdown.  After webview.start() returns (window
    # closed), pynput's mouse Listener thread is non-daemon — if a recording
    # session ever ran and didn't release it, the interpreter will sit at the
    # exit barrier waiting for the listener thread.  WebView2's COM helper
    # threads have a similar history of pinning Windows processes.  Force the
    # process to terminate so no orphan python.exe lingers.
    try:
        api._shutdown()
    except Exception:
        pass
    os._exit(0)


if __name__ == "__main__":
    main()
