import win32gui
import win32ui
import win32con
import cv2
import numpy as np
from typing import Tuple

from src.mumu.capture_controller import BaseCaptureController
from src.config import ImageProcessingConfig as imgconfig


class Win32CaptureController(BaseCaptureController):
    """
    Legacy Win32 BitBlt capture controller.
    Kept as a fallback when MuMu DLL capture is unavailable.
    """

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        # Cached geometry — recomputed by ``_sync_geometry`` on each capture so
        # a window resize between calls doesn't produce a stretched/failed grab.
        self.offset_x = 0
        self.offset_y = 0
        self.width = 0
        self.height = 0

    def connect(self):
        # Nothing persistent to create.  GDI objects are allocated per capture
        # in ``_grab_full`` / ``capture_window_area`` and torn down immediately
        # after.  Caching a GetWindowDC handle across calls corrupts state in
        # pywin32 and makes every capture after the first fail with
        # ``BitBlt failed``; the per-call lifecycle is the robust pattern.
        self._sync_geometry()
        return self

    def _sync_geometry(self) -> None:
        """Recompute client-area size + window-to-client offset for ``self.hwnd``."""
        window_left, window_top, _, _ = win32gui.GetWindowRect(self.hwnd)
        client_left, client_top = win32gui.ClientToScreen(self.hwnd, (0, 0))
        self.offset_x = client_left - window_left
        self.offset_y = client_top - window_top
        client_rect = win32gui.GetClientRect(self.hwnd)
        self.width = client_rect[2]
        self.height = client_rect[3]

    @staticmethod
    def _release(window_dc, mfc_dc, save_dc, bitmap, prev_obj, hwnd) -> None:
        """Tear down per-call GDI objects in the order GDI requires.

        A bitmap still selected into a DC cannot be deleted, so the previous
        stock object must be selected back first.
        """
        try:
            if prev_obj is not None:
                save_dc.SelectObject(prev_obj)
        except Exception:
            pass
        try:
            if bitmap is not None:
                win32gui.DeleteObject(bitmap.GetHandle())
        except Exception:
            pass
        try:
            save_dc.DeleteDC()
        except Exception:
            pass
        try:
            mfc_dc.DeleteDC()
        except Exception:
            pass
        try:
            win32gui.ReleaseDC(hwnd, window_dc)
        except Exception:
            pass

    def _grab_full(self, color: bool) -> np.ndarray:
        """Capture the full client area with fresh GDI objects, then clean up."""
        hwnd = self.hwnd
        width, height = self.width, self.height
        if width <= 0 or height <= 0:
            raise RuntimeError("Win32CaptureController has invalid window size")

        window_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(window_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        save_bitmap = win32ui.CreateBitmap()
        save_bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        prev_obj = save_dc.SelectObject(save_bitmap)
        try:
            save_dc.BitBlt(
                (0, 0),
                (width, height),
                mfc_dc,
                (self.offset_x, self.offset_y),
                win32con.SRCCOPY,
            )
            bmpinfo = save_bitmap.GetInfo()
            signed_ints_array = save_bitmap.GetBitmapBits(True)
            img = np.frombuffer(signed_ints_array, dtype="uint8")
            img.shape = (bmpinfo["bmHeight"], bmpinfo["bmWidth"], 4)
        finally:
            self._release(window_dc, mfc_dc, save_dc, save_bitmap, prev_obj, hwnd)

        if color:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        else:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
        return cv2.resize(img, imgconfig.SCREEN_STANDARD_SIZE)

    def capture_frame(self, color: bool = False) -> np.ndarray:
        """Capture the full window as a grayscale or BGR numpy array."""
        # Refresh geometry each call — the MuMu render sub-window can be
        # recreated across scenes, and a resized window would otherwise be
        # captured at stale dimensions.
        self._sync_geometry()
        return self._grab_full(color=color)

    def capture_window_area(
        self, ratio: Tuple[float, float, float, float]
    ) -> np.ndarray:
        """Capture a ratio-defined sub-area as a grayscale numpy array."""
        if len(ratio) != 4:
            raise ValueError("Ratio must be a tuple of 4 floats")
        if not all(0 <= x <= 1 for x in ratio):
            raise ValueError("Ratio values must be between 0 and 1")
        if ratio[0] >= ratio[2] or ratio[1] >= ratio[3]:
            raise ValueError("Invalid ratio values")

        self._sync_geometry()
        window_width = self.width
        window_height = self.height

        capture_left = int(window_width * ratio[0])
        capture_top = int(window_height * ratio[1])
        capture_right = int(window_width * ratio[2])
        capture_bottom = int(window_height * ratio[3])
        capture_width = capture_right - capture_left
        capture_height = capture_bottom - capture_top
        if capture_width <= 0 or capture_height <= 0:
            raise ValueError("Computed capture area is empty")

        hwnd = self.hwnd
        window_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(window_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        area_bitmap = win32ui.CreateBitmap()
        area_bitmap.CreateCompatibleBitmap(mfc_dc, capture_width, capture_height)
        prev_obj = save_dc.SelectObject(area_bitmap)
        try:
            save_dc.BitBlt(
                (0, 0),
                (capture_width, capture_height),
                mfc_dc,
                (capture_left + self.offset_x, capture_top + self.offset_y),
                win32con.SRCCOPY,
            )
            bmpinfo = area_bitmap.GetInfo()
            signed_ints_array = area_bitmap.GetBitmapBits(True)
            img = np.frombuffer(signed_ints_array, dtype="uint8")
            img.shape = (bmpinfo["bmHeight"], bmpinfo["bmWidth"], 4)
        finally:
            self._release(window_dc, mfc_dc, save_dc, area_bitmap, prev_obj, hwnd)

        img = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
        standardized_width = capture_width * imgconfig.SCREEN_STANDARD_SIZE[0] // window_width
        standardized_height = capture_height * imgconfig.SCREEN_STANDARD_SIZE[1] // window_height
        return cv2.resize(img, (standardized_width, standardized_height))

    def disconnect(self):
        # Nothing persistent — GDI objects are per-call. Kept for API parity.
        pass

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
