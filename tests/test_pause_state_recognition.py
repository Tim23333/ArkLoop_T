import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from src.maa.recognizer import MaaRecognizer


class PauseStateRecognitionTests(unittest.TestCase):
    def _recognizer(self, results):
        recognizer = MaaRecognizer.__new__(MaaRecognizer)
        recognizer._run_node = Mock(side_effect=results)
        return recognizer

    def test_paused_icon_has_priority(self):
        recognizer = self._recognizer([SimpleNamespace(hit=True)])

        state = recognizer.detect_pause_state(np.zeros((1, 1, 3), dtype=np.uint8))

        self.assertTrue(state)
        self.assertEqual(recognizer._run_node.call_count, 1)

    def test_speed_icon_confirms_running_state(self):
        recognizer = self._recognizer(
            [SimpleNamespace(hit=False), SimpleNamespace(hit=True)]
        )

        state = recognizer.detect_pause_state(np.zeros((1, 1, 3), dtype=np.uint8))

        self.assertFalse(state)

    def test_missing_pause_and_speed_icons_is_inconclusive(self):
        recognizer = self._recognizer([None, None, None])

        state = recognizer.detect_pause_state(np.zeros((1, 1, 3), dtype=np.uint8))

        self.assertIsNone(state)


if __name__ == "__main__":
    unittest.main()
