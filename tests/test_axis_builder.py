"""Tests for recorder/backend.py AxisBuilder."""

from __future__ import annotations

import unittest

from recorder.action_recognizer import ActionType, DirectionType, SemanticAction
from recorder.backend import AxisBuilder


class AxisBuilderTests(unittest.TestCase):
    def setUp(self):
        self.builder = AxisBuilder(map_height=7, max_tick=30)

    def _deploy(self, oper, tile_pos, needs_direction=False, frame=0):
        return SemanticAction(
            action_type=ActionType.DEPLOY,
            oper=oper,
            tile_pos=tile_pos,
            side=True,
            direction=DirectionType.NONE,
            game_time={"frame": frame},
            needs_direction=needs_direction,
        )

    def _direction(self, oper, tile_pos, direction, frame):
        return SemanticAction(
            action_type=ActionType.DIRECTION,
            oper=oper,
            tile_pos=tile_pos,
            side=True,
            direction=direction,
            game_time={"frame": frame},
        )

    def _retreat(self, oper, tile_pos, frame=35):
        return SemanticAction(
            action_type=ActionType.RETREAT,
            oper=oper,
            tile_pos=tile_pos,
            side=False,
            direction=DirectionType.NONE,
            game_time={"frame": frame},
        )

    def _skill(self, oper, tile_pos, frame=70):
        return SemanticAction(
            action_type=ActionType.SKILL,
            oper=oper,
            tile_pos=tile_pos,
            side=False,
            direction=DirectionType.NONE,
            game_time={"frame": frame},
        )

    def test_retreat_and_skill_emitted_immediately(self):
        self.builder.on_semantic_action(self._retreat("Castle-3", (1, 4), frame=35))
        self.builder.on_semantic_action(self._skill("斑点", (3, 1), frame=70))
        axis = self.builder.get_axis()
        self.assertEqual(len(axis), 2)
        self.assertEqual(axis[0]["action_type"], "撤退")
        self.assertEqual(axis[0]["oper"], "Castle-3")
        self.assertEqual(axis[0]["frame"], 35)
        self.assertEqual(axis[1]["action_type"], "技能")
        self.assertEqual(axis[1]["oper"], "斑点")
        self.assertEqual(axis[1]["frame"], 70)
        self.assertNotIn("cost", axis[0])
        self.assertNotIn("cost", axis[1])

    def test_deploy_without_direction_emitted_immediately(self):
        deploy = self._deploy("Lancet-2", (4, 5), needs_direction=False, frame=3)
        self.builder.on_semantic_action(deploy)
        axis = self.builder.get_axis()
        self.assertEqual(len(axis), 1)
        self.assertEqual(axis[0]["action_type"], "部署")
        self.assertEqual(axis[0]["oper"], "Lancet-2")
        self.assertEqual(axis[0]["pos"], "C6")
        self.assertEqual(axis[0]["frame"], 3)
        self.assertNotIn("cost", axis[0])

    def test_deploy_with_direction_aggregates_direction_frame(self):
        deploy = self._deploy("斑点", (3, 1), needs_direction=True, frame=0)
        direction = self._direction("斑点", (3, 1), DirectionType.RIGHT, frame=7)
        self.builder.on_semantic_action(deploy)
        self.assertEqual(len(self.builder.get_axis()), 0)
        self.assertEqual(self.builder.pending_count(), 1)

        self.builder.on_semantic_action(direction)
        axis = self.builder.get_axis()
        self.assertEqual(len(axis), 1)
        self.assertEqual(axis[0]["action_type"], "部署")
        self.assertEqual(axis[0]["oper"], "斑点")
        self.assertEqual(axis[0]["direction"], "右")
        self.assertEqual(axis[0]["frame"], 7)
        self.assertEqual(self.builder.pending_count(), 0)
        self.assertNotIn("cost", axis[0])

    def test_multiple_pending_deploys(self):
        d1 = self._deploy("斑点", (3, 1), needs_direction=True, frame=0)
        d2 = self._deploy("Lancet-2", (4, 5), needs_direction=True, frame=0)
        self.builder.on_semantic_action(d1)
        self.builder.on_semantic_action(d2)
        self.assertEqual(self.builder.pending_count(), 2)

        # Direction for Lancet-2
        dir2 = self._direction("Lancet-2", (4, 5), DirectionType.UP, frame=3)
        self.builder.on_semantic_action(dir2)
        self.assertEqual(self.builder.pending_count(), 1)
        axis = self.builder.get_axis()
        self.assertEqual(len(axis), 1)
        self.assertEqual(axis[0]["oper"], "Lancet-2")
        self.assertEqual(axis[0]["frame"], 3)

    def test_clear_resets_state(self):
        self.builder.on_semantic_action(self._retreat("Castle-3", (1, 4), frame=35))
        self.builder.on_semantic_action(self._deploy("斑点", (3, 1), needs_direction=True, frame=0))
        self.assertEqual(len(self.builder.get_axis()), 1)  # retreat emitted
        self.assertEqual(self.builder.pending_count(), 1)

        self.builder.clear()
        self.assertEqual(len(self.builder.get_axis()), 0)
        self.assertEqual(self.builder.pending_count(), 0)

    def test_ignore_action_not_emitted(self):
        ignore = SemanticAction(
            action_type=ActionType.IGNORE,
            game_time={"frame": 5},
        )
        self.builder.on_semantic_action(ignore)
        self.assertEqual(len(self.builder.get_axis()), 0)


if __name__ == "__main__":
    unittest.main()
