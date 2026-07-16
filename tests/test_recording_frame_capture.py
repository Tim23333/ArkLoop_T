import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.input.action_recorder import ActionRecorder
from src.input.coordinate_mapper import MappedCoordinates
from src.input.mouse_listener import MouseEvent, MouseListener


class RecordingFrameCaptureTests(unittest.TestCase):
    def test_mouse_hook_captures_ws_frame_on_press_and_release(self):
        frames = iter([120, 123])
        listener = MouseListener(frame_provider=lambda: next(frames))
        listener._start_ts = 1.0
        button = SimpleNamespace(name="left")

        with patch.object(listener, "_is_mumu_foreground", return_value=True):
            listener._on_click(100, 200, button, True)
            listener._on_click(100, 200, button, False)

        self.assertEqual([event.frame for event in listener.events], [120, 123])

    def test_completed_action_uses_mouseup_frame(self):
        recorder = ActionRecorder(mouse_listener=MouseListener())
        mapped = MappedCoordinates(
            screen_x=100,
            screen_y=200,
            client_x=100.0,
            client_y=200.0,
            ratio_x=0.5,
            ratio_y=0.5,
            game_x=640.0,
            game_y=360.0,
            valid=True,
        )
        events = [
            MouseEvent("mousedown", 100, 200, "left", True, 1.0, 200),
            MouseEvent("mouseup", 100, 200, "left", False, 1.1, 203),
        ]

        with patch.object(recorder, "_map_event", return_value=mapped):
            actions = recorder._build_actions(events)

        self.assertEqual(actions[0]["start_frame"], 200)
        self.assertEqual(actions[0]["end_frame"], 203)


if __name__ == "__main__":
    unittest.main()
