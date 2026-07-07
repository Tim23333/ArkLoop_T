"""Tests for the frame_offset / breakpoint / resume machinery."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from recorder.action_recognizer import ActionType, DirectionType, SemanticAction
from recorder.backend import AxisBuilder
from src.axis.axis_runner import AxisRunner, BreakpointHit
from src.logic.action import Action, ActionType as RunnerActionType, DirectionType as RunnerDirectionType


# ----------------------------------------------------------------------
# AxisBuilder frame_offset
# ----------------------------------------------------------------------
class AxisBuilderOffsetTests(unittest.TestCase):
    def _skill(self, frame: int) -> SemanticAction:
        return SemanticAction(
            action_type=ActionType.SKILL,
            oper="斑点",
            tile_pos=(3, 1),
            side=False,
            direction=DirectionType.NONE,
            game_time={"frame": frame},
        )

    def test_no_offset_writes_raw_frame(self):
        b = AxisBuilder(map_height=7, max_tick=30, frame_offset=0)
        b.on_semantic_action(self._skill(frame=70))
        axis = b.get_axis()
        self.assertEqual(axis[0]["frame"], 70)

    def test_offset_shifts_frame_on_emit(self):
        b = AxisBuilder(map_height=7, max_tick=30, frame_offset=100)
        b.on_semantic_action(self._skill(frame=7))
        b.on_semantic_action(self._skill(frame=90))
        axis = b.get_axis()
        self.assertEqual(axis[0]["frame"], 107)
        self.assertEqual(axis[1]["frame"], 190)


# ----------------------------------------------------------------------
# AxisRunner frame_offset + breakpoints (via stubs)
# ----------------------------------------------------------------------
def _make_action(frame: int, oper: str = "X") -> Action:
    return Action(
        frame=frame,
        action_type=RunnerActionType.SKILL,
        oper=oper,
    )


class _StubRunner(AxisRunner):
    """Replaces I/O-heavy parts so the action-iteration logic can be unit tested."""

    def __init__(self, *args, performed_log: list, gt_sequence: list, **kwargs):
        super().__init__(*args, **kwargs)
        self.performed_log = performed_log
        self._gt_iter = iter(gt_sequence)

    def run(self):  # type: ignore[override]
        # Skip breakpoints already past the frame_offset (they fired in a
        # previous session).
        bp_idx = 0
        while bp_idx < len(self.breakpoints):
            if self.breakpoints[bp_idx] <= self.frame_offset:
                bp_idx += 1
            else:
                break
        self._breakpoint_idx = bp_idx

        for action in self.actions:
            if self.is_paused():
                break
            action_frame = action.frame if action.frame is not None else 0
            if action_frame < self.frame_offset:
                continue
            target_frame = action_frame - self.frame_offset
            bp_idx = self._await_breakpoints_until(bp_idx, target_frame)
            if self.is_paused():
                break
            action.frame = target_frame
            self.performed_log.append((action.frame, action.oper))

    def _apply_settings(self):
        pass


def _patched_get_game_time(seq_iter):
    """Return a function that pulls frame values from the iterator."""
    def _gt():
        return next(seq_iter)
    return _gt


class AxisRunnerOffsetTests(unittest.TestCase):
    def test_actions_before_offset_are_skipped(self):
        actions = [
            _make_action(5, "a"),
            _make_action(70, "b"),
            _make_action(150, "c"),
            _make_action(157, "d"),
        ]
        log: list = []
        r = _StubRunner(
            actions=actions,
            settings={},
            is_paused=lambda: False,
            frame_offset=100,
            performed_log=log,
            gt_sequence=[],
        )
        r.run()
        # frame 5, 70 < 100 → skipped; 150, 157 → biased to 50, 57
        self.assertEqual([(a[0], a[1]) for a in log], [(50, "c"), (57, "d")])

    def test_offset_zero_passes_actions_unchanged(self):
        actions = [_make_action(0, "a"), _make_action(45, "b")]
        log: list = []
        r = _StubRunner(
            actions=actions,
            settings={},
            is_paused=lambda: False,
            frame_offset=0,
            performed_log=log,
            gt_sequence=[],
        )
        r.run()
        self.assertEqual([(a[0], a[1]) for a in log], [(0, "a"), (45, "b")])


class AxisRunnerBreakpointTests(unittest.TestCase):
    def test_breakpoint_before_action_pauses_and_fires_on_pause(self):
        actions = [_make_action(150, "skip_me")]
        log: list = []
        on_pause_calls: list = []

        r = _StubRunner(
            actions=actions,
            settings={},
            is_paused=lambda: False,
            frame_offset=0,
            breakpoints=[60],
            on_pause=lambda f: on_pause_calls.append(f),
            performed_log=log,
            gt_sequence=[75, 60],
        )

        with patch(
            "src.axis.axis_runner.get_game_time",
            side_effect=_patched_get_game_time(iter([75, 60])),
        ):
            r.run()

        self.assertEqual(log, [])
        self.assertEqual(on_pause_calls, [60])

    def test_breakpoint_after_all_actions_is_not_triggered_early(self):
        actions = [_make_action(30, "x")]
        log: list = []
        on_pause_calls: list = []

        r = _StubRunner(
            actions=actions,
            settings={},
            is_paused=lambda: False,
            frame_offset=0,
            breakpoints=[90],
            on_pause=lambda f: on_pause_calls.append(f),
            performed_log=log,
            gt_sequence=[],
        )
        with patch(
            "src.axis.axis_runner.get_game_time",
            side_effect=AssertionError("get_game_time should not be called"),
        ):
            r.run()

        self.assertEqual(log, [(30, "x")])
        self.assertEqual(on_pause_calls, [])

    def test_breakpoints_before_offset_are_ignored(self):
        actions = [_make_action(210, "x")]
        log: list = []
        on_pause_calls: list = []

        r = _StubRunner(
            actions=actions,
            settings={},
            is_paused=lambda: False,
            frame_offset=150,
            breakpoints=[60],
            on_pause=lambda f: on_pause_calls.append(f),
            performed_log=log,
            gt_sequence=[],
        )
        with patch(
            "src.axis.axis_runner.get_game_time",
            side_effect=AssertionError("get_game_time should not be called"),
        ):
            r.run()

        self.assertEqual(log, [(60, "x")])
        self.assertEqual(on_pause_calls, [])

    def test_stop_event_during_breakpoint_poll_aborts(self):
        actions = [_make_action(150, "x")]
        log: list = []

        stop_event = threading.Event()
        gt_seq = [0, 15]

        def _check():
            res = stop_event.is_set()
            stop_event.set()
            return res

        r = _StubRunner(
            actions=actions,
            settings={},
            is_paused=_check,
            frame_offset=0,
            breakpoints=[90],
            stop_event=stop_event,
            performed_log=log,
            gt_sequence=gt_seq,
        )

        with patch(
            "src.axis.axis_runner.get_game_time",
            side_effect=_patched_get_game_time(iter(gt_seq)),
        ):
            r.run()

        self.assertEqual(log, [])


class RunnerStateSeedTests(unittest.TestCase):
    """initial_state seeding + skipped-action state registration."""

    def _runner(self, **kwargs) -> AxisRunner:
        return AxisRunner(actions=[], settings={}, is_paused=lambda: False, **kwargs)

    def test_initial_state_seeds_deployed(self):
        r = self._runner(initial_state={"deployed": {"极境": (4, 1), "桃金娘": [2, 7]}})
        deployed = r.get_state()["deployed"]
        self.assertEqual(deployed["极境"], (4, 1))
        self.assertEqual(deployed["桃金娘"], (2, 7))

    def test_no_initial_state_is_empty(self):
        self.assertEqual(self._runner().get_state()["deployed"], {})

    def test_initial_state_ignores_malformed_entries(self):
        r = self._runner(initial_state={"deployed": {"a": (1,), "b": "xx", "c": (3, 4)}})
        self.assertEqual(r.get_state()["deployed"], {"c": (3, 4)})

    def test_skipped_deploy_registers_into_state(self):
        r = self._runner()
        a = Action(
            frame=15, action_type=RunnerActionType.DEPLOY,
            oper="斑点", pos="C3", direction=RunnerDirectionType.UP,
        )
        r._register_skipped_action(a, map_height=7, map_width=11)
        self.assertEqual(r.get_state()["deployed"]["斑点"], (4, 2))

    def test_skipped_retreat_removes_from_state(self):
        r = self._runner(initial_state={"deployed": {"斑点": (4, 2)}})
        a = Action(frame=30, action_type=RunnerActionType.RETREAT, oper="斑点")
        r._register_skipped_action(a, map_height=7, map_width=11)
        self.assertNotIn("斑点", r.get_state()["deployed"])

    def test_skipped_skill_leaves_state_untouched(self):
        r = self._runner(initial_state={"deployed": {"斑点": (4, 2)}})
        a = Action(frame=30, action_type=RunnerActionType.SKILL, oper="斑点")
        r._register_skipped_action(a, map_height=7, map_width=11)
        self.assertEqual(r.get_state()["deployed"], {"斑点": (4, 2)})


if __name__ == "__main__":
    unittest.main()
