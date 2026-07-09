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

    def test_precise_pause_timing_enters_bullet_and_steps_to_target(self):
        from src.logic import perform_action

        frame = {"value": 0}
        clicks = []
        sleeps = []

        def advance_frame(*_args, **_kwargs):
            frame["value"] += 1
            return True

        with (
            patch.object(perform_action.actionconfig, "BULLET_TIME_FRAMES", 30),
            patch.object(perform_action.actionconfig, "PRECISE_PAUSE_FRAMES", 10),
            patch.object(perform_action.actionconfig, "FRAME_STEP_INTERVAL", 0.008),
            patch.object(perform_action.actionconfig, "PAUSE_VERIFY_STABLE_TIME", 0.0),
            patch.object(perform_action.actionconfig, "MINIMUM_WAITTIME", 0.0),
            patch.object(perform_action, "get_game_time", side_effect=lambda: frame["value"]),
            patch.object(perform_action, "wait_for_game_time_update", side_effect=advance_frame),
            patch.object(perform_action, "mouseclick", side_effect=lambda pos: clicks.append((frame["value"], pos))),
            patch.object(perform_action.time, "sleep", side_effect=lambda value: sleeps.append(value)),
        ):
            perform_action._enter_precise_pause(100, (0.95, 0.9), lambda: False)

        pause_clicks = [
            frame for frame, pos in clicks
            if pos == perform_action.ratioconfig.PAUSE_BUTTON_RATIO
        ]
        focus_clicks = [
            (frame, pos) for frame, pos in clicks
            if pos != perform_action.ratioconfig.PAUSE_BUTTON_RATIO
        ]
        self.assertEqual(focus_clicks, [(70, (0.95, 0.9))])
        self.assertEqual(pause_clicks[0], 90)
        self.assertEqual(pause_clicks[-1], 99)
        self.assertEqual(frame["value"], 100)
        self.assertIn(0.008, sleeps)

    def test_deploy_executes_while_paused_at_target_then_resumes(self):
        from src.logic import perform_action
        from src.logic.action import Action, ActionType, DirectionType

        frame = {"value": 0}
        events = []
        clicks = []
        sleeps = []

        def advance_frame(*_args, **_kwargs):
            frame["value"] += 1
            return True

        def record_sleep(value):
            sleeps.append(value)
            if value == 0.123:
                events.append(("sleep", frame["value"], value))

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
            patch.object(perform_action.actionconfig, "BULLET_TIME_FRAMES", 30),
            patch.object(perform_action.actionconfig, "PRECISE_PAUSE_FRAMES", 10),
            patch.object(perform_action.actionconfig, "FRAME_STEP_INTERVAL", 0.008),
            patch.object(perform_action.actionconfig, "PAUSE_VERIFY_STABLE_TIME", 0.0),
            patch.object(perform_action.actionconfig, "DRAG_STEPS", 1),
            patch.object(perform_action.actionconfig, "DRAG_HOLD_TIME", 0.0),
            patch.object(perform_action.actionconfig, "DRAG_STEP_WAIT", 0.0),
            patch.object(perform_action.actionconfig, "MINIMUM_WAITTIME", 0.0),
            patch.object(perform_action.actionconfig, "DEPLOY_TO_DIRECTION_WAIT", 0.123),
            patch.object(perform_action, "get_game_time", side_effect=lambda: frame["value"]),
            patch.object(perform_action, "wait_for_game_time_update", side_effect=advance_frame),
            patch.object(perform_action, "locate_avatar", side_effect=lambda a: setattr(a, "avatar_pos", (0.8, 0.9))),
            patch.object(perform_action, "mouseclick", side_effect=lambda pos: clicks.append((frame["value"], pos))),
            patch.object(perform_action, "mousedown", side_effect=lambda pos: events.append(("down", frame["value"], pos))),
            patch.object(perform_action, "mousemove", side_effect=lambda pos: events.append(("move", frame["value"], pos))),
            patch.object(perform_action, "mouseup", side_effect=lambda pos: events.append(("up", frame["value"], pos))),
            patch.object(perform_action.time, "sleep", side_effect=record_sleep),
        ):
            perform_action.perform_action(action, lambda: False)

        pause_clicks = [
            frame for frame, pos in clicks
            if pos == perform_action.ratioconfig.PAUSE_BUTTON_RATIO
        ]
        self.assertIn((70, (0.95, 0.9)), clicks)
        self.assertEqual(pause_clicks[0], 90)
        self.assertEqual(pause_clicks[-1], 100)
        self.assertEqual(events[0], ("down", 100, (0.8, 0.9)))
        self.assertEqual(events[-1], ("up", 100, (0.7, 0.5)))
        self.assertEqual([kind for kind, *_ in events].count("down"), 2)
        self.assertEqual([kind for kind, *_ in events].count("up"), 2)
        deploy_release_idx = events.index(("up", 100, (0.5, 0.52)))
        direction_begin_idx = events.index(("down", 100, (0.5, 0.5)))
        self.assertLess(deploy_release_idx, direction_begin_idx)
        self.assertEqual(events[deploy_release_idx + 1], ("sleep", 100, 0.123))
        self.assertIn(0.123, sleeps)

    def test_precise_pause_retries_when_frame_keeps_advancing(self):
        from src.logic import perform_action
        from src.logic.action import Action, ActionType, DirectionType

        frame = {"value": 0}
        ops = []
        verify_attempts = {"count": 0}

        def advance_frame(*_args, **_kwargs):
            frame["value"] += 1
            return True

        def verify_pause(_duration, _user_paused):
            verify_attempts["count"] += 1
            if verify_attempts["count"] == 1:
                frame["value"] += 1
                return False
            return True

        action = Action(
            frame=100,
            action_type=ActionType.DEPLOY,
            oper="斑点",
            pos="C3",
            direction=DirectionType.NONE,
            avatar_pos=(0.8, 0.9),
            view_pos_side=(0.5, 0.5),
        )

        with (
            patch.object(perform_action.actionconfig, "BULLET_TIME_FRAMES", 30),
            patch.object(perform_action.actionconfig, "PRECISE_PAUSE_FRAMES", 10),
            patch.object(perform_action.actionconfig, "FRAME_STEP_INTERVAL", 0.008),
            patch.object(perform_action.actionconfig, "DRAG_STEPS", 1),
            patch.object(perform_action.actionconfig, "DRAG_HOLD_TIME", 0.0),
            patch.object(perform_action.actionconfig, "DRAG_STEP_WAIT", 0.0),
            patch.object(perform_action.actionconfig, "MINIMUM_WAITTIME", 0.0),
            patch.object(perform_action, "get_game_time", side_effect=lambda: frame["value"]),
            patch.object(perform_action, "wait_for_game_time_update", side_effect=advance_frame),
            patch.object(perform_action, "_frame_stable_for", side_effect=verify_pause),
            patch.object(perform_action, "locate_avatar", side_effect=lambda a: setattr(a, "avatar_pos", (0.8, 0.9))),
            patch.object(perform_action, "mouseclick", side_effect=lambda pos: ops.append(("click", frame["value"], pos))),
            patch.object(perform_action, "mousedown", side_effect=lambda pos: ops.append(("down", frame["value"], pos))),
            patch.object(perform_action, "mousemove", return_value=None),
            patch.object(perform_action, "mouseup", return_value=None),
            patch.object(perform_action.time, "sleep", return_value=None),
        ):
            perform_action.perform_action(action, lambda: False)

        pause_click_indices = [
            idx for idx, op in enumerate(ops)
            if op[0] == "click"
            and op[2] == perform_action.ratioconfig.PAUSE_BUTTON_RATIO
        ]
        first_drag_idx = ops.index(("down", 100, (0.8, 0.9)))
        pause_before_drag = [idx for idx in pause_click_indices if idx < first_drag_idx]
        self.assertGreaterEqual(len(pause_before_drag), 2)
        self.assertEqual(ops[first_drag_idx], ("down", 100, (0.8, 0.9)))

    def test_user_pause_before_precise_pause_does_not_toggle_pause(self):
        from src.logic import perform_action
        from src.logic.action import Action, ActionType

        clicks = []
        action = Action(
            frame=100,
            action_type=ActionType.SKILL,
            oper="斑点",
            view_pos_front=(0.4, 0.5),
        )

        with (
            patch.object(perform_action, "get_game_time", return_value=0),
            patch.object(perform_action, "mouseclick", side_effect=lambda pos: clicks.append(pos)),
        ):
            with self.assertRaises(perform_action.UserPausedError):
                perform_action.perform_action(action, lambda: True)

        self.assertEqual(clicks, [])

    def test_user_pause_at_target_keeps_game_paused(self):
        from src.logic import perform_action
        from src.logic.action import Action, ActionType

        frame = {"value": 0}
        clicks = []

        def advance_frame(*_args, **_kwargs):
            frame["value"] += 1
            return True

        action = Action(
            frame=100,
            action_type=ActionType.SKILL,
            oper="斑点",
            view_pos_front=(0.4, 0.5),
        )

        with (
            patch.object(perform_action.actionconfig, "BULLET_TIME_FRAMES", 30),
            patch.object(perform_action.actionconfig, "PRECISE_PAUSE_FRAMES", 10),
            patch.object(perform_action.actionconfig, "FRAME_STEP_INTERVAL", 0.008),
            patch.object(perform_action.actionconfig, "PAUSE_VERIFY_STABLE_TIME", 0.0),
            patch.object(perform_action.actionconfig, "MINIMUM_WAITTIME", 0.0),
            patch.object(perform_action, "get_game_time", side_effect=lambda: frame["value"]),
            patch.object(perform_action, "wait_for_game_time_update", side_effect=advance_frame),
            patch.object(perform_action, "mouseclick", side_effect=lambda pos: clicks.append((frame["value"], pos))),
            patch.object(perform_action.time, "sleep", return_value=None),
        ):
            with self.assertRaises(perform_action.UserPausedError):
                perform_action.perform_action(action, lambda: frame["value"] >= 100)

        pause_clicks = [
            frame for frame, pos in clicks
            if pos == perform_action.ratioconfig.PAUSE_BUTTON_RATIO
        ]
        action_clicks = [
            (frame, pos) for frame, pos in clicks
            if pos != perform_action.ratioconfig.PAUSE_BUTTON_RATIO
        ]
        self.assertEqual(action_clicks, [(70, (0.4, 0.5))])
        self.assertEqual(pause_clicks[0], 90)
        self.assertEqual(pause_clicks[-1], 99)

    def test_skill_uses_same_precise_pause_flow(self):
        from src.logic import perform_action
        from src.logic.action import Action, ActionType

        frame = {"value": 0}
        clicks = []

        def advance_frame(*_args, **_kwargs):
            frame["value"] += 1
            return True

        action = Action(
            frame=100,
            action_type=ActionType.SKILL,
            oper="斑点",
            view_pos_front=(0.4, 0.5),
        )

        with (
            patch.object(perform_action.actionconfig, "BULLET_TIME_FRAMES", 30),
            patch.object(perform_action.actionconfig, "PRECISE_PAUSE_FRAMES", 10),
            patch.object(perform_action.actionconfig, "FRAME_STEP_INTERVAL", 0.008),
            patch.object(perform_action.actionconfig, "PAUSE_VERIFY_STABLE_TIME", 0.0),
            patch.object(perform_action.actionconfig, "MINIMUM_WAITTIME", 0.0),
            patch.object(perform_action, "get_game_time", side_effect=lambda: frame["value"]),
            patch.object(perform_action, "wait_for_game_time_update", side_effect=advance_frame),
            patch.object(perform_action, "mouseclick", side_effect=lambda pos: clicks.append((frame["value"], pos))),
            patch.object(perform_action.time, "sleep", return_value=None),
        ):
            perform_action.perform_action(action, lambda: False)

        pause_clicks = [
            frame for frame, pos in clicks
            if pos == perform_action.ratioconfig.PAUSE_BUTTON_RATIO
        ]
        self.assertEqual(clicks[0], (70, (0.4, 0.5)))
        self.assertEqual(clicks[-2], (100, perform_action.ratioconfig.SKILL_RATIO))
        self.assertEqual(clicks[-1], (100, perform_action.ratioconfig.PAUSE_BUTTON_RATIO))
        self.assertEqual(pause_clicks[0], 90)
        self.assertEqual(pause_clicks[-1], 100)

    def test_retreat_uses_same_precise_pause_flow(self):
        from src.logic import perform_action
        from src.logic.action import Action, ActionType

        frame = {"value": 0}
        clicks = []

        def advance_frame(*_args, **_kwargs):
            frame["value"] += 1
            return True

        action = Action(
            frame=100,
            action_type=ActionType.RETREAT,
            oper="斑点",
            view_pos_front=(0.4, 0.5),
        )

        with (
            patch.object(perform_action.actionconfig, "BULLET_TIME_FRAMES", 30),
            patch.object(perform_action.actionconfig, "PRECISE_PAUSE_FRAMES", 10),
            patch.object(perform_action.actionconfig, "FRAME_STEP_INTERVAL", 0.008),
            patch.object(perform_action.actionconfig, "PAUSE_VERIFY_STABLE_TIME", 0.0),
            patch.object(perform_action.actionconfig, "MINIMUM_WAITTIME", 0.0),
            patch.object(perform_action, "get_game_time", side_effect=lambda: frame["value"]),
            patch.object(perform_action, "wait_for_game_time_update", side_effect=advance_frame),
            patch.object(perform_action, "mouseclick", side_effect=lambda pos: clicks.append((frame["value"], pos))),
            patch.object(perform_action.time, "sleep", return_value=None),
        ):
            perform_action.perform_action(action, lambda: False)

        pause_clicks = [
            frame for frame, pos in clicks
            if pos == perform_action.ratioconfig.PAUSE_BUTTON_RATIO
        ]
        self.assertEqual(clicks[0], (70, (0.4, 0.5)))
        self.assertEqual(clicks[-2], (100, perform_action.ratioconfig.RETREAT_RATIO))
        self.assertEqual(clicks[-1], (100, perform_action.ratioconfig.PAUSE_BUTTON_RATIO))
        self.assertEqual(pause_clicks[0], 90)
        self.assertEqual(pause_clicks[-1], 100)


if __name__ == "__main__":
    unittest.main()
