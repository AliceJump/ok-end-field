"""Pure learning identity, statistics, persistence and replay policy contracts."""

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.core.learning.models import (
    CaptureProfile,
    ContextConfidence,
    LearningRecord,
    MotionStatistics,
    RouteContext,
    TargetPrior,
    WorkflowContext,
    canonical_fingerprint,
    yaw_bucket,
)
from src.core.learning.policy import choose_hint, deny_learning, learning_allowed
from src.core.learning.store import LearningStore


def workflow_context(**changes):
    context = WorkflowContext(
        "delivery",
        "pickup",
        "武陵",
        "武陵城",
        "outbound_completed",
        "storage",
        "pickup",
        CaptureProfile(1920, 1080, "unscrolled"),
    )
    return replace(context, **changes)


def reliable_record():
    record = LearningRecord(target=TargetPrior(initial_position=(0.8, 0.5), aligned_position=(0.5, 0.5)))
    for dx in (100, 102, 98):
        record = record.successful(dx, 20, now=1, provenance=("delivery",))
    return record


@contextmanager
def fixed_cache_metadata(path):
    """Simulate timestamp/size collisions without changing directory metadata."""
    original_stat = Path.stat

    def stat(candidate, *args, **kwargs):
        if candidate.name == path.name:
            return SimpleNamespace(st_mtime_ns=123, st_size=456)
        return original_stat(candidate, *args, **kwargs)

    with patch.object(Path, "stat", stat):
        yield


class TestLearningCore(unittest.TestCase):
    def test_canonical_keys_and_yaw_wrap(self):
        self.assertEqual(
            canonical_fingerprint({"b": [1, 2], "a": "武陵"}), canonical_fingerprint({"a": "武陵", "b": [1, 2]})
        )
        self.assertNotEqual(canonical_fingerprint([1, 2]), canonical_fingerprint([2, 1]))
        self.assertEqual(yaw_bucket(359), yaw_bucket(1))
        for value in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                canonical_fingerprint({"number": value})
            with self.assertRaises(ValueError):
                yaw_bucket(value)

    def test_workflow_semantic_and_capture_isolation(self):
        context = workflow_context()
        for changes in (
            {"phase": "dropoff"},
            {"target_id": "npc"},
            {"area": "other"},
            {"location": "试验园区"},
            {"entry": "recovered"},
            {"origin_anchor": "other"},
            {"entry_route_signature": (64, 108)},
            {"start_yaw_bucket": 1},
            {"capture_profile": CaptureProfile(2560, 1080, "unscrolled")},
            {"capture_profile": CaptureProfile(1920, 1080, "scrolled")},
            {"capture_profile": CaptureProfile(1920, 1080, "unscrolled", axes="x")},
        ):
            with self.subTest(changes=changes):
                self.assertNotEqual(context.fingerprint, workflow_context(**changes).fingerprint)
        self.assertEqual(context.fingerprint, workflow_context().fingerprint)

    def test_unknown_or_mutable_context_fields_are_rejected(self):
        for changes in (
            {"location": ""},
            {"target_id": " npc "},
            {"entry_route_signature": [64]},
            {"start_yaw_bucket": True},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                workflow_context(**changes)
        with self.assertRaises(ValueError):
            CaptureProfile(True, 1080, "camera")

    def test_route_prefix_identity_forks_and_repeated_distances(self):
        route = RouteContext(
            "npc_a",
            (64, 108, 80, 45),
            2,
            CaptureProfile(1920, 1080, "camera"),
            0,
            ("area", "location", "origin", "out", "delivery"),
        )
        other = replace(route, config_key="npc_b", distances=(64, 108, 80, 72))
        self.assertNotEqual(route.fingerprint, other.fingerprint)
        self.assertEqual(route.shared_fingerprint, other.shared_fingerprint)
        self.assertNotEqual(
            replace(route, segment_index=3).shared_fingerprint, replace(other, segment_index=3).shared_fingerprint
        )
        self.assertNotEqual(route.shared_fingerprint, replace(route, distances=(64, 108, 96, 80)).shared_fingerprint)
        repeated = replace(route, distances=(64, 64, 64), segment_index=0)
        self.assertNotEqual(repeated.shared_fingerprint, replace(repeated, segment_index=1).shared_fingerprint)
        self.assertIsNone(replace(route, family=()).shared_fingerprint)
        self.assertNotEqual(
            route.shared_fingerprint,
            replace(route, family=("area", "other", "origin", "out", "delivery")).shared_fingerprint,
        )

    def test_online_variance_and_merge_match_raw_samples(self):
        all_samples = MotionStatistics()
        left, right = MotionStatistics(), MotionStatistics()
        for index, (dx, dy) in enumerate(((10, 5), (20, 0), (40, -5), (50, 10))):
            all_samples = all_samples.add(dx, dy)
            if index < 2:
                left = left.add(dx, dy)
            else:
                right = right.add(dx, dy)
        merged = left.merge(right)
        self.assertEqual(merged.samples, 4)
        for a, b in zip(
            (merged.mean_dx, merged.mean_dy, *merged.variance),
            (all_samples.mean_dx, all_samples.mean_dy, *all_samples.variance),
            strict=True,
        ):
            self.assertAlmostEqual(a, b)
        self.assertAlmostEqual(merged.variance[0], 1000 / 3)

    def test_legacy_variance_is_unknown_and_needs_new_samples(self):
        legacy = MotionStatistics(100, 100, 20, variance_known=False)
        self.assertIsNone(legacy.variance)
        merged = legacy.merge(MotionStatistics(2, 200, 40))
        self.assertAlmostEqual(merged.mean_dx, 10400 / 102)
        self.assertIsNone(merged.variance)
        fresh = legacy.add(90, 18)
        self.assertEqual((fresh.samples, fresh.legacy_samples), (1, 100))
        self.assertIsNone(fresh.variance)
        self.assertEqual(fresh.add(110, 22).legacy_samples, 100)

    def test_invalid_statistics_and_target_geometry_are_rejected(self):
        for changes in ({"samples": True}, {"mean_dx": float("nan")}, {"m2_dx": -1}, {"variance_known": 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                MotionStatistics(**changes)
        for args in ({"roi": (0.8, 0, 0.1, 1)}, {"initial_position": (2, 0)}, {"aligned_position": (float("inf"), 0)}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                TargetPrior(**args)

    def test_strength_is_capped_by_statistics_and_context(self):
        record = reliable_record()
        self.assertEqual(choose_hint(record, ContextConfidence.HIGH).action, (100, 20))
        self.assertEqual(choose_hint(record, ContextConfidence.MEDIUM).action, (50, 10))
        self.assertIsNone(choose_hint(record, ContextConfidence.LOW).action)
        self.assertIsNone(choose_hint(record, ContextConfidence.INVALID))
        unstable = replace(record, motion=MotionStatistics(3, 100, 20, 100000, 0))
        self.assertIsNone(choose_hint(unstable, ContextConfidence.HIGH).action)
        self.assertIsNone(choose_hint(record.failed(now=2), ContextConfidence.HIGH).action)
        self.assertIsNone(
            choose_hint(
                replace(record, motion=MotionStatistics(100, 100, 20, variance_known=False)), ContextConfidence.HIGH
            ).action
        )
        self.assertIsNone(choose_hint(record, ContextConfidence.HIGH, max_motion=10).action)

    def test_zero_motion_preserves_target_prior_and_provenance(self):
        record = LearningRecord().successful(
            0, 0, target=TargetPrior(roi=(0.1, 0.2, 0.9, 0.8)), now=1, provenance=("source",)
        )
        self.assertEqual(record.motion.samples, 1)
        self.assertIsNotNone(record.target.roi)
        self.assertEqual(record.provenance, ("source",))

    def test_store_roundtrip_and_independent_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.json"
            first, second = LearningStore(path), LearningStore(path)
            key = workflow_context().fingerprint
            self.assertIsNone(second.get(key))
            self.assertTrue(first.put(key, reliable_record()))
            self.assertEqual(second.get(key), reliable_record())
            copied = second.snapshot()
            copied.clear()
            self.assertEqual(first.get(key).success_count, 3)
            self.assertTrue(second.update(key, lambda record: record.failed(now=2)))
            self.assertEqual(first.get(key).failure_count, 1)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 1)

    def test_same_metadata_replace_invalidates_other_instances_and_path_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.json"
            (path.parent / "unused").mkdir()
            first = LearningStore(path)
            second = LearningStore(path.parent / "unused" / ".." / path.name)
            key = workflow_context().fingerprint
            other_key = workflow_context(target_id="other").fingerprint
            self.assertTrue(first.put(key, reliable_record()))
            with fixed_cache_metadata(path):
                self.assertEqual(first.get(key).failure_count, 0)
                self.assertTrue(second.update(key, lambda record: record.failed(now=2)))
                self.assertEqual(first.get(key).failure_count, 1)
                self.assertTrue(first.put(other_key, reliable_record()))
            records = LearningStore(path).snapshot()
            self.assertEqual(records[key].failure_count, 1)
            self.assertIn(other_key, records)

    def test_unknown_schema_is_preserved_until_readable_schema_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.json"
            original = '{"schema_version":99,"records":{"future":"keep me"}}'
            path.write_text(original, encoding="utf-8")
            store = LearningStore(path)
            key = workflow_context().fingerprint
            transform = Mock()
            with fixed_cache_metadata(path):
                self.assertEqual(store.snapshot(), {})
                self.assertFalse(store.put(key, reliable_record()))
                self.assertFalse(store.update(key, transform))
                transform.assert_not_called()
                self.assertEqual(path.read_text(encoding="utf-8"), original)
                path.write_text('{"schema_version":1,"records":{}}', encoding="utf-8")
                self.assertTrue(store.put(key, reliable_record()))
            self.assertEqual(LearningStore(path).get(key), reliable_record())

    def test_read_failure_preserves_file_and_retries_same_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.json"
            key = workflow_context().fingerprint
            writer, reader = LearningStore(path), LearningStore(path)
            self.assertTrue(writer.put(key, reliable_record()))
            original = path.read_bytes()
            with fixed_cache_metadata(path):
                with patch.object(Path, "read_text", side_effect=PermissionError("temporarily locked")):
                    self.assertIsNone(reader.get(key))
                    self.assertFalse(reader.put(key, reliable_record().failed(now=2)))
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(reader.get(key), reliable_record())
                self.assertTrue(reader.update(key, lambda record: record.failed(now=2)))
            self.assertEqual(LearningStore(path).get(key).failure_count, 1)

    def test_stat_failure_cannot_be_treated_as_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.json"
            key = workflow_context().fingerprint
            store = LearningStore(path)
            self.assertTrue(store.put(key, reliable_record()))
            original = path.read_bytes()
            with patch.object(Path, "stat", side_effect=PermissionError("stat denied")):
                self.assertIsNone(store.get(key))
                self.assertFalse(store.put(key, reliable_record().failed(now=2)))
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(store.get(key), reliable_record())

    def test_store_corrupt_payloads_and_records_fail_soft(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.json"
            key = workflow_context().fingerprint
            for contents in ("broken", "[]", '{"schema_version":99,"records":{}}', '{"schema_version":1,"records":[]}'):
                path.write_text(contents, encoding="utf-8")
                store = LearningStore(path)
                self.assertIsNone(store.get(key))
                self.assertFalse(store.put(key, reliable_record()))
                self.assertEqual(path.read_text(encoding="utf-8"), contents)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "records": {
                            key: reliable_record().to_dict(),
                            "invalid": {},
                            workflow_context(target_id="other").fingerprint: {"motion": {"samples": True}},
                        },
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(len(LearningStore(path).snapshot()), 1)

    def test_deeply_corrupt_json_cannot_break_navigation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.json"
            path.write_text("[" * 2000 + "]" * 2000, encoding="utf-8")
            self.assertEqual(LearningStore(path).snapshot(), {})

    def test_failed_atomic_replace_keeps_previous_file_and_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.json"
            store = LearningStore(path)
            key = workflow_context().fingerprint
            self.assertTrue(store.put(key, reliable_record()))
            before = path.read_bytes()
            signature = store._stamp()
            with patch("src.core.learning.store.os.replace", side_effect=PermissionError("locked")):
                self.assertFalse(store.update(key, lambda record: record.failed(now=2)))
            self.assertEqual(store._stamp(), signature)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(store.get(key).failure_count, 0)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_store_limits_keep_latest_successful_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LearningStore(Path(directory) / "learning.json", max_records=2)
            for index in range(4):
                self.assertTrue(
                    store.put(
                        workflow_context(target_id=str(index)).fingerprint, replace(reliable_record(), updated_at=index)
                    )
                )
            self.assertEqual(set(store.snapshot()), {workflow_context(target_id=str(i)).fingerprint for i in (2, 3)})

    def test_concurrent_updates_across_instances_do_not_lose_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.json"
            key = workflow_context().fingerprint

            def update(index):
                return LearningStore(path).update(key, lambda record: record.successful(index, 0, now=index))

            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertTrue(all(pool.map(update, range(12))))
            record = LearningStore(path).get(key)
            self.assertEqual(record.motion.samples, 12)
            self.assertAlmostEqual(record.motion.mean_dx, 5.5)

    def test_nested_denial_has_zero_store_io_and_restores_context(self):
        store = LearningStore("unused.json")
        key = workflow_context().fingerprint
        with patch.object(store, "_load", side_effect=AssertionError("must not load")):
            with deny_learning("post_battle"), deny_learning("after_transfer"):
                self.assertFalse(learning_allowed())
                self.assertIsNone(store.get(key))
                self.assertEqual(store.snapshot(), {})
                self.assertFalse(store.put(key, reliable_record()))
                self.assertFalse(store.update(key, lambda record: record))
            self.assertTrue(learning_allowed())
