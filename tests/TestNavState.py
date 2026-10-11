"""Unit tests for the pure navigation state and session data."""

import unittest

from src.nav.nav_state import (
    GridRouteRequest,
    NavSession,
    NavState,
    NavTick,
    SyncReason,
)
from src.nav.route_follower import (
    DONE,
    FAILED,
    NARROW_CALIBRATE,
    REPLAN,
    STUCK,
    TURN,
    WAIT,
    WALK,
    ZIP_LINE,
)


def _session(**kwargs) -> NavSession:
    request = GridRouteRequest(goal=(10.0, 20.0), stop_distance=1.5)
    return NavSession(request=request, deadline=100.0, limit=30.0, tick=0.2, **kwargs)


class TestNavState(unittest.TestCase):
    """状态名必须能直接承接跟随器动作。"""

    def test_execution_states_reuse_follower_action_values(self):
        for state, action in (
            (NavState.WALK, WALK),
            (NavState.TURN, TURN),
            (NavState.WAIT, WAIT),
            (NavState.NARROW_CALIBRATE, NARROW_CALIBRATE),
            (NavState.ZIP_LINE, ZIP_LINE),
            (NavState.STUCK, STUCK),
            (NavState.REPLAN, REPLAN),
            (NavState.DONE, DONE),
            (NavState.FAILED, FAILED),
        ):
            with self.subTest(action=action):
                self.assertEqual(state, action)

    def test_gate_states_do_not_collide_with_actions(self):
        """闸门状态不能和跟随器动作同值，否则动作会被误判成"还不能走"。"""
        gate_states = (
            NavState.WAIT_POSE,
            NavState.WAIT_ANCHOR,
            NavState.WAIT_TRUST,
            NavState.WAIT_ODOM,
            NavState.CALIBRATE,
            NavState.WAIT_HEADING,
        )
        actions = {DONE, FAILED, NARROW_CALIBRATE, REPLAN, STUCK, TURN, WAIT, WALK, ZIP_LINE}
        self.assertEqual(len(set(gate_states)), 6)
        for state in gate_states:
            with self.subTest(state=state):
                self.assertNotIn(state.value, actions)


class TestSyncReason(unittest.TestCase):
    """校准原因必须在日志里互相区分。"""

    def test_every_reason_has_a_non_empty_label(self):
        for reason in SyncReason:
            with self.subTest(reason=reason):
                self.assertTrue(reason.label.strip())

    def test_labels_are_unique(self):
        labels = [reason.label for reason in SyncReason]
        self.assertEqual(len(set(labels)), len(labels))


class TestNavSession(unittest.TestCase):
    """会话只保存本次导航有效、且旧实现摊在局部变量里的状态。"""

    def test_route_parameters_come_from_request(self):
        session = _session()
        self.assertEqual(session.goal, (10.0, 20.0))
        self.assertEqual(session.stop_distance, 1.5)
        self.assertIs(session.goal, session.request.goal)

    def test_note_progress_returns_quota_after_real_progress(self):
        session = _session(replans=3, recoveries=2, best_goal_distance=10.0)
        self.assertTrue(session.note_progress(9.0))
        self.assertEqual((session.replans, session.recoveries), (0, 0))
        self.assertEqual(session.best_goal_distance, 9.0)

    def test_note_progress_keeps_quota_without_progress(self):
        session = _session(replans=3, recoveries=2, best_goal_distance=10.0)
        self.assertFalse(session.note_progress(9.6))
        self.assertEqual((session.replans, session.recoveries), (3, 2))
        self.assertEqual(session.best_goal_distance, 10.0)

    def test_waypoint_arrival_counts_up(self):
        session = _session()
        self.assertEqual(session.note_waypoint_arrival(), 1)
        self.assertEqual(session.note_waypoint_arrival(), 2)
        self.assertEqual(session.waypoints_since_calibration, 2)

    def test_note_sync_clears_visited_cells_only_on_change(self):
        session = _session(visited_cells={(1, 2): (1.0, 2.0)})
        self.assertTrue(session.note_sync(4))
        self.assertEqual(session.visited_cells, {})
        self.assertEqual(session.last_sync_seq, 4)

        session.visited_cells[(1, 2)] = (1.0, 2.0)
        self.assertFalse(session.note_sync(4))
        self.assertEqual(session.visited_cells, {(1, 2): (1.0, 2.0)})


class TestNavTick(unittest.TestCase):
    """一拍快照：位置与三个会话快捷属性。"""

    def test_position_and_session_shortcuts(self):
        session = _session()
        ctx = NavTick(
            session=session,
            frame=None,
            pose={"x": 3.0, "z": 4.0},
            x=3.0,
            z=4.0,
            map_id="test",
            sync_seq=0,
            heading=None,
            heading_score=None,
        )
        self.assertEqual(ctx.position, (3.0, 4.0))
        self.assertEqual(ctx.tick, 0.2)
        self.assertEqual(ctx.deadline, 100.0)
        self.assertEqual(ctx.limit, 30.0)
        self.assertIsNone(ctx.step)
        self.assertIsNone(ctx.pending_zip_step)


if __name__ == "__main__":
    unittest.main()
