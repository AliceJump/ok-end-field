"""Bounded, versioned and atomic prior storage. It never performs game actions."""

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from threading import RLock

from src.core.learning.models import LearningRecord, canonical_fingerprint
from src.core.learning.policy import learning_allowed

SCHEMA_VERSION = 1
_STORE_LOCK = RLock()


class LearningStore:
    """One process owns the store; independent instances refresh when the file changes."""

    def __init__(self, path=None, *, max_records=4096):
        if type(max_records) is not int or max_records <= 0:
            raise ValueError("Learning store capacity must be positive")
        self.path = Path(path) if path is not None else Path("tmp/context_learning_cache.json")
        self.max_records = max_records
        self._signature = None
        self._records = {}

    def _stamp(self):
        try:
            stat = self.path.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def _load(self):
        signature = self._stamp()
        if signature == self._signature:
            return
        records = {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or type(payload.get("schema_version")) is not int
                or payload["schema_version"] != SCHEMA_VERSION
            ):
                raise ValueError("Unsupported learning schema")
            entries = payload["records"]
            if not isinstance(entries, dict):
                raise ValueError("Invalid learning records")
            for key, entry in entries.items():
                try:
                    self._validate_key(key)
                    records[key] = LearningRecord.from_dict(entry)
                except (TypeError, ValueError, KeyError, OverflowError, RecursionError):
                    continue
        except (OSError, TypeError, ValueError, KeyError, OverflowError, RecursionError):
            pass
        self._records = self._trim(records)
        self._signature = signature

    @staticmethod
    def _validate_key(key):
        if (
            not isinstance(key, str)
            or not isinstance(json.loads(key), dict)
            or canonical_fingerprint(json.loads(key)) != key
        ):
            raise ValueError("Expected a canonical context fingerprint")

    def _trim(self, records):
        keys = sorted(records, key=lambda key: (records[key].updated_at, key), reverse=True)[: self.max_records]
        return {key: records[key] for key in keys}

    def get(self, key):
        if not learning_allowed():
            return None
        with _STORE_LOCK:
            self._load()
            return self._records.get(key)

    def snapshot(self):
        if not learning_allowed():
            return {}
        with _STORE_LOCK:
            self._load()
            return dict(self._records)

    def put(self, key, record):
        """Commit only after a complete atomic write; failed writes keep previous memory/disk."""
        if not learning_allowed():
            return False
        temp_path = None
        with _STORE_LOCK:
            try:
                self._validate_key(key)
                if not isinstance(record, LearningRecord):
                    return False
                self._load()
                records = self._trim({**self._records, key: record})
                text = canonical_fingerprint(
                    {"schema_version": SCHEMA_VERSION, "records": {k: v.to_dict() for k, v in records.items()}}
                )
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self.path.parent,
                    prefix=f".{self.path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as stream:
                    temp_path = Path(stream.name)
                    stream.write(text)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_path, self.path)
                self._records = records
                self._signature = self._stamp()
                return True
            except (OSError, TypeError, ValueError, OverflowError, RecursionError):
                return False
            finally:
                if temp_path is not None:
                    with suppress(OSError):
                        temp_path.unlink(missing_ok=True)

    def update(self, key, transform):
        """Serialize read-modify-write across store instances in this process."""
        if not learning_allowed():
            return False
        with _STORE_LOCK:
            previous = self.get(key) or LearningRecord()
            try:
                record = transform(previous)
            except (TypeError, ValueError, OverflowError):
                return False
            return self.put(key, record)
