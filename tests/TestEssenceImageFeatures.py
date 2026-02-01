# -*- coding: utf-8 -*-
import json
import unittest
from pathlib import Path

import cv2

from ok.feature.FeatureSet import FeatureSet


class TestEssenceImageFeatures(unittest.TestCase):
    def test_essence_features_can_be_found_in_assets_images(self):
        repo_root = Path(__file__).resolve().parents[1]
        coco_path = repo_root / "assets" / "coco_detection.json"
        self.assertTrue(coco_path.exists(), f"Missing {coco_path}")

        coco = json.loads(coco_path.read_text(encoding="utf-8"))
        categories = coco.get("categories", [])
        images = {img["id"]: img for img in coco.get("images", [])}
        annotations = coco.get("annotations", [])

        id_by_name = {c["name"]: c["id"] for c in categories}

        required = {"essence_ui_marker", "essence_locked", "essence_unlocked"}
        for name in required:
            self.assertIn(name, id_by_name, f"Missing category {name} in assets/coco_detection.json")

        gold_names = sorted(n for n in id_by_name if n.startswith("essence_quality_gold"))
        self.assertGreaterEqual(len(gold_names), 1, "Missing any essence_quality_gold* category in assets/coco_detection.json")

        # compress_copy_coco() requires exactly 1 box per category (global unique)
        for name in sorted(required) + gold_names:
            cid = id_by_name[name]
            boxes = [a for a in annotations if a.get("category_id") == cid]
            self.assertEqual(
                len(boxes),
                1,
                f"Category {name} must have exactly 1 annotation box, got {len(boxes)}",
            )

        fs = FeatureSet(str(repo_root / "assets"), str(coco_path), 1, 1)
        threshold = 0.75

        def load_image_for_category(category_name: str):
            cid = id_by_name[category_name]
            ann = next(a for a in annotations if a.get("category_id") == cid)
            image_id = ann["image_id"]
            self.assertIn(image_id, images, f"Missing image_id={image_id} for category {category_name}")
            rel = images[image_id]["file_name"]
            img_path = repo_root / "assets" / rel
            self.assertTrue(img_path.exists(), f"Missing image file {img_path} for category {category_name}")
            mat = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
            self.assertIsNotNone(mat, f"Failed to read {img_path}")
            return mat

        for name in sorted(required) + gold_names:
            mat = load_image_for_category(name)
            h, w = mat.shape[:2]
            matches = fs.find_one_feature(
                mat,
                name,
                threshold=threshold,
                x=0,
                y=0,
                to_x=w,
                to_y=h,
            )
            self.assertIsNotNone(matches, f"find_one_feature returned None for {name}")
            if isinstance(matches, list):
                box = max(
                    matches,
                    key=lambda b: float(getattr(b, "confidence", 0.0)),
                    default=None,
                )
            else:
                box = matches
            self.assertIsNotNone(box, f"find_one_feature returned empty list for {name}")
            self.assertGreater(getattr(box, "width", 0), 0, f"Invalid width for {name}: {box}")
            self.assertGreater(getattr(box, "height", 0), 0, f"Invalid height for {name}: {box}")
            self.assertGreaterEqual(
                float(getattr(box, "confidence", 0.0)),
                threshold,
                f"Low confidence for {name}: {getattr(box, 'confidence', None)}",
            )


if __name__ == "__main__":
    unittest.main()
