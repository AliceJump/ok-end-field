"""Small, untrusted visual proposals for cold floor acquisition."""

from concurrent.futures import CancelledError
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from src.localization.localization_workers import decode_memory_bytes, ordered_results


@dataclass(frozen=True)
class LayerSearchIndex:
    images: tuple
    pixels_per_meter: float = 0.5

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
