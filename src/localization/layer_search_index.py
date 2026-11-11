"""Small, untrusted visual proposals for cold floor acquisition."""

import hashlib
import json
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile

import cv2
import numpy as np


@dataclass(frozen=True)
class LayerSearchIndex:
    images: tuple
    pixels_per_meter: float = 0.5

    @classmethod
    def load_or_build(cls, root, entries, *, color, cache_dir=Path("cache/localization/layer-index")):
        """Reuse derived pixels, never previous-frame scores or positions."""
        root = Path(root).resolve()
        entries = tuple(entries)
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
        index = cls.build(root, entries, color=color)
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
    def build(cls, root, entries, *, color):
        images = []
        for entry in entries:
            path = (root / entry["path"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("layer image escapes resource root")
            image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            if image is None or image.shape[:2] != (entry["height"], entry["width"]):
                # This index is only an accelerator. The ordinary full scan
                # retains resource retry and fail-closed overlap checks.
                continue
            # Resize before channel copies/masking: keeping full-resolution
            # decoded floors would make both cold loading and memory unbounded.
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
            images.append((entry["id"], image))
        return cls(tuple(images))

    def rank(self, template, map_scale):
        size = max(4, round(template.flat.shape[0] * self.pixels_per_meter / (4 * map_scale)))
        thumbnail = cv2.resize(template.flat, (size, size), interpolation=cv2.INTER_AREA)
        proposals = []
        for tier_id, image in self.images:
            if min(image.shape[:2]) < size:
                continue
            scores = cv2.matchTemplate(image, thumbnail, cv2.TM_CCOEFF_NORMED)
            scores = np.where(np.isfinite(scores), scores, -1)
            proposals.append((float(scores.max()), tier_id))
        return sorted(proposals, reverse=True)
