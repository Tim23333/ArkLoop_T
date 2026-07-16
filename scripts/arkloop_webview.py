"""ArkLoop desktop entry point — PyWebview + React.

Loads the React frontend from ``ui/dist/index.html`` and exposes the action
recognition backend via ``pywebview.api``.

Usage:
    .venv\\Scripts\\python scripts/arkloop_webview.py
"""

from __future__ import annotations

# Add MAA's DLL directory to the Windows search path BEFORE any maa imports.
# PyInstaller bundles MaaFramework.dll + deps under _internal/maa/bin/ but the
# DLL loader doesn't search there by default.  os.add_dll_directory alone may
# not work after PyInstaller's bootloader restricts the search path, so we also
# prepend to PATH and force-load the dependency DLLs first.
import os as _os, sys as _sys
if _sys.platform == "win32":
    _maa_bin = (
        _os.path.join(_sys._MEIPASS, "maa", "bin")
        if getattr(_sys, "frozen", False)
        else _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                           "..", ".venv", "Lib", "site-packages", "maa", "bin")
    )
    if _os.path.isdir(_maa_bin):
        # Belt-and-suspenders: add to PATH (always works) and add_dll_directory.
        _os.environ["PATH"] = _maa_bin + _os.pathsep + _os.environ.get("PATH", "")
        try:
            _os.add_dll_directory(_maa_bin)
        except Exception:
            pass
        # Force-load MaaFramework's dependencies so they're already in the
        # process module list when MaaFramework.dll itself is loaded via ctypes.
        import ctypes as _ctypes
        for _dll in [
            "MaaUtils.dll",
            "fastdeploy_ppocr_maa.dll",
            "onnxruntime_maa.dll",
            "opencv_world4_maa.dll",
            "DirectML.dll",
        ]:
            try:
                _ctypes.WinDLL(_os.path.join(_maa_bin, _dll))
            except Exception:
                pass

import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import webview

if getattr(sys, "frozen", False):
    # PyInstaller onedir: bundled datas (ui/dist, resource,
    # bundled resources live under sys._MEIPASS (== _internal/).  User-
    # writable state (timelines/, config.json) lives next to the EXE so it
    # survives reinstalls and isn't hidden inside _internal.
    project_root = Path(sys._MEIPASS)
    user_root = Path(sys.executable).parent
else:
    project_root = Path(__file__).parent.parent
    user_root = project_root
timelines_dir = user_root / "timelines"
bundled_resource_dir = project_root / "resource"
external_resource_dir = user_root / "resource"


def _is_complete_resource_dir(path: Path) -> bool:
    return path.is_dir() and all(
        (path / name).is_file()
        for name in (
            "operator_mapping.json",
            "level_code_mapping.json",
            "level_name_mapping.json",
        )
    )


runtime_resource_dir = (
    external_resource_dir
    if getattr(sys, "frozen", False) and _is_complete_resource_dir(external_resource_dir)
    else bundled_resource_dir
)
resource_sync_target_dir = (
    external_resource_dir if getattr(sys, "frozen", False) else bundled_resource_dir
)
os.environ["ARKLOOP_RESOURCE_PATH"] = str(runtime_resource_dir)
sys.path.insert(0, str(project_root))

from src.runtime_dependencies import (
    ACCELERATION_ENV,
    configure_gpu_dependencies,
    configure_optional_dependencies,
    write_dependency_mode,
)

optional_dependency_state = configure_optional_dependencies(
    user_root,
    frozen=bool(getattr(sys, "frozen", False)),
)

from recorder.backend import ActionBackend, write_axis_json
from recorder.action_recognizer import AvatarMatcher
from src.desktop.config_service import ConfigService
from src.desktop.resource_service import ResourceService
from src.desktop.resource_sync_service import ResourceSyncService
from src.desktop.state_publisher import start_state_publisher
from src.desktop.timeline_service import TimelineService
from src.desktop.window_overlay import WindowOverlayController
from src.axis.playback_controller import PlaybackController, PlaybackInterrupted
from src.cache import configure_resource_path
from src.logger import logger
from src.logic.ws_time_source import DEFAULT_WS_URL, get_ws_time_source

if optional_dependency_state.configured:
    logger.info(optional_dependency_state.message)
else:
    logger.warning(optional_dependency_state.message)

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
        self._shutdown_lock = threading.Lock()
        self._shutdown_started = threading.Event()
        self._shutdown_finished = threading.Event()
        self._shutdown_thread: Optional[threading.Thread] = None
        self.config_service = ConfigService(user_root)
        self.resource_service = ResourceService(project_root, runtime_resource_dir)
        self.resource_sync_service = ResourceSyncService(
            runtime_resource_dir,
            resource_sync_target_dir,
        )
        self.timeline_service = TimelineService(timelines_dir, window)
        # Pre-warmed resources (populated in init_app)
        self._cached_matcher: Optional[AvatarMatcher] = None
        self._cached_view_detector: Optional[Any] = None
        self._acceleration_switching = False
        # Playback
        self._playback_thread: Optional[threading.Thread] = None
        self._playback_controller: Optional[PlaybackController] = None
        self._last_playback_frame: int = 0
        self._last_playback_state: Dict[str, Any] = {}
        self._mouse_debug = mouse_debug
        self.window_overlay = WindowOverlayController(window, self._push_event)

    # ------------------------------------------------------------------
    # Recording lifecycle
    # ------------------------------------------------------------------
    def start_recording(
        self,
        map_code: str = "1-7",
        max_tick: Optional[int] = None,
        fake_avatar: bool = False,
        frame_offset: int = 0,
        recognizer_state: Optional[Dict[str, Any]] = None,
        devices: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        with self._lock:
            if self.backend is not None:
                return
            if self.resource_sync_service.is_running:
                raise RuntimeError("资源同步中，请等待同步完成后再开始录制")

            # A paused playback intentionally leaves the game paused. Resume
            # through the same controller before starting a recording session.
            if self._playback_controller is not None:
                try:
                    self._playback_controller.resume_for_new_session()
                    logger.info("[recording] resumed game from previous playback pause")
                except Exception as exc:
                    logger.warning(f"Failed to resume previous playback pause: {exc}")

            self.backend = ActionBackend(
                map_code=map_code,
                max_tick=max_tick,
                event_callback=self._on_backend_event,
                fake_avatar=fake_avatar,
                frame_offset=int(frame_offset or 0),
                recognizer_state=recognizer_state,
                devices=devices,
                _matcher=self._cached_matcher,
                _view_detector=self._cached_view_detector,
                mouse_debug=self._mouse_debug,
            ).start()
            logger.info(
                f"[recording] started frame_offset={frame_offset} "
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

    def pause_recording(self) -> Dict[str, Any]:
        """Stop the recorder and return the frame at pause time.

        Used by the frontend Pause button: caller then sets frame_offset for
        the next session.  Emits a 'paused' event so the UI can also pick it
        up out-of-band.
        """
        with self._lock:
            if self.backend is None:
                return {"frame": 0}
            gt = self.backend.latest_game_time or {"frame": 0}
            axis = self.backend.stop()
            self.backend = None
        frame = int(gt.get("frame", 0))
        self._push_event("axis", axis)
        self._push_event("paused", {"source": "recording", "frame": frame})
        return {"frame": frame, "axis": axis}

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

    def capture_with_grid(self, map_code: str) -> str:
        return self.resource_service.capture_with_grid(map_code)

    def get_avatar_url(self, oper: str) -> str:
        return self.resource_service.get_avatar_url(oper)

    def start_resource_sync(self) -> Dict[str, Any]:
        """Start an incremental avatar/map update in a background thread."""
        with self._lock:
            playback_active = bool(
                self._playback_thread is not None and self._playback_thread.is_alive()
            )
            if self.backend is not None or playback_active:
                status = self.resource_sync_service.get_status()
                return {
                    **status,
                    "ok": False,
                    "running": False,
                    "phase": "error",
                    "message": "请先停止录制或播放，再同步资源。",
                    "error": "请先停止录制或播放，再同步资源。",
                }
            if self._acceleration_switching:
                status = self.resource_sync_service.get_status()
                return {
                    **status,
                    "ok": False,
                    "running": False,
                    "phase": "error",
                    "message": "识别模式正在切换，请稍候。",
                    "error": "识别模式正在切换，请稍候。",
                }
            return self.resource_sync_service.start(self._activate_synced_resources)

    def get_resource_sync_status(self) -> Dict[str, Any]:
        return self.resource_sync_service.get_status()

    def _activate_synced_resources(self, resource_dir: Path) -> None:
        configure_resource_path(resource_dir)
        self.resource_service.set_resource_dir(resource_dir)
        self.resource_service.prewarm_avatars(limit=30)

        matcher = AvatarMatcher()
        count = matcher.prewarm()
        with self._lock:
            self._cached_matcher = matcher
        logger.info(
            "Synchronized resources activated from %s (%s operators pre-warmed)",
            resource_dir,
            count,
        )

    def init_app(self) -> dict:
        """Initialize app resources (avatar cache, MAA, directories). Called once on startup."""
        try:
            timelines_dir.mkdir(parents=True, exist_ok=True)

            # Start the WebSocket time source using the URL configured in
            # config.json (time_source.ws_url).  This is the sole game-time
            # provider for both recording and playback.  Started here so the
            # feed is live before any record /
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

            count = self.resource_service.prewarm_avatars(limit=30)

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

            runtime_mode = (
                "gpu"
                if self._cached_matcher is not None
                and bool(getattr(self._cached_matcher, "_gpu_ready", False))
                else "cpu"
            )
            return {
                "ok": True,
                "avatars_loaded": count,
                "runtime_mode": runtime_mode,
            }
        except Exception as exc:
            logger.exception(f"init_app error: {exc}")
            return {"ok": False, "error": str(exc)}

    def get_acceleration_mode(self) -> Dict[str, Any]:
        """Return the recognition mode that is active in this process."""
        return {"ok": True, "mode": self._effective_acceleration_mode()}

    def set_acceleration_mode(self, mode: str) -> Dict[str, Any]:
        """Try to switch avatar recognition between CPU and GPU at runtime."""
        requested_mode = str(mode or "").strip().lower()
        current_mode = self._effective_acceleration_mode()
        if requested_mode not in {"cpu", "gpu"}:
            return {
                "ok": False,
                "mode": current_mode,
                "error": f"不支持的识别模式：{mode}",
            }

        with self._lock:
            playback_active = bool(
                self._playback_thread is not None and self._playback_thread.is_alive()
            )
            if self.backend is not None or playback_active or self.resource_sync_service.is_running:
                return {
                    "ok": False,
                    "mode": current_mode,
                    "error": "请先停止录制或播放，并等待资源同步完成，再切换识别模式。",
                }
            if self._acceleration_switching:
                return {
                    "ok": False,
                    "mode": current_mode,
                    "error": "识别模式正在切换，请稍候。",
                }
            if requested_mode == current_mode:
                return {
                    "ok": True,
                    "mode": current_mode,
                    "changed": False,
                    "message": f"当前已经是 {current_mode.upper()} 模式。",
                }
            self._acceleration_switching = True

        previous_env = os.environ.get(ACCELERATION_ENV)
        try:
            if requested_mode == "gpu":
                dependency_state = configure_gpu_dependencies(
                    user_root,
                    frozen=bool(getattr(sys, "frozen", False)),
                )
                if not dependency_state.configured:
                    installer_started = self._launch_dependency_installer()
                    message = (
                        "未安装 GPU 依赖，已打开依赖安装程序。安装完成后请再次点击 GPU 模式。"
                        if installer_started
                        else "未安装 GPU 依赖，且未找到 ArkLoopDependencyInstaller.exe。"
                    )
                    return {
                        "ok": False,
                        "mode": current_mode,
                        "installer_started": installer_started,
                        "error": message,
                    }
            else:
                os.environ[ACCELERATION_ENV] = "cpu"

            candidate = AvatarMatcher()
            previous_matcher = self._cached_matcher
            previous_templates = getattr(previous_matcher, "_templates", None)
            if isinstance(previous_templates, dict):
                candidate._templates = previous_templates
            avatar_count = candidate.prewarm()
            if requested_mode == "gpu" and not bool(candidate._gpu_ready):
                raise RuntimeError("PyTorch 已加载，但 CUDA 当前不可用")

            write_dependency_mode(
                user_root,
                requested_mode,
                runtime_switch=True,
            )
            self._cached_matcher = candidate
            logger.info(
                "Avatar recognition switched to %s (%s operators pre-warmed)",
                requested_mode.upper(),
                avatar_count,
            )
            result = {
                "ok": True,
                "mode": requested_mode,
                "changed": True,
                "message": f"已切换到 {requested_mode.upper()} 识别模式。",
            }
            self._push_event("acceleration_mode_changed", result)
            return result
        except Exception as exc:
            if previous_env is None:
                os.environ.pop(ACCELERATION_ENV, None)
            else:
                os.environ[ACCELERATION_ENV] = previous_env
            logger.exception("Failed to switch avatar recognition to %s", requested_mode)
            return {
                "ok": False,
                "mode": current_mode,
                "error": f"无法切换到 {requested_mode.upper()} 模式：{exc}",
            }
        finally:
            with self._lock:
                self._acceleration_switching = False

    def create_timeline(self) -> str:
        return self.timeline_service.create_timeline()

    def save_timeline(self, name: str, actions: list, settings: dict) -> bool:
        return self.timeline_service.save_timeline(name, actions, settings)

    def delete_timeline(self, name: str) -> bool:
        return self.timeline_service.delete_timeline(name)

    def duplicate_timeline(self, name: str) -> str:
        return self.timeline_service.duplicate_timeline(name)

    def rename_timeline(self, old_name: str, new_name: str) -> str:
        return self.timeline_service.rename_timeline(old_name, new_name)

    def export_timeline(self, name: str) -> bool:
        return self.timeline_service.export_timeline(name)

    def import_timeline(self) -> str:
        return self.timeline_service.import_timeline()

    def get_app_config(self) -> Dict[str, Any]:
        return self.config_service.get_app_config()

    def get_ws_status(self) -> Dict[str, Any]:
        return self.config_service.get_ws_status()

    def restart_ws_source(self, url: Optional[str] = None) -> bool:
        return self.config_service.restart_ws_source(url)

    def update_app_config(self, patch: Dict[str, Any]) -> bool:
        return self.config_service.update_app_config(patch)

    def list_timeline_presets(self) -> List[Dict[str, Any]]:
        return self.timeline_service.list_timeline_presets()

    def save_timeline_preset(self, name: str, settings: Dict[str, Any]) -> bool:
        return self.timeline_service.save_timeline_preset(name, settings)

    def delete_timeline_preset(self, name: str) -> bool:
        return self.timeline_service.delete_timeline_preset(name)

    def get_pinned_timelines(self) -> list:
        return self.timeline_service.get_pinned_timelines()

    def set_pinned_timelines(self, pinned: list) -> bool:
        return self.timeline_service.set_pinned_timelines(pinned)

    def list_timelines(self) -> List[str]:
        return self.timeline_service.list_timelines()

    def list_maps(self) -> List[Dict[str, str]]:
        return self.resource_service.list_maps()

    def list_operators(self) -> List[Dict[str, str]]:
        return self.resource_service.list_operators()

    def start_playback(
        self,
        name: str,
        frame_offset: int = 0,
        breakpoints: Optional[List[int]] = None,
    ) -> bool:
        """Start playing a timeline file in a background thread.

        ``frame_offset`` shifts where in the timeline playback starts (resume
        from pause).  ``breakpoints`` is a list of absolute frame numbers.
        """
        if self.resource_sync_service.is_running:
            return False
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

        bp_frames: List[int] = []
        for bp in breakpoints or []:
            try:
                bp_frames.append(int(bp))
            except (TypeError, ValueError):
                continue

        from src.axis.axis_runner import AxisRunner
        frame_offset_int = int(frame_offset or 0)

        if self._playback_controller is not None:
            try:
                self._playback_controller.resume_for_new_session()
            except Exception as exc:
                logger.warning(f"Failed to prepare previous playback session: {exc}")

        controller = PlaybackController(
            state_callback=lambda state: self._push_event("playback_state", state)
        )
        self._playback_controller = controller

        # A fresh playback (no resume offset) starts from a clean slate so a
        # stale deployed set from a previous timeline/run can't leak in. A
        # resume (offset > 0) carries the deployed set forward so operators
        # placed in earlier segments stay known.
        if frame_offset_int <= 0:
            self._last_playback_state = {}
        seed_state = dict(self._last_playback_state) if frame_offset_int > 0 else None

        # Game time is now pushed by the global _state_publisher (60 Hz from
        # the WS time source).  No per-playback publisher thread is needed —
        # the runner only needs the observer for breakpoint checking.

        def _run() -> None:
            runner = None
            try:
                def _on_runner_pause(frame: int) -> None:
                    # Breakpoint fired — runner already paused the game.
                    state = runner.get_state() if runner is not None else {}
                    logger.info(
                        f"[playback] breakpoint pause frame={frame} "
                        f"state={state}"
                    )
                    self._push_event(
                        "paused",
                        {"source": "playback", "frame": frame, "state": state},
                    )

                runner = AxisRunner(
                    actions=actions,
                    settings=_settings,
                    # One controller owns stop, pause and frame-accurate action timing.
                    playback_controller=controller,
                    show_error=lambda msg: logger.error(f"Playback error: {msg}"),
                    frame_offset=frame_offset_int,
                    breakpoints=bp_frames,
                    on_pause=_on_runner_pause,
                    initial_state=seed_state,
                )
                runner.run()
            except Exception as exc:
                logger.exception(f"Playback error: {exc}")
            finally:
                self._last_playback_state = runner.get_state() if runner is not None else {}
                # Read final frame from WS for the playback_done event.
                try:
                    ws = get_ws_time_source()
                    final_fc = int(ws.latest()[0])
                except Exception:
                    final_fc = 0
                self._last_playback_frame = final_fc
                logger.info(
                    f"[playback] ended frame={final_fc} "
                    f"state={self._last_playback_state}"
                )
                self._push_event(
                    "playback_done",
                    {
                        "frame": final_fc,
                        "state": self._last_playback_state,
                        "playback": controller.snapshot(),
                    },
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
        self._last_playback_frame = 0

    def stop_playback(self, reset_state: bool = True) -> None:
        """Stop a running playback.

        ``reset_state`` clears the carried-forward state (full stop / red ■).
        Pause passes ``reset_state=False`` so a subsequent resume can inherit
        the deployed set.
        """
        controller = self._playback_controller
        if controller is not None:
            controller.request_stop(source="ui_stop")
        thread = self._playback_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        if thread is not None and thread.is_alive():
            logger.warning("Playback thread did not stop within 3 seconds")
        else:
            self._playback_thread = None
            if controller is not None and controller.stop_requested:
                try:
                    controller.check_interruption()
                except PlaybackInterrupted:
                    pass
            if reset_state:
                self.reset_playback_state()

    def pause_playback(self) -> Dict[str, Any]:
        """Stop playback and return the last known frame — frontend
        uses it to set frame_offset for resume. Emits 'paused' event too."""
        controller = self._playback_controller
        if controller is None:
            return {"ok": False}
        controller.request_pause(source="ui_pause")
        thread = self._playback_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        if thread is not None and thread.is_alive():
            logger.warning("Playback thread did not pause within 3 seconds")
            return {"ok": False}
        self._playback_thread = None
        if controller.stop_requested and not controller.game_paused:
            try:
                controller.check_interruption()
            except PlaybackInterrupted:
                pass
        logger.info(
            f"[playback] paused frame={self._last_playback_frame} "
            f"state={self._last_playback_state}"
        )
        self._push_event(
            "paused",
            {
                "source": "playback",
                "frame": self._last_playback_frame,
                "state": self._last_playback_state,
            },
        )
        return {"ok": True}
    def append_to_timeline(self, name: str, new_actions: list) -> bool:
        return self.timeline_service.append_to_timeline(name, new_actions)

    def save_breakpoints(self, name: str, breakpoints: list) -> bool:
        return self.timeline_service.save_breakpoints(name, breakpoints)

    def load_timeline(self, name: str) -> Dict[str, Any]:
        return self.timeline_service.load_timeline(name)


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
            self.window_overlay.set_bounds(x, y, width, height)
        except Exception:
            logger.exception("Failed to update ArkLoop window bounds")

    def set_overlay_mode(self, enabled: bool) -> Dict[str, Any]:
        """Switch between the full editor and compact transparent overlay."""
        try:
            return self.window_overlay.set_mode(bool(enabled))
        except Exception as exc:
            logger.exception("Failed to switch ArkLoop overlay mode")
            return {"ok": False, "error": str(exc)}

    def begin_window_drag(self) -> Dict[str, Any]:
        """Start the native Windows move loop for the compact overlay."""
        try:
            return self.window_overlay.begin_drag()
        except Exception as exc:
            logger.exception("Failed to begin ArkLoop window drag")
            return {"ok": False, "error": str(exc)}

    def set_overlay_locked(self, locked: bool) -> Dict[str, Any]:
        """Enable or disable native click-through for the compact overlay."""
        try:
            return self.window_overlay.set_locked(bool(locked))
        except Exception as exc:
            logger.exception("Failed to change ArkLoop overlay lock")
            return {"ok": False, "error": str(exc)}

    def set_overlay_opacity(self, opacity: float) -> Dict[str, Any]:
        """Adjust compact-overlay opacity without affecting the full editor."""
        try:
            return self.window_overlay.set_opacity(opacity)
        except Exception as exc:
            logger.exception("Failed to change ArkLoop overlay opacity")
            return {"ok": False, "error": str(exc)}

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
    def _effective_acceleration_mode(self) -> str:
        matcher = self._cached_matcher
        return "gpu" if matcher is not None and bool(matcher._gpu_ready) else "cpu"

    def _launch_dependency_installer(self) -> bool:
        if not bool(getattr(sys, "frozen", False)):
            return False
        installer = user_root / "ArkLoopDependencyInstaller.exe"
        if not installer.is_file():
            logger.warning("GPU dependency installer not found at %s", installer)
            return False
        try:
            subprocess.Popen([str(installer), "--gpu"], cwd=str(user_root))
            logger.info("Started GPU dependency installer: %s", installer)
            return True
        except OSError as exc:
            logger.warning("Failed to start GPU dependency installer: %s", exc)
            return False

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
                state["frame_count"] = game_time.get("frame", 0)
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

    def _finish_shutdown(self) -> None:
        try:
            self.window_overlay.stop()
            controller = self._playback_controller
            if controller is not None:
                controller.request_stop(source="shutdown")

            # Detach under the API lock, then stop outside it. Backend workers
            # may emit a final callback that also needs this lock.
            with self._lock:
                backend = self.backend
                self.backend = None
            if backend is not None:
                try:
                    backend.stop()
                except Exception:
                    logger.exception("Error stopping backend during shutdown")

            try:
                self.stop_playback(reset_state=True)
            except Exception:
                logger.debug("Playback stop failed during shutdown", exc_info=True)

            try:
                get_ws_time_source().stop()
            except Exception:
                logger.debug("WS time source stop failed during shutdown", exc_info=True)
        finally:
            self._shutdown_finished.set()

    def _shutdown(self) -> None:
        """Begin idempotent background cleanup without blocking window close."""
        with self._shutdown_lock:
            if self._shutdown_started.is_set():
                return
            self._shutdown_started.set()

            self._shutdown_thread = threading.Thread(
                target=self._finish_shutdown,
                name="arkloop-shutdown",
                daemon=True,
            )
            self._shutdown_thread.start()

    def _wait_for_shutdown(self, timeout: float) -> bool:
        return self._shutdown_finished.wait(timeout=max(0.0, float(timeout)))


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
        transparent=True,
    )

    api = ArkLoopApi(window, mouse_debug=debug_mouse)

    # Expose API methods to the frontend as window.pywebview.api.*
    window.expose(*[
        getattr(api, name)
        for name in dir(api)
        if not name.startswith('_') and callable(getattr(api, name))
    ])

    # OS-native title-bar X bypasses frontend JS. Begin cleanup from the native
    # close event, but never run thread joins on WebView's UI thread. The
    # process-exit path below gives cleanup a brief
    # grace period before forcing termination.
    try:
        window.events.closing += lambda: api._shutdown()
    except Exception as exc:
        logger.warning(f"Failed to attach closing handler: {exc}")

    # Allow Ctrl+C to exit cleanly
    signal.signal(signal.SIGINT, lambda _s, _f: api.close_window())

    start_state_publisher(
        get_backend=lambda: api.backend,
        push_event=api._push_event,
        push_state=api._push_state,
    )

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
        api._wait_for_shutdown(timeout=1.0)
    except Exception:
        pass
    os._exit(0)


if __name__ == "__main__":
    main()
