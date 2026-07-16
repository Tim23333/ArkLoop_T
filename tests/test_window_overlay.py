import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.desktop import window_overlay


class _Handle:
    def ToInt64(self):
        return 1234


class WindowOverlayBoundsTests(unittest.TestCase):
    def test_begin_drag_uses_native_caption_move_loop(self):
        controller = object.__new__(window_overlay.WindowOverlayController)
        controller._mode_lock = __import__("threading").RLock()
        controller._overlay_enabled = True
        controller._locked = False
        controller._get_hwnd = Mock(return_value=1234)
        controller._invoke_native = lambda callback: callback()

        user32 = SimpleNamespace(
            ReleaseCapture=Mock(return_value=True),
            SendMessageW=Mock(return_value=0),
        )
        with patch.object(
            window_overlay.ctypes,
            "windll",
            SimpleNamespace(user32=user32),
        ):
            result = controller.begin_drag()

        self.assertTrue(result["ok"])
        user32.ReleaseCapture.assert_called_once_with()
        user32.SendMessageW.assert_called_once_with(
            1234,
            window_overlay._WM_NCLBUTTONDOWN,
            window_overlay._HTCAPTION,
            0,
        )

    def test_set_bounds_passes_integer_size_to_set_window_pos(self):
        controller = object.__new__(window_overlay.WindowOverlayController)
        controller.window = SimpleNamespace(native=SimpleNamespace(Handle=_Handle()))

        user32 = SimpleNamespace(
            GetDpiForWindow=Mock(return_value=144),
            SetWindowPos=Mock(return_value=True),
        )
        with patch.object(
            window_overlay.ctypes,
            "windll",
            SimpleNamespace(user32=user32),
        ):
            controller.set_bounds(20, 30, 946, 666)

        args = user32.SetWindowPos.call_args.args
        self.assertEqual(args[2:6], (30, 45, 1419, 999))
        self.assertTrue(all(isinstance(value, int) for value in args[2:6]))
        self.assertNotIn(None, args[2:6])

    def test_set_mode_restore_does_not_call_pywebview_move(self):
        fake_window = SimpleNamespace(
            x=20,
            y=30,
            width=946,
            height=666,
            resize=Mock(),
            move=Mock(side_effect=AssertionError("pywebview move must not be used")),
        )
        with patch.object(window_overlay.sys, "platform", "test"):
            controller = window_overlay.WindowOverlayController(fake_window, Mock())
        controller._hotkey_ready.set()
        controller._apply_overlay_chrome = Mock()
        controller._apply_rounded_region = Mock()
        controller._clear_rounded_region = Mock()
        controller._restore_normal_chrome = Mock()
        controller._restore_normal_bounds = Mock()
        controller._restore_normal_window_state = Mock()

        controller.set_mode(True)
        controller.set_mode(False)

        fake_window.move.assert_not_called()
        controller._restore_normal_bounds.assert_called_once_with(
            {"x": 20, "y": 30, "width": 946, "height": 666}
        )


if __name__ == "__main__":
    unittest.main()
