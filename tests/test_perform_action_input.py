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

    def test_pause_sends_escape_to_mumu_window(self):
        from src.mumu import mumu_controller

        sent = []
        with (
            patch.object(mumu_controller, "get_handle", return_value=123),
            patch.object(mumu_controller.win32api, "MapVirtualKey", return_value=1),
            patch.object(mumu_controller.win32api, "SendMessage", side_effect=lambda *args: sent.append(args)),
        ):
            mumu_controller.pause()

        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0][0], 123)
        self.assertEqual(sent[0][1], mumu_controller.win32con.WM_KEYDOWN)
        self.assertEqual(sent[0][2], mumu_controller.win32con.VK_ESCAPE)
        self.assertEqual(sent[1][1], mumu_controller.win32con.WM_KEYUP)
        self.assertEqual(sent[1][2], mumu_controller.win32con.VK_ESCAPE)

    def test_deploy_placement_accepts_raised_selected_avatar(self):
        from src.logic import perform_action
        from src.logic.action import Action

        action = Action(oper="斑点")

        with (
            patch.object(perform_action, "_deploy_avatar_match_info", return_value=(0.94, 0.89)),
            patch.object(perform_action.actionconfig, "MINIMUM_WAITTIME", 0.0),
            patch.object(perform_action.time, "sleep", return_value=None),
        ):
            self.assertTrue(perform_action._deploy_placement_succeeded(action))

    def test_deploy_placement_rejects_bottom_avatar(self):
        from src.logic import perform_action
        from src.logic.action import Action

        action = Action(oper="斑点")

        with (
            patch.object(perform_action, "_deploy_avatar_match_info", return_value=(0.94, 0.91)),
            patch.object(perform_action.actionconfig, "MINIMUM_WAITTIME", 0.0),
            patch.object(perform_action.time, "sleep", return_value=None),
        ):
            self.assertFalse(perform_action._deploy_placement_succeeded(action))

    def test_close_deploy_does_not_toggle_pause(self):
        from src.logic import perform_action
        from src.logic.action import Action, ActionType, DirectionType

        frame = {"value": 100}
        pause_calls = []

        def advance_frame(*_args, **_kwargs):
            frame["value"] += 1
            return True

        def locate(action):
            self.assertEqual(pause_calls, [])
            action.avatar_pos = (0.8, 0.9)

        action = Action(
            frame=101,
            action_type=ActionType.DEPLOY,
            oper="鏂戠偣",
            pos="C3",
            direction=DirectionType.NONE,
            avatar_pos=(0.8, 0.9),
            view_pos_side=(0.5, 0.5),
        )

        with (
            patch.object(perform_action.actionconfig, "DEPLOY_PREPARE_FRAMES", 60),
            patch.object(perform_action.actionconfig, "DRAG_STEPS", 1),
            patch.object(perform_action.actionconfig, "DRAG_HOLD_TIME", 0.0),
            patch.object(perform_action.actionconfig, "DRAG_STEP_WAIT", 0.0),
            patch.object(perform_action.actionconfig, "MINIMUM_WAITTIME", 0.0),
            patch.object(perform_action.actionconfig, "GENERAL_WAITTIME", 0.0),
            patch.object(perform_action, "get_game_time", side_effect=lambda: frame["value"]),
            patch.object(perform_action, "wait_for_game_time_update", side_effect=advance_frame),
            patch.object(perform_action, "locate_avatar", side_effect=locate),
            patch.object(perform_action, "_deploy_placement_succeeded", return_value=True),
            patch.object(perform_action, "pause", side_effect=lambda: pause_calls.append(frame["value"])),
            patch.object(perform_action, "mouseclick", return_value=None),
            patch.object(perform_action, "mousedown", return_value=None),
            patch.object(perform_action, "mousemove", return_value=None),
            patch.object(perform_action, "mouseup", return_value=None),
            patch.object(perform_action.time, "sleep", return_value=None),
        ):
            actual = perform_action.perform_deploy(action, lambda: False, 15, 2)

        self.assertEqual(actual, 101)
        self.assertEqual(pause_calls, [])

    def test_direction_deploy_releases_before_direction_drag(self):
        from src.logic import perform_action
        from src.logic.action import Action, ActionType, DirectionType

        frame = {"value": 0}
        events = []
        pause_calls = []

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
            patch.object(perform_action, "_deploy_placement_succeeded", return_value=True),
            patch.object(perform_action, "pause", side_effect=lambda: pause_calls.append(frame["value"])),
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
        self.assertEqual([kind for kind, *_ in events].count("down"), 2)
        self.assertEqual([kind for kind, *_ in events].count("up"), 2)
        self.assertIn(("up", 70, (0.5, 0.52)), events)
        self.assertIn(("down", 70, (0.5, 0.5)), events)
        self.assertIn(("move", 70, (0.7, 0.5)), events)
        self.assertEqual(pause_calls, [0])

    def test_deploy_retries_placement_when_first_drag_not_accepted(self):
        from src.logic import perform_action
        from src.logic.action import Action, ActionType, DirectionType

        frame = {"value": 0}
        events = []
        pause_calls = []

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
            patch.object(perform_action, "_deploy_placement_succeeded", side_effect=[False, True]),
            patch.object(perform_action, "pause", side_effect=lambda: pause_calls.append(frame["value"])),
            patch.object(perform_action, "mouseclick", return_value=None),
            patch.object(perform_action, "mousedown", side_effect=lambda pos: events.append(("down", frame["value"], pos))),
            patch.object(perform_action, "mousemove", side_effect=lambda pos: events.append(("move", frame["value"], pos))),
            patch.object(perform_action, "mouseup", side_effect=lambda pos: events.append(("up", frame["value"], pos))),
            patch.object(perform_action.time, "sleep", return_value=None),
        ):
            actual = perform_action.perform_deploy(action, lambda: False, 15, 2)

        self.assertEqual(actual, 100)
        self.assertEqual([kind for kind, *_ in events].count("down"), 3)
        self.assertEqual([kind for kind, *_ in events].count("up"), 3)
        self.assertIn(("up", 70, (0.5, 0.52)), events)
        self.assertIn(("down", 71, (0.8, 0.9)), events)
        self.assertIn(("up", 71, (0.5, 0.52)), events)
        self.assertIn(("down", 71, (0.5, 0.5)), events)
        self.assertEqual(events[-1], ("up", 100, (0.7, 0.5)))
        self.assertEqual(pause_calls, [0])


if __name__ == "__main__":
    unittest.main()
