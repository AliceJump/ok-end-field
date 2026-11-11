"""Small, untrusted visual proposals for cold floor acquisition."""

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class LayerSearchIndex:
    images: tuple
    pixels_per_meter: float = 0.5

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
            bounds = entry["bounds"]
            size = (
                max(1, round((bounds["east"] - bounds["west"]) * cls.pixels_per_meter)),
                max(1, round((bounds["north"] - bounds["south"]) * cls.pixels_per_meter)),
            )
            # Resize before channel copies/masking: keeping full-resolution
            # decoded floors would make both cold loading and memory unbounded.
            image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
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
