import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from src.core.learning.models import ContextConfidence, VerificationEvidence
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

    def test_only_verified_total_including_replay_and_correction_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LearningStore(Path(directory) / "learning.json")
            context = workflow_context()
            session = create_learning_session(context, store, started_at=0)
            session.observe_input(100, 20, accepted=True, observed_at=1, after_frame_id=1)
            session.observe_input(5, -2, accepted=True, observed_at=2, after_frame_id=2)
            self.assertIsNone(store.get(context.fingerprint))
            self.assertFalse(session.aligned(self.evidence(at=1, frame=1)))
            self.assertFalse(session.aligned(self.evidence(at=3, frame=2)))
            self.assertFalse(session.aligned(self.evidence(at=3, frame=3, target="wrong")))
            self.assertTrue(session.aligned(self.evidence(at=3, frame=3)))
            self.assertTrue(session.confirm(self.evidence("arrived", 4, 4), now=10))
            record = store.get(context.fingerprint)
            self.assertEqual((record.motion.mean_dx, record.motion.mean_dy), (105, 18))
            self.assertFalse(session.confirm(self.evidence("arrived", 5, 5), now=11))
            self.assertEqual(store.get(context.fingerprint).success_count, 1)

    def test_unverified_same_frame_and_wrong_target_never_commit(self):
        for kind, at, frame, target, aligned in (
            ("arrived", 2, 2, "pickup", False),
            ("arrived", 2, 2, "pickup", True),
            ("arrived", 1, 3, "pickup", True),
            ("arrived", 3, 3, "wrong", True),
            ("aligned", 3, 3, "pickup", True),
        ):
            with self.subTest(kind=kind, at=at, frame=frame, target=target, aligned=aligned):
                store = Mock()
                session = create_learning_session(workflow_context(), store, started_at=0)
                if aligned:
                    session.aligned(self.evidence())
                self.assertFalse(session.confirm(self.evidence(kind, at, frame, target), now=10))
                store.update.assert_not_called()

    def test_incomplete_input_and_disturbed_context_cannot_strengthen(self):
        for disturbance in ("rejected", "random_search", "extra_walk"):
            store = Mock()
            session = create_learning_session(workflow_context(), store, started_at=0)
            if disturbance == "rejected":
                session.observe_input(100, 20, accepted=False, observed_at=1, after_frame_id=1)
            else:
                session.disturb(disturbance, position_changed=disturbance == "extra_walk")
            session.aligned(self.evidence())
            self.assertFalse(session.confirm(self.evidence("arrived", 3, 3), now=10))
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

    def test_battle_invalidates_preexisting_pending_sample_after_scope_restores(self):
        store = Mock()
        session = create_learning_session(workflow_context(), store, started_at=0)
        session.aligned(self.evidence())
        with deny_learning("battle"):
            pass
        self.assertFalse(session.confirm(self.evidence("arrived", 3, 3), now=10))
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
        self.assertFalse(session.confirm(self.evidence("arrived", 3, 3), now=10))

    def test_additional_input_invalidates_previous_alignment(self):
        store = Mock()
        session = create_learning_session(workflow_context(), store, started_at=0)
        self.assertTrue(session.aligned(self.evidence()))
        session.observe_input(5, 0, accepted=True, observed_at=3, after_frame_id=2)
        self.assertFalse(session.confirm(self.evidence("arrived", 4, 4), now=10))
        store.update.assert_not_called()
