"""Migration integrity, unknown variance, camera isolation and retry contracts."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.learning.models import CaptureProfile, LearningRecord
from src.core.learning.policy import deny_learning
from src.core.learning.route_migration import migrate_zip_line_cache
from src.core.learning.routes import RouteLearning, route_context_from_key
from src.core.learning.store import LearningStore
from tests.TestRouteLearning import route_context


def legacy_route(key="常沄", distances=(64, 108, 80, 45), *, count=5, dx=100, dy=20, bucket=18):
    route = json.dumps([key, distances], ensure_ascii=False)
    return json.dumps([route, [1920, 1080], bucket], ensure_ascii=False), {
        "segments": {"2": {"samples": count, "mean_dx": dx, "mean_dy": dy}}
    }


class TestRouteMigration(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "old.json"
        self.store = LearningStore(Path(self.directory.name) / "new.json")

    def write(self, *entries, version=4):
        self.path.write_text(
            json.dumps({"version": version, "routes": dict(entries)}, ensure_ascii=False), encoding="utf-8"
        )

    def migrate(self, **kwargs):
        return migrate_zip_line_cache(self.path, self.store, now=10, **kwargs)

    def mapped(self):
        return {
            "profile_resolver": lambda *_: route_context().capture_profile,
            "family_resolver": lambda _: route_context().family,
        }

    def source_records(self):
        return {key: record for key, record in self.store.snapshot().items() if route_context_from_key(key) is not None}

    def test_default_import_preserves_original_and_uses_private_unknown_camera(self):
        self.write(legacy_route())
        before = self.path.read_bytes()
        result = self.migrate()
        self.assertTrue(result.committed)
        self.assertEqual(result.imported, 1)
        key, record = next(iter(self.source_records().items()))
        context = route_context_from_key(key)
        self.assertEqual((context.family, context.capture_profile.camera), ((), "legacy-unknown"))
        self.assertEqual((record.motion.samples, record.motion.mean_dx), (5, 100))
        self.assertIsNone(record.motion.variance)
        self.assertFalse(record.motion.variance_known)
        self.assertIn("legacy-mean-may-be-ema", record.provenance)
        self.assertIsNone(RouteLearning(self.store).lookup(context).action)
        self.assertEqual(self.path.read_bytes(), before)

    def test_weighted_legacy_estimates_keep_sources_and_never_become_strong(self):
        self.write(legacy_route(count=5, dx=100), legacy_route("彦宁", count=15, dx=120))
        self.assertTrue(self.migrate(**self.mapped()).committed)
        learning = RouteLearning(self.store)
        records = self.source_records()
        shared = learning._shared(route_context(config_key="new"), records)
        self.assertEqual((shared.motion.samples, shared.motion.mean_dx), (20, 115))
        self.assertIsNone(shared.motion.variance)
        self.assertIsNone(learning.lookup(route_context(config_key="new")).action)
        self.assertEqual(len(records), 2)
        self.assertNotEqual(*[record.provenance for record in records.values()])

    def test_successful_import_is_idempotent_even_if_old_writer_changes_file(self):
        self.write(legacy_route())
        self.assertTrue(self.migrate(**self.mapped()).committed)
        before = self.store.snapshot()
        self.write(legacy_route(count=999, dx=999))
        result = self.migrate(**self.mapped())
        self.assertEqual((result.committed, result.imported), (True, 0))
        self.assertEqual(self.store.snapshot(), before)

    def test_existing_new_samples_win_and_start_clean_variance(self):
        context = route_context()
        learning = RouteLearning(self.store)
        learning.record_success(context, 99, 20, now=1)
        self.write(legacy_route(count=1000))
        self.assertTrue(self.migrate(**self.mapped()).committed)
        self.assertEqual(self.store.get(context.fingerprint).motion.samples, 1)

    def test_first_new_sample_keeps_legacy_count_separate_and_needs_new_warmup(self):
        self.write(legacy_route(count=1000))
        self.assertTrue(self.migrate(**self.mapped()).committed)
        context, learning = route_context(), RouteLearning(self.store)
        learning.record_success(context, 110, 20, now=11)
        record = self.store.get(context.fingerprint)
        self.assertEqual((record.motion.samples, record.motion.legacy_samples), (1, 1000))
        self.assertIsNone(learning.lookup(context).action)
        for now in (12, 13):
            learning.record_success(context, 110, 20, now=now)
        self.assertEqual(learning.lookup(context).action, (110, 20))

    def test_v3_suffix_cannot_mix_with_v4_whole_action_even_with_mapping(self):
        self.write(legacy_route(), version=3)
        self.assertTrue(self.migrate(**self.mapped()).committed)
        key = next(iter(self.source_records()))
        context = route_context_from_key(key)
        self.assertEqual((context.family, context.capture_profile.movement_version), ((), "legacy-v3-suffix"))
        self.assertIsNone(RouteLearning(self.store).lookup(route_context()))

    def test_conflicting_legacy_sources_stay_private(self):
        self.write(legacy_route(dx=500), legacy_route("彦宁", dx=-500))
        self.assertTrue(self.migrate(**self.mapped()).committed)
        self.assertIsNone(RouteLearning(self.store).lookup(route_context(config_key="new")))
        self.assertEqual(len(self.source_records()), 2)

    def test_invalid_segments_are_skipped_without_losing_valid_sources(self):
        key, value = legacy_route()
        value["segments"].update(
            {
                "4": {"samples": 1, "mean_dx": 100, "mean_dy": 20},
                "-1": {"samples": 1, "mean_dx": 100, "mean_dy": 20},
                "01": {"samples": 1, "mean_dx": 100, "mean_dy": 20},
                "1": {"samples": True, "mean_dx": 100, "mean_dy": 20},
                "0": {"samples": 1, "mean_dx": float("nan"), "mean_dy": 20},
            }
        )
        self.write((key, value), ("broken", {}))
        result = self.migrate()
        self.assertEqual((result.committed, result.imported, result.skipped), (True, 1, 6))

    def test_malformed_whole_payloads_and_future_destination_schema_are_preserved(self):
        for source in ("broken", "[]", '{"version":99,"routes":{}}', '{"version":4,"routes":[]}'):
            self.path.write_text(source, encoding="utf-8")
            self.assertFalse(self.migrate().committed)
            self.assertFalse(self.store.path.exists())
        self.write(legacy_route())
        future = '{"schema_version":99,"records":{"keep":"future data"}}'
        self.store.path.write_text(future, encoding="utf-8")
        self.assertFalse(self.migrate().committed)
        self.assertEqual(self.store.path.read_text(encoding="utf-8"), future)

    def test_failed_replace_leaves_no_half_import_and_retry_imports_once(self):
        self.write(legacy_route(), legacy_route("彦宁"))
        workflow = '{"kind":"workflow-test"}'
        self.store.put(workflow, LearningRecord())
        before, original = self.store.path.read_bytes(), self.path.read_bytes()
        with patch("src.core.learning.store.os.replace", side_effect=PermissionError("locked")):
            self.assertFalse(self.migrate().committed)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        self.assertEqual(self.migrate().imported, 2)
        self.assertEqual(self.migrate().imported, 0)
        self.assertIn(workflow, self.store.snapshot())

    def test_readback_failure_reports_failure_and_retry_does_not_duplicate(self):
        self.write(legacy_route())
        real_read = Path.read_text

        def read(path, *args, **kwargs):
            if path == self.store.path:
                raise PermissionError("readback unavailable")
            return real_read(path, *args, **kwargs)

        with patch.object(Path, "read_text", read):
            self.assertFalse(self.migrate().committed)
        self.assertEqual(self.migrate().imported, 0)
        self.assertEqual(len(self.source_records()), 1)

    def test_insufficient_destination_capacity_never_marks_complete(self):
        self.write(legacy_route(), legacy_route("彦宁"))
        self.store = LearningStore(self.store.path, max_records=1)
        self.assertFalse(self.migrate().committed)
        self.assertFalse(self.store.path.exists())

    def test_route_capacity_cannot_mark_a_partially_trimmed_import_complete(self):
        self.write(legacy_route(), legacy_route("彦宁"))
        self.assertFalse(self.migrate(max_routes=1).committed)
        self.assertFalse(self.store.path.exists())

    def test_invalid_camera_mapping_cannot_change_resolution_or_create_shared_profile(self):
        self.write(legacy_route())
        result = self.migrate(profile_resolver=lambda *_: CaptureProfile(2560, 1440, "wrong"))
        self.assertTrue(result.committed)
        self.assertEqual((result.imported, result.skipped), (0, 1))

    def test_denied_migration_has_zero_file_or_store_access(self):
        with (
            patch.object(Path, "read_text", side_effect=AssertionError("read")),
            patch.object(self.store, "update_many", side_effect=AssertionError("write")),
            deny_learning("battle"),
        ):
            self.assertFalse(self.migrate().committed)

    def test_batch_validation_failure_is_atomic(self):
        key = route_context().fingerprint
        self.assertTrue(self.store.put(key, LearningRecord()))
        before = self.store.path.read_bytes()
        self.assertFalse(self.store.update_many(lambda records: {**records, "invalid": LearningRecord()}))
        self.assertEqual(self.store.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
