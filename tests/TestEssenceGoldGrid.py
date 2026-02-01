# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

from ok.test.TaskTestCase import TaskTestCase

from src.config import config
from src.tasks.EssenceScanTask import EssenceScanTask, EssenceScanSettings


class TestEssenceGoldGrid(TaskTestCase):
    task_class = EssenceScanTask
    config = config

    def test_gold_row_and_first_purple(self):
        repo_root = Path(__file__).resolve().parents[1]
        image_path = repo_root / "tests" / "images" / "essence_gold_row1.png"
        if not image_path.exists():
            self.skipTest(f"Missing test image: {image_path}")

        self.set_image(str(image_path))

        settings = EssenceScanSettings.from_task(self.task)
        grid_x, grid_y = settings.grid_origin
        dx, dy = settings.grid_step
        icon_w, icon_h = settings.icon_size

        # row 1 col 1 should be recognized as gold candidate
        cx = grid_x
        cy = grid_y
        cell_box = self.task._ref_box(
            settings,
            cx - icon_w / 2,
            cy - icon_h / 2,
            cx + icon_w / 2,
            cy + icon_h / 2,
            name="essence_cell_test",
        )
        self.assertTrue(self.task._is_gold_cell(cell_box), "Expected row 1 col 1 to be gold")

        # panel OCR should confirm current selection is gold (无瑕基质)
        info = self.task.read_essence_info()
        self.assertIsNotNone(info, "Expected OCR to return essence info for 1.png")
        assert info is not None
        self.assertTrue(info.is_gold, f"Expected gold essence in panel, got {info.name}")


if __name__ == "__main__":
    unittest.main()
