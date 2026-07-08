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
sys.path.insert(0, str(project_root))

from recorder.backend import ActionBackend, write_axis_json
from recorder.action_recognizer import AvatarMatcher
from src.desktop.config_service import ConfigService
from src.desktop.resource_service import ResourceService
from src.desktop.state_publisher import start_state_publisher
from src.desktop.timeline_service import TimelineService
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
        self.config_service = ConfigService(user_root)
        self.resource_service = ResourceService(project_root)
        self.timeline_service = TimelineService(timelines_dir, window)
        # Pre-warmed resources (populated in init_app)
        self._cached_matcher: Optional[AvatarMatcher] = None
        self._cached_view_detector: Optional[Any] = None
        # Playback
        self._playback_thread: Optional[threading.Thread] = None
        self._playback_stop = threading.Event()
        self._last_playback_frame: int = 0
        self._last_playback_state: Dict[str, Any] = {}
        self._mouse_debug = mouse_debug
        # True when the most recent playback breakpoint left the game paused
        # via the in-game pause toggle. Cleared on the next start_recording
        # / start_playback so the game is resumed before fresh control starts.
        self._game_paused_by_runner = False

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

            # If the previous playback (or user click) paused the game,
            # toggle pause again so recording observes a live game rather
            # than a frozen menu overlay.
            if self._game_paused_by_runner:
                try:
                    from src.mumu.mumu_controller import pause as game_pause
                    game_pause()
                    logger.info("[recording] dismissed pause menu from breakpoint")
                except Exception as exc:
                    logger.warning(f"Failed to dismiss pause menu: {exc}")
                self._game_paused_by_runner = False

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

            return {"ok": True, "avatars_loaded": count}
        except Exception as exc:
            logger.exception(f"init_app error: {exc}")
            return {"ok": False, "error": str(exc)}

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
        autoenter: bool = False,
        frame_offset: int = 0,
        breakpoints: Optional[List[int]] = None,
    ) -> bool:
        """Start playing a timeline file in a background thread.

        ``frame_offset`` shifts where in the timeline playback starts (resume
        from pause).  ``breakpoints`` is a list of absolute frame numbers.
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

        bp_frames: List[int] = []
        for bp in breakpoints or []:
            try:
                bp_frames.append(int(bp))
            except (TypeError, ValueError):
                continue

        from src.axis.axis_runner import AxisRunner
        self._playback_stop.clear()
        self._game_paused_by_runner = False
        frame_offset_int = int(frame_offset or 0)

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
                    # tick_callback removed — game_time is pushed by the global
                    # _state_publisher from the WS time source at 60 Hz.
                    stop_event=self._playback_stop,
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
                    {"frame": final_fc, "state": self._last_playback_state},
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
        self._playback_stop.set()
        if self._playback_thread is not None:
            self._playback_thread.join(timeout=2.0)
            self._playback_thread = None
        if reset_state:
            self.reset_playback_state()

    def pause_playback(self) -> Dict[str, Any]:
        """Stop playback and return the last known frame — frontend
        uses it to set frame_offset for resume. Emits 'paused' event too."""
        self.stop_playback(reset_state=False)
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
    # Debug
    # ------------------------------------------------------------------
    def debug_pause(self) -> Dict[str, Any]:
        """Toggle game pause and return the result for debugging."""
        logger.info("[debug_pause] called")
        try:
            from src.mumu.mumu_controller import pause as game_pause
        except Exception as exc:
            logger.error(f"[debug_pause] import failed: {exc}")
            return {"error": str(exc), "frame_before": 0, "frame_after": 0, "paused": False}
        frame_before = 0
        try:
            ws = get_ws_time_source()
            frame_before = ws.get_game_time()
        except Exception as exc:
            logger.warning(f"[debug_pause] ws read failed: {exc}")
        logger.info(f"[debug_pause] frame_before={frame_before}, sending ESC...")
        game_pause()
        import time as _time
        _time.sleep(0.2)
        frame_after = 0
        try:
            ws = get_ws_time_source()
            frame_after = ws.get_game_time()
        except Exception as exc:
            logger.warning(f"[debug_pause] ws read after failed: {exc}")
        result = {
            "frame_before": int(frame_before),
            "frame_after": int(frame_after),
            "paused": frame_after <= frame_before,
        }
        logger.info(f"[debug_pause] {result}")
        return result

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
    # FrameSource and input hooks actually get stopped.  Without this, a
    # post-recording exit leaves pynput's non-daemon Listener thread holding
    # the interpreter alive even after the window has disappeared.
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
    except Exception:
        pass
    os._exit(0)


if __name__ == "__main__":
    main()
