"""Windows overlay-window support for the compact ArkLoop controls."""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from typing import Any, Callable, Dict, Optional


_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_LAYERED = 0x00080000
_WS_EX_NOACTIVATE = 0x08000000
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020
_SWP_SHOWWINDOW = 0x0040
_HWND_TOPMOST = -1

_MOD_CONTROL = 0x0002
_MOD_ALT = 0x0001
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
_WM_NCLBUTTONDOWN = 0x00A1
_HTCAPTION = 2
_PM_REMOVE = 0x0001
_UNLOCK_HOTKEY_ID = 0xA710


class WindowOverlayController:
    """Switch a pywebview window between normal and compact overlay modes."""

    overlay_width = 760
    overlay_height = 340

    def __init__(
        self,
        window: Any,
        push_event: Callable[[str, Any], None],
    ) -> None:
        self.window = window
        self._push_event = push_event
        self._mode_lock = threading.RLock()
        self._overlay_enabled = False
        self._locked = False
        self._normal_bounds: Optional[Dict[str, int]] = None
        self._normal_border_style: Any = None
        self._normal_window_state: Any = None
        self._normal_minimum_size: Optional[tuple[int, int]] = None
        self._normal_opacity: Optional[float] = None
        self._overlay_opacity = 0.82
        self._unlocked_ex_style: Optional[int] = None

        self._hotkey_stop = threading.Event()
        self._hotkey_ready = threading.Event()
        self._hotkey_available = False
        self._hotkey_thread: Optional[threading.Thread] = None
        if sys.platform == "win32":
            self._hotkey_thread = threading.Thread(
                target=self._hotkey_loop,
                name="arkloop-overlay-hotkey",
                daemon=True,
            )
            self._hotkey_thread.start()

    @property
    def state(self) -> Dict[str, Any]:
        return {
            "enabled": self._overlay_enabled,
            "locked": self._locked,
            "hotkey_available": self.hotkey_available,
            "hotkey": "Ctrl+Alt+L",
            "opacity": self._overlay_opacity,
        }

    @property
    def hotkey_available(self) -> bool:
        self._hotkey_ready.wait(timeout=0.25)
        return self._hotkey_available

    def set_mode(self, enabled: bool) -> Dict[str, Any]:
        enabled = bool(enabled)
        with self._mode_lock:
            if enabled == self._overlay_enabled:
                return {"ok": True, **self.state}

            if enabled:
                self._normal_bounds = self._get_bounds()
                self._apply_overlay_chrome()
                self._overlay_enabled = True
                self.window.resize(self.overlay_width, self.overlay_height)
                self._apply_rounded_region()
            else:
                self.set_locked(False)
                self._clear_rounded_region()
                self._restore_normal_chrome()
                self._overlay_enabled = False
                bounds = self._normal_bounds
                if bounds:
                    self._restore_normal_bounds(bounds)
                self._restore_normal_window_state()

            result = {"ok": True, **self.state}
            self._push_event("overlay_mode_changed", result)
            return result

    def set_locked(self, locked: bool) -> Dict[str, Any]:
        locked = bool(locked)
        with self._mode_lock:
            if locked and not self._overlay_enabled:
                return {"ok": False, "error": "请先切换到迷你展示模式", **self.state}
            if locked and not self.hotkey_available:
                return {
                    "ok": False,
                    "error": "全局解锁快捷键注册失败，无法安全锁定窗口",
                    **self.state,
                }
            if locked == self._locked:
                return {"ok": True, **self.state}

            hwnd = self._get_hwnd()
            if not hwnd:
                return {"ok": False, "error": "未找到 ArkLoop 窗口句柄", **self.state}

            user32 = ctypes.windll.user32
            user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.GetWindowLongW.restype = ctypes.c_long
            user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
            user32.SetWindowLongW.restype = ctypes.c_long
            user32.SetWindowPos.argtypes = [
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            ]
            user32.SetWindowPos.restype = wintypes.BOOL
            current_style = int(user32.GetWindowLongW(hwnd, _GWL_EXSTYLE))
            if locked:
                self._unlocked_ex_style = current_style
                next_style = (
                    current_style
                    | _WS_EX_TRANSPARENT
                    | _WS_EX_LAYERED
                    | _WS_EX_NOACTIVATE
                )
            else:
                next_style = (
                    self._unlocked_ex_style
                    if self._unlocked_ex_style is not None
                    else current_style & ~(_WS_EX_TRANSPARENT | _WS_EX_NOACTIVATE)
                )

            ctypes.set_last_error(0)
            user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, next_style)
            user32.SetWindowPos(
                hwnd,
                wintypes.HWND(_HWND_TOPMOST),
                0,
                0,
                0,
                0,
                _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
            )
            self._locked = locked
            result = {"ok": True, **self.state}
            self._push_event("overlay_lock_changed", result)
            return result

    def set_opacity(self, opacity: float) -> Dict[str, Any]:
        """Set compact-overlay opacity, clamped to a readable range."""
        try:
            normalized = float(opacity)
        except (TypeError, ValueError):
            return {"ok": False, "error": "透明度必须是数字", **self.state}
        normalized = max(0.35, min(1.0, normalized))
        with self._mode_lock:
            self._overlay_opacity = normalized
            if self._overlay_enabled:
                def apply() -> None:
                    self.window.native.Opacity = normalized

                self._invoke_native(apply)
            result = {"ok": True, **self.state}
            self._push_event("overlay_opacity_changed", result)
            return result

    def begin_drag(self) -> Dict[str, Any]:
        """Hand compact-window dragging to Windows' native move loop."""
        with self._mode_lock:
            if not self._overlay_enabled:
                return {"ok": False, "error": "当前不是迷你展示模式"}
            if self._locked:
                return {"ok": False, "error": "请先解锁迷你窗口"}

            hwnd = self._get_hwnd()
            if not hwnd:
                return {"ok": False, "error": "未找到 ArkLoop 窗口句柄"}

            def drag() -> None:
                user32 = ctypes.windll.user32
                user32.ReleaseCapture.argtypes = []
                user32.ReleaseCapture.restype = wintypes.BOOL
                user32.SendMessageW.argtypes = [
                    wintypes.HWND,
                    ctypes.c_uint,
                    wintypes.WPARAM,
                    wintypes.LPARAM,
                ]
                user32.SendMessageW.restype = ctypes.c_ssize_t
                user32.ReleaseCapture()
                user32.SendMessageW(hwnd, _WM_NCLBUTTONDOWN, _HTCAPTION, 0)

            self._invoke_native(drag)
            return {"ok": True}

    def stop(self) -> None:
        """Release click-through and unregister the process-wide hotkey."""
        try:
            if self._locked:
                self.set_locked(False)
        finally:
            self._hotkey_stop.set()
            thread = self._hotkey_thread
            if thread and thread.is_alive():
                thread.join(timeout=0.3)

    def _get_bounds(self) -> Dict[str, int]:
        return {
            "x": int(getattr(self.window, "x", 0) or 0),
            "y": int(getattr(self.window, "y", 0) or 0),
            "width": int(getattr(self.window, "width", 946) or 946),
            "height": int(getattr(self.window, "height", 666) or 666),
        }

    def _get_hwnd(self) -> int:
        native = getattr(self.window, "native", None)
        if native is None:
            return 0
        try:
            return int(native.Handle.ToInt64())
        except Exception:
            try:
                return int(native.Handle.ToInt32())
            except Exception:
                return 0

    def set_bounds(self, x: int, y: int, width: int, height: int) -> None:
        """Move and resize the native window using logical-pixel bounds."""
        self._set_native_bounds(
            {
                "x": int(x),
                "y": int(y),
                "width": max(1, int(width)),
                "height": max(1, int(height)),
            }
        )

    def _restore_normal_bounds(self, bounds: Dict[str, int]) -> None:
        self._set_native_bounds(bounds)

    def _set_native_bounds(self, bounds: Dict[str, int]) -> None:
        """Apply geometry without pywebview's broken WinForms move().

        pywebview 5's WinForms ``move`` implementation passes ``None`` for the
        integer width/height parameters of ``SetWindowPos``. ctypes rejects
        those values before Win32 can honor ``SWP_NOSIZE``. Apply position and
        size atomically with explicit physical-pixel integers instead.
        """
        hwnd = self._get_hwnd()
        if not hwnd:
            raise RuntimeError("未找到 ArkLoop 窗口句柄，无法恢复窗口位置")

        user32 = ctypes.windll.user32
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        user32.GetDpiForWindow.restype = ctypes.c_uint
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL

        dpi = int(user32.GetDpiForWindow(hwnd) or 96)
        scale = dpi / 96.0
        x = round(int(bounds["x"]) * scale)
        y = round(int(bounds["y"]) * scale)
        width = max(1, round(int(bounds["width"]) * scale))
        height = max(1, round(int(bounds["height"]) * scale))

        ctypes.set_last_error(0)
        restored = user32.SetWindowPos(
            hwnd,
            wintypes.HWND(0),
            x,
            y,
            width,
            height,
            _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED | _SWP_SHOWWINDOW,
        )
        if not restored:
            error_code = ctypes.get_last_error()
            raise OSError(error_code, "恢复 ArkLoop 窗口位置和尺寸失败")

    def _invoke_native(self, callback: Callable[[], None]) -> None:
        native = getattr(self.window, "native", None)
        if native is None:
            raise RuntimeError("pywebview native window is not ready")
        if getattr(native, "InvokeRequired", False):
            from System import Func, Type  # type: ignore[import-not-found]

            native.Invoke(Func[Type](callback))
        else:
            callback()

    def _apply_overlay_chrome(self) -> None:
        def apply() -> None:
            import System.Windows.Forms as WinForms  # type: ignore[import-not-found]
            from System.Drawing import Size  # type: ignore[import-not-found]

            native = self.window.native
            self._normal_border_style = native.FormBorderStyle
            self._normal_window_state = native.WindowState
            self._normal_minimum_size = (
                int(native.MinimumSize.Width),
                int(native.MinimumSize.Height),
            )
            self._normal_opacity = float(native.Opacity)
            native.WindowState = WinForms.FormWindowState.Normal
            native.MinimumSize = Size(360, 110)
            native.FormBorderStyle = getattr(WinForms.FormBorderStyle, "None")
            native.TopMost = True
            native.Opacity = self._overlay_opacity

        self._invoke_native(apply)

    def _restore_normal_chrome(self) -> None:
        def restore() -> None:
            from System.Drawing import Size  # type: ignore[import-not-found]

            native = self.window.native
            if self._normal_border_style is not None:
                native.FormBorderStyle = self._normal_border_style
            if self._normal_minimum_size is not None:
                native.MinimumSize = Size(*self._normal_minimum_size)
            if self._normal_opacity is not None:
                native.Opacity = self._normal_opacity
            native.TopMost = True

        self._invoke_native(restore)

    def _apply_rounded_region(self) -> None:
        """Physically clip the borderless HWND so transparent corners stay transparent."""
        hwnd = self._get_hwnd()
        if not hwnd:
            return

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        user32.GetDpiForWindow.restype = ctypes.c_uint
        user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
        user32.SetWindowRgn.restype = ctypes.c_int
        gdi32.CreateRoundRectRgn.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteObject.restype = wintypes.BOOL

        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return
        width = max(1, int(rect.right - rect.left))
        height = max(1, int(rect.bottom - rect.top))
        dpi = int(user32.GetDpiForWindow(hwnd) or 96)
        corner_diameter = max(2, round(28 * dpi / 96))
        region = gdi32.CreateRoundRectRgn(
            0,
            0,
            width + 1,
            height + 1,
            corner_diameter,
            corner_diameter,
        )
        if region and not user32.SetWindowRgn(hwnd, region, True):
            gdi32.DeleteObject(region)

    def _clear_rounded_region(self) -> None:
        hwnd = self._get_hwnd()
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
        user32.SetWindowRgn.restype = ctypes.c_int
        user32.SetWindowRgn(hwnd, None, True)

    def _restore_normal_window_state(self) -> None:
        if self._normal_window_state is None:
            return

        def restore() -> None:
            self.window.native.WindowState = self._normal_window_state

        self._invoke_native(restore)

    def _hotkey_loop(self) -> None:
        user32 = ctypes.windll.user32
        registered = bool(
            user32.RegisterHotKey(
                None,
                _UNLOCK_HOTKEY_ID,
                _MOD_CONTROL | _MOD_ALT | _MOD_NOREPEAT,
                ord("L"),
            )
        )
        self._hotkey_available = registered
        self._hotkey_ready.set()
        if not registered:
            return

        msg = wintypes.MSG()
        try:
            while not self._hotkey_stop.wait(0.04):
                while user32.PeekMessageW(
                    ctypes.byref(msg), None, 0, 0, _PM_REMOVE
                ):
                    if msg.message == _WM_HOTKEY and msg.wParam == _UNLOCK_HOTKEY_ID:
                        if self._locked:
                            self.set_locked(False)
        finally:
            user32.UnregisterHotKey(None, _UNLOCK_HOTKEY_ID)
