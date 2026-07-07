"""Tests for recorder/axis_writer.py."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from recorder.action_recognizer import ActionType, DirectionType, SemanticAction
from recorder.axis_writer import AxisWriter
from src.axis.json_loader import load_axis_from_json


class AxisWriterTests(unittest.TestCase):
    def setUp(self):
        self.map_code = "1-7"
        self.analysis_data = {
            "metadata": {
                "video_path": "dummy.mp4",
                "timestamps_path": None,
                "fps": 30.0,
                "frame_count": 300,
                "duration": 10.0,
                "ticks_per_cycle": 30,
            },
            "frames": [
                {
                    "frame_id": 0,
                    "timestamp": 0.0,
                    "tick": 0,
                    "cycle": 0,
                    "total_elapsed_frames": 0,
                    "paused": False,
                },
                {
                    "frame_id": 60,
                    "timestamp": 2.0,
                    "tick": 0,
                    "cycle": 2,
                    "total_elapsed_frames": 60,
                    "paused": False,
                },
            ],
        }
        self.actions_data = {
            "version": 1,
            "actions": [
                {
                    "type": "drag",
                    "start_ts": 1.0,
                    "start_ratio": {"x": 0.8, "y": 0.9},
                    "end_ratio": {"x": 0.5, "y": 0.5},
                }
            ],
        }

    @patch("recorder.axis_writer.get_map_by_code")
    @patch("recorder.axis_writer.AvatarMatcher")
    def test_build_and_write(self, mock_avatar_matcher, mock_get_map):
        mock_get_map.return_value = {
            "levelId": "main_01-07",
            "code": "1-7",
            "name": "预备关卡",
            "height": 7,
            "width": 11,
            "tiles": [[{"buildableType": 1, "heightType": 0}] * 11 for _ in range(7)],
            "view": [[0.0, -4.81, -7.76], [0.0, -4.81, -7.76]],
        }

        fake_action = SemanticAction(
            action_type=ActionType.DEPLOY,
            oper="斑点",
            tile_pos=(3, 1),
            side=False,
            direction=DirectionType.RIGHT,
            game_time={"frame": 60},
            raw={"start_ts": 1.0},
        )
        recognizer_instance = mock_avatar_matcher.return_value
        recognizer_instance.recognize.return_value = [fake_action]

        writer = AxisWriter(self.analysis_data, self.actions_data, self.map_code)
        writer.recognizer = recognizer_instance

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "axis.json")
            writer.write(out_path)

            self.assertTrue(Path(out_path).is_file())
            actions, settings = load_axis_from_json(out_path)

        self.assertEqual(settings["map_code"], "1-7")
        self.assertEqual(settings["max_tick"], 30)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_type.name, "DEPLOY")
        self.assertEqual(actions[0].oper, "斑点")
        self.assertEqual(actions[0].pos, "D2")
        self.assertEqual(actions[0].direction.name, "RIGHT")
        self.assertEqual(actions[0].frame, 60)


if __name__ == "__main__":
    unittest.main()
