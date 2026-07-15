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

    def test_deploy_releases_before_direction_drag(self):
        from src.logic import perform_action
        from src.logic.action import Action, ActionType, DirectionType

        events = []
        action = Action(
            frame=100,
            action_type=ActionType.DEPLOY,
            oper="斑点",
            pos="C3",
            direction=DirectionType.RIGHT,
            view_pos_side=(0.5, 0.5),
        )
        with (
            patch.object(perform_action.actionconfig, "DRAG_STEPS", 1),
            patch.object(perform_action.actionconfig, "DRAG_HOLD_TIME", 0.0),
            patch.object(perform_action.actionconfig, "DRAG_STEP_WAIT", 0.0),
            patch.object(perform_action.actionconfig, "MINIMUM_WAITTIME", 0.0),
            patch.object(perform_action.actionconfig, "DEPLOY_TO_DIRECTION_WAIT", 0.123),
            patch.object(perform_action, "locate_avatar", side_effect=lambda a: setattr(a, "avatar_pos", (0.8, 0.9))),
            patch.object(perform_action, "mouseclick", return_value=None),
            patch.object(perform_action, "mousedown", side_effect=lambda pos: events.append(("down", pos))),
            patch.object(perform_action, "mousemove", side_effect=lambda pos: events.append(("move", pos))),
            patch.object(perform_action, "mouseup", side_effect=lambda pos: events.append(("up", pos))),
            patch.object(perform_action.time, "sleep", side_effect=lambda value: events.append(("sleep", value))),
        ):
            perform_action.perform_deploy(action)

        deploy_release = events.index(("up", (0.5, 0.52)))
        direction_begin = events.index(("down", (0.5, 0.5)))
        self.assertLess(deploy_release, direction_begin)
        self.assertIn(("sleep", 0.123), events[deploy_release:direction_begin])

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
        self.assertEqual(sent[0][1], mumu_controller.win32con.WM_LBUTTONUP)
        self.assertEqual(sent[0][2], 0)

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
        self.assertEqual(sent[0][1], mumu_controller.win32con.WM_KEYDOWN)
        self.assertEqual(sent[1][1], mumu_controller.win32con.WM_KEYUP)


class PlaybackControllerTests(unittest.TestCase):
    def _action(self, action_type):
        from src.logic.action import Action, DirectionType

        return Action(
            frame=100,
            action_type=action_type,
            oper="斑点",
            pos="C3" if action_type.value == "部署" else None,
            direction=DirectionType.RIGHT,
            view_pos_front=(0.4, 0.5),
            view_pos_side=(0.5, 0.5),
        )

    def _run_controller(self, action):
        from src.axis import playback_controller

        frame = {"value": 0}
        paused = {"value": False}
        clicks = []
        executed = []
        sleeps = []

        def advance_frame(*_args, **_kwargs):
            frame["value"] += 1
            return True

        def click(pos):
            clicks.append((frame["value"], pos))
            if pos == playback_controller.ratioconfig.PAUSE_BUTTON_RATIO:
                paused["value"] = not paused["value"]

        controller = playback_controller.PlaybackController()
        with (
            patch.object(playback_controller.actionconfig, "BULLET_TIME_FRAMES", 30),
            patch.object(playback_controller.actionconfig, "PRECISE_PAUSE_FRAMES", 10),
            patch.object(playback_controller.actionconfig, "PAUSE_TOGGLE_MIN_INTERVAL", 0.0),
            patch.object(playback_controller.actionconfig, "FRAME_STEP_UPDATE_TIMEOUT", 0.1),
            patch.object(playback_controller.actionconfig, "FRAME_STEP_POLL_INTERVAL", 0.001),
            patch.object(playback_controller.actionconfig, "PAUSE_TOGGLE_SETTLE", 0.0),
            patch.object(playback_controller.actionconfig, "ACTION_RESUME_DELAY", 0.0),
            patch.object(playback_controller.actionconfig, "RESUME_TOGGLE_SETTLE", 0.0),
            patch.object(playback_controller.actionconfig, "MINIMUM_WAITTIME", 0.0),
            patch.object(playback_controller, "get_game_time", side_effect=lambda: frame["value"]),
            patch.object(playback_controller, "wait_for_game_time_update", side_effect=advance_frame),
            patch.object(playback_controller, "_image_reports_paused", side_effect=lambda: paused["value"]),
            patch.object(playback_controller, "mouseclick", side_effect=click),
            patch.object(playback_controller, "perform_deploy", side_effect=lambda a: executed.append((frame["value"], a.action_type))),
            patch.object(playback_controller, "perform_skill", side_effect=lambda a: executed.append((frame["value"], a.action_type))),
            patch.object(playback_controller, "perform_retreat", side_effect=lambda a: executed.append((frame["value"], a.action_type))),
            patch.object(playback_controller.time, "sleep", side_effect=lambda value: sleeps.append(value)),
        ):
            controller.execute(action)
        return controller, frame, clicks, executed, sleeps

    def test_skill_uses_precise_pause_and_resumes(self):
        from src.axis import playback_controller
        from src.logic.action import ActionType

        controller, frame, clicks, executed, sleeps = self._run_controller(
            self._action(ActionType.SKILL)
        )
        pause_clicks = [
            f for f, pos in clicks if pos == playback_controller.ratioconfig.PAUSE_BUTTON_RATIO
        ]
        self.assertEqual(clicks[0], (70, (0.4, 0.5)))
        self.assertEqual(pause_clicks[0], 90)
        self.assertEqual(pause_clicks[-1], 100)
        self.assertEqual(executed, [(100, ActionType.SKILL)])
        self.assertEqual(frame["value"], 100)
        self.assertFalse(controller.game_paused)

    def test_resume_retries_when_first_click_is_ignored(self):
        from src.axis import playback_controller

        controller = playback_controller.PlaybackController()
        controller._game_paused = True
        paused = {"value": True}
        clicks = []

        def click(_pos):
            clicks.append(True)
            if len(clicks) > 1:
                paused["value"] = False

        with (
            patch.object(playback_controller.actionconfig, "PAUSE_TOGGLE_MIN_INTERVAL", 0.0),
            patch.object(playback_controller.actionconfig, "RESUME_TOGGLE_SETTLE", 0.0),
            patch.object(playback_controller.actionconfig, "RESUME_VERIFY_RETRIES", 2),
            patch.object(playback_controller, "mouseclick", side_effect=click),
            patch.object(playback_controller, "_image_reports_paused", side_effect=lambda: paused["value"]),
            patch.object(playback_controller.time, "sleep", return_value=None),
        ):
            controller._resume_game("test action")

        self.assertEqual(len(clicks), 2)
        self.assertFalse(controller.game_paused)

    def test_pause_toggle_enforces_minimum_interval(self):
        from src.axis import playback_controller

        controller = playback_controller.PlaybackController()
        sleeps = []
        clock = iter([10.0, 10.0, 10.004, 10.020])

        with (
            patch.object(playback_controller.actionconfig, "PAUSE_TOGGLE_MIN_INTERVAL", 0.016),
            patch.object(playback_controller, "mouseclick", return_value=None),
            patch.object(playback_controller.time, "perf_counter", side_effect=lambda: next(clock)),
            patch.object(playback_controller.time, "sleep", side_effect=lambda value: sleeps.append(value)),
        ):
            controller._toggle_game_pause()
            controller._toggle_game_pause()

        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 0.012)

    def test_frame_step_waits_for_actual_frame_advance_before_pausing(self):
        from src.axis import playback_controller

        frame = {"value": 90}
        wake_count = {"value": 0}
        clicks = []
        controller = playback_controller.PlaybackController()
        controller._game_paused = True

        def delayed_advance(*_args, **_kwargs):
            wake_count["value"] += 1
            if wake_count["value"] == 3:
                frame["value"] += 1
            return True

        with (
            patch.object(playback_controller.actionconfig, "PAUSE_TOGGLE_MIN_INTERVAL", 0.0),
            patch.object(playback_controller.actionconfig, "FRAME_STEP_UPDATE_TIMEOUT", 1.0),
            patch.object(playback_controller.actionconfig, "FRAME_STEP_POLL_INTERVAL", 0.001),
            patch.object(playback_controller.actionconfig, "PAUSE_TOGGLE_SETTLE", 0.0),
            patch.object(playback_controller, "get_game_time", side_effect=lambda: frame["value"]),
            patch.object(playback_controller, "wait_for_game_time_update", side_effect=delayed_advance),
            patch.object(playback_controller, "_image_reports_paused", return_value=True),
            patch.object(playback_controller, "mouseclick", side_effect=lambda pos: clicks.append((frame["value"], pos))),
            patch.object(playback_controller.time, "sleep", return_value=None),
        ):
            controller._frame_step_until(91)

        self.assertEqual(wake_count["value"], 3)
        self.assertEqual(
            clicks,
            [
                (90, playback_controller.ratioconfig.PAUSE_BUTTON_RATIO),
                (91, playback_controller.ratioconfig.PAUSE_BUTTON_RATIO),
            ],
        )
        self.assertTrue(controller.game_paused)

    def test_retreat_uses_same_controller_flow(self):
        from src.logic.action import ActionType

        controller, _frame, _clicks, executed, _sleeps = self._run_controller(
            self._action(ActionType.RETREAT)
        )
        self.assertEqual(executed, [(100, ActionType.RETREAT)])
        self.assertEqual(controller.phase.value, "waiting_action")

    def test_pause_image_retry_occurs_before_action(self):
        from src.axis import playback_controller
        from src.logic.action import ActionType

        frame = {"value": 0}
        paused = {"value": False}
        clicks = []
        executed = []
        pause_click_count = {"value": 0}

        def advance_frame(*_args, **_kwargs):
            frame["value"] += 1
            return True

        def click(pos):
            clicks.append((frame["value"], pos))
            if pos != playback_controller.ratioconfig.PAUSE_BUTTON_RATIO:
                return
            pause_click_count["value"] += 1
            if pause_click_count["value"] > 1:
                paused["value"] = not paused["value"]

        controller = playback_controller.PlaybackController()
        with (
            patch.object(playback_controller.actionconfig, "PAUSE_TOGGLE_SETTLE", 0.0),
            patch.object(playback_controller.actionconfig, "ACTION_RESUME_DELAY", 0.0),
            patch.object(playback_controller.actionconfig, "RESUME_TOGGLE_SETTLE", 0.0),
            patch.object(playback_controller.actionconfig, "MINIMUM_WAITTIME", 0.0),
            patch.object(playback_controller, "get_game_time", side_effect=lambda: frame["value"]),
            patch.object(playback_controller, "wait_for_game_time_update", side_effect=advance_frame),
            patch.object(playback_controller, "_image_reports_paused", side_effect=lambda: paused["value"]),
            patch.object(playback_controller, "mouseclick", side_effect=click),
            patch.object(playback_controller, "perform_skill", side_effect=lambda _a: executed.append(frame["value"])),
            patch.object(playback_controller.time, "sleep", return_value=None),
        ):
            controller.execute(self._action(ActionType.SKILL))

        pause_before_action = [
            item for item in clicks
            if item[0] <= executed[0]
            and item[1] == playback_controller.ratioconfig.PAUSE_BUTTON_RATIO
        ]
        self.assertGreaterEqual(len(pause_before_action), 2)

    def test_pause_request_at_target_keeps_game_paused(self):
        from src.axis import playback_controller
        from src.logic.action import ActionType

        frame = {"value": 0}
        controller = playback_controller.PlaybackController()
        executed = []

        def advance_frame(*_args, **_kwargs):
            frame["value"] += 1
            if frame["value"] == 100:
                controller.request_pause()
            return True

        with (
            patch.object(playback_controller.actionconfig, "PAUSE_TOGGLE_SETTLE", 0.0),
            patch.object(playback_controller.actionconfig, "MINIMUM_WAITTIME", 0.0),
            patch.object(playback_controller, "get_game_time", side_effect=lambda: frame["value"]),
            patch.object(playback_controller, "wait_for_game_time_update", side_effect=advance_frame),
            patch.object(playback_controller, "_image_reports_paused", return_value=True),
            patch.object(playback_controller, "mouseclick", return_value=None),
            patch.object(playback_controller, "perform_skill", side_effect=lambda _a: executed.append(True)),
            patch.object(playback_controller.time, "sleep", return_value=None),
        ):
            with self.assertRaises(playback_controller.PlaybackInterrupted) as raised:
                controller.execute(self._action(ActionType.SKILL))

        self.assertEqual(raised.exception.mode, playback_controller.StopMode.PAUSE)
        self.assertFalse(executed)
        self.assertTrue(controller.game_paused)
        self.assertEqual(controller.phase, playback_controller.PlaybackPhase.PAUSED)

    def test_stop_request_before_action_does_not_pause_game(self):
        from src.axis import playback_controller
        from src.logic.action import ActionType

        controller = playback_controller.PlaybackController()
        controller.request_stop()
        clicks = []
        with (
            patch.object(playback_controller, "get_game_time", return_value=0),
            patch.object(playback_controller, "mouseclick", side_effect=lambda pos: clicks.append(pos)),
        ):
            with self.assertRaises(playback_controller.PlaybackInterrupted) as raised:
                controller.execute(self._action(ActionType.SKILL))

        self.assertEqual(raised.exception.mode, playback_controller.StopMode.STOP)
        self.assertEqual(clicks, [])
        self.assertEqual(controller.phase, playback_controller.PlaybackPhase.STOPPED)

    def test_stop_request_during_frame_step_resumes_before_stopping(self):
        from src.axis import playback_controller
        from src.logic.action import ActionType

        frame = {"value": 0}
        controller = playback_controller.PlaybackController()
        paused = {"value": False}
        clicks = []

        def advance_frame(*_args, **_kwargs):
            frame["value"] += 1
            if frame["value"] == 100:
                controller.request_stop()
            return True

        def click(pos):
            clicks.append((frame["value"], pos))
            if pos == playback_controller.ratioconfig.PAUSE_BUTTON_RATIO:
                paused["value"] = not paused["value"]

        with (
            patch.object(playback_controller.actionconfig, "PAUSE_TOGGLE_SETTLE", 0.0),
            patch.object(playback_controller.actionconfig, "RESUME_TOGGLE_SETTLE", 0.0),
            patch.object(playback_controller.actionconfig, "MINIMUM_WAITTIME", 0.0),
            patch.object(playback_controller, "get_game_time", side_effect=lambda: frame["value"]),
            patch.object(playback_controller, "wait_for_game_time_update", side_effect=advance_frame),
            patch.object(playback_controller, "_image_reports_paused", side_effect=lambda: paused["value"]),
            patch.object(playback_controller, "mouseclick", side_effect=click),
            patch.object(playback_controller, "perform_skill", side_effect=AssertionError("action must not execute")),
            patch.object(playback_controller.time, "sleep", return_value=None),
        ):
            with self.assertRaises(playback_controller.PlaybackInterrupted) as raised:
                controller.execute(self._action(ActionType.SKILL))

        pause_clicks = [
            item for item in clicks
            if item[1] == playback_controller.ratioconfig.PAUSE_BUTTON_RATIO
        ]
        self.assertEqual(raised.exception.mode, playback_controller.StopMode.STOP)
        self.assertEqual(pause_clicks[-1][0], 100)
        self.assertFalse(controller.game_paused)
        self.assertEqual(controller.phase, playback_controller.PlaybackPhase.STOPPED)


if __name__ == "__main__":
    unittest.main()
