"""Import legacy zipline estimates without inventing variance or physical identity."""

import json
import math
import os
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path

from src.core.learning.models import (
    CaptureProfile,
    LearningRecord,
    MotionStatistics,
    RouteContext,
    canonical_fingerprint,
)
from src.core.learning.policy import learning_allowed
from src.core.learning.routes import trim_routes


@dataclass(frozen=True)
class RouteMigrationResult:
    committed: bool
    imported: int = 0
    skipped: int = 0


def _legacy_entries(payload, profile_resolver, family_resolver, now):
    version = payload["version"]
    records = {}
    skipped = 0
    for key, route in payload["routes"].items():
        try:
            route_key, size, bucket = json.loads(key)
            config_key, distances = json.loads(route_key)
            width, height = size
            profile = CaptureProfile(width, height, "legacy-unknown", f"zipline-v{version}")
            family = ()
            if version == 4 and profile_resolver is not None:
                resolved = profile_resolver(config_key, width, height)
                if resolved is not None:
                    if not isinstance(resolved, CaptureProfile) or (resolved.width, resolved.height) != (width, height):
                        raise ValueError("Migration cannot change the capture size")
                    profile = resolved
                    if family_resolver is not None:
                        family = family_resolver(config_key)
            # A v3 estimate contains only the suffix after yaw normalization.
            if version == 3:
                profile = replace(profile, movement_version="legacy-v3-suffix")
            segments = route["segments"]
            if not isinstance(segments, dict):
                raise ValueError("Invalid legacy segments")
            # Validate identity even if the legacy route has no usable segments.
            RouteContext(config_key, tuple(distances), 0, profile, bucket, family)
        except (TypeError, ValueError, KeyError, OverflowError, RecursionError):
            skipped += 1
            continue
        for segment, entry in segments.items():
            try:
                index = int(segment)
                if str(index) != segment or not 0 <= index < 64:
                    raise ValueError("Invalid legacy segment")
                context = RouteContext(config_key, tuple(distances), index, profile, bucket, family)
                samples, dx, dy = entry["samples"], entry["mean_dx"], entry["mean_dy"]
                if type(samples) is not int or samples <= 0:
                    raise ValueError("Invalid legacy count")
                if any(type(value) not in (float, int) or not math.isfinite(value) for value in (dx, dy)):
                    raise ValueError("Invalid legacy estimate")
                source = sha256(canonical_fingerprint([version, key, segment]).encode("utf-8")).hexdigest()
                record = LearningRecord(
                    motion=MotionStatistics(samples, dx, dy, variance_known=False),
                    success_count=samples,
                    updated_at=now,
                    provenance=(f"legacy-v{version}", f"legacy-source:{source}", "legacy-mean-may-be-ema"),
                )
                # Alternate whitespace in a source identity must not count twice.
                records.setdefault(context.fingerprint, record)
            except (TypeError, ValueError, KeyError, OverflowError, RecursionError):
                skipped += 1
    return records, skipped


def migrate_zip_line_cache(path, store, *, profile_resolver=None, family_resolver=None, now=0.0, max_routes=128):
    """One atomic snapshot import, followed by disk readback; the source is untouched.

    Without an explicitly verified camera resolver, legacy records stay private in
    an unknown-camera profile. V4 records retain unknown variance and may only
    supply a separately labelled private raw fallback, never converted new data.
    Counts weight legacy *estimates*, not raw data.
    A durable per-source marker and non-overwriting imports make retries idempotent.
    """
    if not learning_allowed():
        return RouteMigrationResult(False)
    if type(max_routes) is not int or max_routes <= 0:
        raise ValueError("Migration route limit must be positive")
    path = Path(os.path.abspath(path))
    if path == store.path:
        return RouteMigrationResult(False)
    try:
        source_text = path.read_text(encoding="utf-8")
        payload = json.loads(source_text)
        if (
            not isinstance(payload, dict)
            or type(payload.get("version")) is not int
            or payload["version"] not in (3, 4)
            or not isinstance(payload.get("routes"), dict)
        ):
            return RouteMigrationResult(False)
        incoming, skipped = _legacy_entries(payload, profile_resolver, family_resolver, now)
        marker = canonical_fingerprint(
            {
                "kind": "route_migration",
                "version": 1,
                "source": os.path.normcase(str(path)),
                "schema": payload["version"],
            }
        )
        imported = 0
        expected = {}

        def update(records):
            nonlocal imported, expected
            if marker in records:
                expected = {marker: records[marker]}
                return records
            for key, record in incoming.items():
                if key not in records:
                    records[key] = record
                    imported += 1
            trimmed = trim_routes(records, max_routes=max_routes)
            if any(key not in trimmed for key in incoming):
                raise ValueError("Route capacity cannot retain the complete import")
            records = trimmed
            records[marker] = LearningRecord(
                updated_at=now,
                provenance=("migration-complete", "sha256:" + sha256(source_text.encode("utf-8")).hexdigest()),
            )
            # Do not mark complete if the store would silently evict part of this import.
            if len(records) > store.max_records:
                raise ValueError("Insufficient capacity for atomic route migration")
            expected = {key: record for key, record in records.items() if key in incoming or key == marker}
            return records

        if not store.update_many(update):
            return RouteMigrationResult(False, skipped=skipped)
        # Fresh instance forces a real readback rather than validating only memory.
        actual = type(store)(store.path, max_records=store.max_records).snapshot()
        if any(actual.get(key) != record for key, record in expected.items()):
            return RouteMigrationResult(False, skipped=skipped)
        return RouteMigrationResult(True, imported, skipped)
    except (OSError, TypeError, ValueError, KeyError, OverflowError, RecursionError):
        return RouteMigrationResult(False)
