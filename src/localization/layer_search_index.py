"""Small, untrusted visual proposals for cold floor acquisition."""

import hashlib
import json
import os
import tempfile
from concurrent.futures import CancelledError
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile

import cv2
import numpy as np

from src.localization.localization_workers import decode_memory_bytes, ordered_results
from src.localization.tiled_map_image import TiledMapImage, floor_options


@dataclass(frozen=True)
class LayerSearchIndex:
    images: tuple
    pixels_per_meter: float = 0.5

    @classmethod
    def load_or_build(
        cls,
        root,
        entries,
        *,
        color,
        cache_dir=Path("cache/localization/layer-index"),
        resources=None,
        stop=None,
        bundle=None,
    ):
        """Reuse derived pixels, never previous-frame scores or positions."""
        root = Path(root).resolve()
        entries = tuple(entries)
        if bundle is not None:
            return cls.build(root, entries, color=color, resources=resources, stop=stop, bundle=bundle)
        identity = json.dumps([str(root), [entry["id"] for entry in entries], color])
        cache_path = Path(cache_dir) / (hashlib.sha256(identity.encode()).hexdigest() + ".npz")
        signature = cls._signature(root, entries, color)
        try:
            with np.load(cache_path, allow_pickle=False) as cached:
                if cached["signature"].item() == signature:
                    images = []
                    for i, entry in enumerate(entries):
                        pixels = cached[f"image_{i}"]
                        expected = cls._size(entry)
                        shape = (expected[1], expected[0], 3) if color else (expected[1], expected[0])
                        if pixels.shape != shape or pixels.dtype != np.uint8:
                            raise ValueError("invalid floor index pixels")
                        pixels.setflags(write=False)
                        images.append((entry["id"], pixels))
                    return cls(tuple(images))
        except (OSError, ValueError, TypeError, KeyError, EOFError, BadZipFile):
            pass
        index = cls.build(root, entries, color=color, resources=resources, stop=stop)
        if stop is not None and stop.is_set():
            raise CancelledError()
        # Do not publish a partial index or one built while resources changed.
        if len(index.images) != len(entries) or signature != cls._signature(root, entries, color):
            return index
        temporary = None
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=cache_path.parent, suffix=".npz", delete=False) as handle:
                temporary = Path(handle.name)
                np.savez(
                    handle, signature=signature, **{f"image_{i}": image for i, (_, image) in enumerate(index.images)}
                )
            os.replace(temporary, cache_path)
        except OSError:
            pass  # An unwritable cache must not disable localization.
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
        return index

    @classmethod
    def _signature(cls, root, entries, color):
        root = Path(root).resolve()
        sources = []
        for entry in entries:
            path = (root / entry["path"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("layer image escapes resource root")
            try:
                stat = path.stat()
                stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            except OSError:
                stamp = None
            sources.append((entry, stamp))
        encoded = json.dumps([1, cls.pixels_per_meter, color, sources], sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _size(cls, entry):
        bounds = entry["bounds"]
        return (
            max(1, round((bounds["east"] - bounds["west"]) * cls.pixels_per_meter)),
            max(1, round((bounds["north"] - bounds["south"]) * cls.pixels_per_meter)),
        )

    @classmethod
    def build(cls, root, entries, *, color, resources=None, stop=None, bundle=None):
        root = Path(root).resolve()
        images = []
        entries = tuple(entries)
        decode = cls._decode_entry if bundle is None else cls._read_bundle_proposal
        options = {"bundle": bundle} if bundle is not None else {}
        # Bound submitted futures as well as workers. Only small thumbnails
        # survive each task; full-resolution arrays die inside the decoder.
        for offset in range(0, len(entries), 4):
            if stop is not None and stop.is_set():
                raise CancelledError()
            batch = entries[offset : offset + 4]
            if resources is None:
                results = [decode(root, entry, color, **options) for entry in batch]
            else:
                futures = []
                try:
                    for entry in batch:
                        width, height = cls._size(entry)
                        futures.append(
                            resources.submit(
                                decode,
                                root,
                                entry,
                                color,
                                memory_bytes=(
                                    decode_memory_bytes(width, height)
                                    if bundle is not None
                                    else decode_memory_bytes(entry["width"], entry["height"], width * height)
                                ),
                                **options,
                            )
                        )
                    results = ordered_results(futures, stop)
                finally:
                    for future in futures:
                        future.cancel()
            images.extend(result for result in results if result is not None)
        return cls(tuple(images))

    @staticmethod
    def _read_bundle_proposal(root, entry, color, *, bundle):
        # A damaged rank image only disables this proposal. The full search
        # still checks the corresponding floor before proving uniqueness.
        try:
            tiled = bundle.open_floor(root, entry)
            try:
                image = tiled.proposal()
            finally:
                tiled.close()
        except (OSError, ValueError):
            return None
        if not color:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            image.setflags(write=False)
        return entry["id"], image

    @classmethod
    def _decode_entry(cls, root, entry, color):
        path = (root / entry["path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("layer image escapes resource root")
        tiled = TiledMapImage.open_prepared(path, (entry["width"], entry["height"]), **floor_options(entry))
        if tiled is not None:
            image = tiled.proposal()
            if not color:
                image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                image.setflags(write=False)
            return entry["id"], image
        image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if image is None or image.shape[:2] != (entry["height"], entry["width"]):
            # The full scan retains retry and fail-closed overlap checks.
            return None
        image = cv2.resize(image, cls._size(entry), interpolation=cv2.INTER_AREA)
        if image.ndim == 3 and image.shape[2] == 4:
            alpha = image[:, :, 3]
            image = image[:, :, :3].copy()
            image[alpha == 0] = 0
        if color and image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        elif not color and image.ndim == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        image.setflags(write=False)
        return entry["id"], image

    def rank(self, template, map_scale, *, executor=None, stop=None, with_positions=False):
        size = max(4, round(template.flat.shape[0] * self.pixels_per_meter / (4 * map_scale)))
        thumbnail = cv2.resize(template.flat, (size, size), interpolation=cv2.INTER_AREA)
        thumbnail.setflags(write=False)
        options = {"with_positions": True} if with_positions else {}
        if executor is None:
            return sorted(self._rank_images(self.images, thumbnail, **options), reverse=True)
        futures = []
        try:
            for i in range(min(4, len(self.images))):
                futures.append(executor.submit(self._rank_images, self.images[i::4], thumbnail, **options))
            return sorted((proposal for batch in ordered_results(futures, stop) for proposal in batch), reverse=True)
        finally:
            for future in futures:
                future.cancel()

    @staticmethod
    def _rank_images(images, thumbnail, *, with_positions=False):
        size = thumbnail.shape[0]
        proposals = []
        for tier_id, image in images:
            if min(image.shape[:2]) < size:
                continue
            scores = cv2.matchTemplate(image, thumbnail, cv2.TM_CCOEFF_NORMED)
            scores = np.where(np.isfinite(scores), scores, -1)
            if with_positions:
                _, score, _, (left, top) = cv2.minMaxLoc(scores)
                proposals.append((float(score), tier_id, (left + (size - 1) / 2, top + (size - 1) / 2)))
            else:
                proposals.append((float(scores.max()), tier_id))
        return proposals
