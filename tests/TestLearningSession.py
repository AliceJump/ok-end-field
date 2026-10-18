import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from src.core.learning.models import ContextConfidence, TargetPrior, VerificationEvidence
from src.core.learning.policy import deny_learning
from src.core.learning.session import create_learning_session
from src.core.learning.store import LearningStore
from tests.TestLearningCore import reliable_record, workflow_context


class TestLearningSession(unittest.TestCase):
    def evidence(self, kind="aligned", at=2, frame=2, target="pickup"):
        return VerificationEvidence(target, kind, at, frame)

    def test_action_prior_does_not_require_detection_and_is_taken_only_once(self):
        store = Mock()
        store.get.return_value = reliable_record()
        session = create_learning_session(workflow_context(), store, started_at=0)
        self.assertEqual(session.take_action_prior(), (100, 20))
        self.assertIsNone(session.take_action_prior())
        store.get.assert_called_once()
        self.assertTrue(session.fail(now=4))
        store.update.assert_called_once()

    def test_fresh_alignment_commits_replay_and_correction_without_arrival(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LearningStore(Path(directory) / "learning.json")
            context = workflow_context()
            session = create_learning_session(context, store, started_at=0)
            session.observe_input(100, 20, accepted=True, observed_at=1, after_frame_id=1)
            session.observe_input(5, -2, accepted=True, observed_at=2, after_frame_id=2)
            self.assertIsNone(store.get(context.fingerprint))
            target = TargetPrior(initial_position=(0.8, 0.5), aligned_position=(0.5, 0.5))
            self.assertTrue(session.confirm_alignment(self.evidence(at=3, frame=3), now=10, target=target))
            record = store.get(context.fingerprint)
            self.assertEqual((record.motion.mean_dx, record.motion.mean_dy), (105, 18))
            self.assertEqual(record.target, target)
            self.assertIn("aligned", record.provenance)
            self.assertTrue(session.closed)
            self.assertFalse(session.confirm_alignment(self.evidence(at=4, frame=4), now=11))
            self.assertEqual(store.get(context.fingerprint).success_count, 1)

    def test_unverified_same_frame_and_wrong_target_never_commit(self):
        for kind, at, frame, target in (
            ("aligned", 0, 2, "pickup"),
            ("aligned", 2, 1, "pickup"),
            ("aligned", 3, 3, "wrong"),
            ("arrived", 3, 3, "pickup"),
            ("interacted", 3, 3, "pickup"),
        ):
            with self.subTest(kind=kind, at=at, frame=frame, target=target):
                store = Mock()
                session = create_learning_session(workflow_context(), store, started_at=0)
                session.observe_input(100, 20, accepted=True, observed_at=1, after_frame_id=1)
                self.assertFalse(session.confirm_alignment(self.evidence(kind, at, frame, target), now=10))
                store.update.assert_not_called()

    def test_incomplete_input_and_disturbed_context_cannot_strengthen(self):
        for disturbance in ("rejected", "random_search", "extra_walk"):
            store = Mock()
            session = create_learning_session(workflow_context(), store, started_at=0)
            if disturbance == "rejected":
                session.observe_input(100, 20, accepted=False, observed_at=1, after_frame_id=1)
            else:
                session.disturb(disturbance, position_changed=disturbance == "extra_walk")
            self.assertFalse(session.confirm_alignment(self.evidence(), now=10))
            store.update.assert_not_called()

    def test_confidence_downgrades_never_upgrade_and_axes_are_enforced(self):
        store = Mock()
        store.get.return_value = reliable_record()
        context = workflow_context()
        context = replace(context, capture_profile=replace(context.capture_profile, axes="x"))
        session = create_learning_session(context, store, started_at=0)
        self.assertEqual(session.take_action_prior(), (100, 0))
        session.observe_input(100, 1, accepted=True, observed_at=1, after_frame_id=1)
        self.assertTrue(session.contaminated)
        session.disturb("walk", position_changed=True)
        session.disturb("rotation")
        self.assertEqual(session.confidence, ContextConfidence.LOW)

    def test_hard_invalid_does_not_create_session_or_read_store(self):
        store = Mock()
        with deny_learning("post_battle"):
            self.assertIsNone(create_learning_session(workflow_context(), store, started_at=0))
        for phase in ("battle", "battle/active", "post_battle", "post_battle_retry", "post-battle/claim"):
            self.assertIsNone(create_learning_session(workflow_context(phase=phase), store, started_at=0))
        store.assert_not_called()
        self.assertEqual(store.method_calls, [])

    def test_battle_invalidates_unconfirmed_sample_after_scope_restores(self):
        store = Mock()
        session = create_learning_session(workflow_context(), store, started_at=0)
        session.observe_input(100, 20, accepted=True, observed_at=1, after_frame_id=1)
        with deny_learning("battle"):
            pass
        self.assertFalse(session.confirm_alignment(self.evidence(), now=10))
        store.update.assert_not_called()
        fresh = create_learning_session(workflow_context(), store, started_at=4)
        self.assertIsNotNone(fresh)

    def test_closed_sample_and_absent_prior_do_not_create_failure_record(self):
        store = Mock()
        store.get.return_value = None
        session = create_learning_session(workflow_context(), store, started_at=0)
        self.assertIsNone(session.take_action_prior())
        self.assertFalse(session.fail(now=1))
        store.update.assert_not_called()
        session = create_learning_session(workflow_context(), store, started_at=0)
        session.close()
        self.assertFalse(session.confirm_alignment(self.evidence(), now=10))

    def test_navigation_input_and_failure_cannot_change_completed_alignment_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LearningStore(Path(directory) / "learning.json")
            context = workflow_context()
            session = create_learning_session(context, store, started_at=0)
            session.observe_input(100, 20, accepted=True, observed_at=1, after_frame_id=1)
            self.assertTrue(session.confirm_alignment(self.evidence(), now=10))
            before = store.get(context.fingerprint)
            session.observe_input(999, 999, accepted=True, observed_at=3, after_frame_id=3)
            self.assertFalse(session.fail(now=11))
            self.assertFalse(session.confirm_alignment(self.evidence(at=4, frame=4), now=12))
            self.assertEqual(store.get(context.fingerprint), before)

    def test_unverified_close_never_creates_record_and_zero_motion_alignment_can_learn(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LearningStore(Path(directory) / "learning.json")
            context = workflow_context()
            session = create_learning_session(context, store, started_at=0)
            session.close()
            self.assertIsNone(store.get(context.fingerprint))
            session = create_learning_session(context, store, started_at=0)
            self.assertTrue(session.confirm_alignment(self.evidence(), now=10))
            self.assertEqual(store.get(context.fingerprint).motion.samples, 1)
