import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class PerformActionInputTests(unittest.TestCase):
    def test_drag_mouse_holds_until_final_release(self):
        from src.logic import perform_action

        calls = []
        with (
            patch.object(perform_action.actionconfig, "DRAG_STEPS", 2),
            patch.object(perform_action.actionconfig, "DRAG_HOLD_TIME", 0.0),
            patch.object(perform_action.actionconfig, "DRAG_STEP_WAIT", 0.0),
            patch.object(perform_action.actionconfig, "MINIMUM_WAITTIME", 0.0),
            patch.object(perform_action, "mousedown", side_effect=lambda pos: calls.append(("down", pos))),
            patch.object(perform_action, "mousemove", side_effect=lambda pos: calls.append(("move", pos))),
            patch.object(perform_action, "mouseup", side_effect=lambda pos: calls.append(("up", pos))),
            patch.object(perform_action.time, "sleep", return_value=None),
        ):
            perform_action._drag_mouse((0.8, 0.9), (0.5, 0.5), via=(0.8, 0.82))

        self.assertEqual(calls[0], ("down", (0.8, 0.9)))
        self.assertEqual(calls[-1], ("up", (0.5, 0.5)))
        self.assertEqual([kind for kind, _ in calls].count("down"), 1)
        self.assertEqual([kind for kind, _ in calls].count("up"), 1)
        self.assertGreaterEqual([kind for kind, _ in calls].count("move"), 4)

    def test_mouseup_reports_left_button_released(self):
        from src.mumu import mumu_controller

        sent = []
        with (
            patch.object(mumu_controller, "get_handle", return_value=123),
            patch.object(mumu_controller.win32gui, "GetClientRect", return_value=(0, 0, 1000, 500)),
            patch.object(mumu_controller.win32api, "MAKELONG", return_value=456),
            patch.object(mumu_controller.win32api, "SendMessage", side_effect=lambda *args: sent.append(args)),
        ):
            mumu_controller.mouseup((0.5, 0.5))

        self.assertEqual(len(sent), 1)
        _handle, msg, wparam, _lparam = sent[0]
        self.assertEqual(msg, mumu_controller.win32con.WM_LBUTTONUP)
        self.assertEqual(wparam, 0)

    def test_deploy_holds_drag_until_target_frame(self):
        from src.logic import perform_action
        from src.logic.action import Action, ActionType, DirectionType

        frame = {"value": 0}
        events = []

        def advance_frame(*_args, **_kwargs):
            frame["value"] += 1
            return True

        action = Action(
            frame=100,
            action_type=ActionType.DEPLOY,
            oper="斑点",
            pos="C3",
            direction=DirectionType.RIGHT,
            avatar_pos=(0.8, 0.9),
            view_pos_side=(0.5, 0.5),
        )

        with (
            patch.object(perform_action.actionconfig, "DEPLOY_PREPARE_FRAMES", 60),
            patch.object(perform_action.actionconfig, "DEPLOY_DIRECTION_FRAMES", 30),
            patch.object(perform_action.actionconfig, "DRAG_STEPS", 1),
            patch.object(perform_action.actionconfig, "DRAG_HOLD_TIME", 0.0),
            patch.object(perform_action.actionconfig, "DRAG_STEP_WAIT", 0.0),
            patch.object(perform_action.actionconfig, "MINIMUM_WAITTIME", 0.0),
            patch.object(perform_action.actionconfig, "GENERAL_WAITTIME", 0.0),
            patch.object(perform_action, "get_game_time", side_effect=lambda: frame["value"]),
            patch.object(perform_action, "wait_for_game_time_update", side_effect=advance_frame),
            patch.object(perform_action, "locate_avatar", side_effect=lambda a: setattr(a, "avatar_pos", (0.8, 0.9))),
            patch.object(perform_action, "pause", return_value=None),
            patch.object(perform_action, "esc", return_value=None),
            patch.object(perform_action, "mouseclick", return_value=None),
            patch.object(perform_action, "mousedown", side_effect=lambda pos: events.append(("down", frame["value"], pos))),
            patch.object(perform_action, "mousemove", side_effect=lambda pos: events.append(("move", frame["value"], pos))),
            patch.object(perform_action, "mouseup", side_effect=lambda pos: events.append(("up", frame["value"], pos))),
            patch.object(perform_action.time, "sleep", return_value=None),
        ):
            actual = perform_action.perform_deploy(action, lambda: False, 15, 2)

        self.assertEqual(actual, 100)
        self.assertEqual(events[0], ("down", 40, (0.8, 0.9)))
        self.assertEqual(events[-1], ("up", 100, (0.7, 0.5)))
        self.assertEqual([kind for kind, *_ in events].count("down"), 1)
        self.assertEqual([kind for kind, *_ in events].count("up"), 1)
        self.assertIn(("move", 70, (0.7, 0.5)), events)


if __name__ == "__main__":
    unittest.main()
