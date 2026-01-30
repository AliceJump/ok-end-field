# Test for TakeDeliveryTask
import unittest
import re

from src.config import config
from ok.test.TaskTestCase import TaskTestCase

from src.tasks.TakeDeliveryTask import TakeDeliveryTask


class TestTakeDelivery(TaskTestCase):
    task_class = TakeDeliveryTask
    config = config

    def test_ocr_reward_in_sample(self):
        """将你的示例截图放在 tests/images/take_delivery_example.png
        然后运行此测试来确认 OCR 是否能在配置的奖励列区域识别到报酬文本。
        可以在运行测试前通过修改 config 中的 reward_regex 或 reward 列坐标来调优。
        """
        self.set_image('tests/images/take_delivery_example.png')
        # 默认任务中使用的区域
        reward_box = self.task.box_of_screen(0.56, 0.18, 0.80, 0.78)
        pattern = re.compile(self.task.config.get('reward_regex', ''), re.I)
        found = self.task.ocr(box=reward_box, match=pattern)
        # 至少识别出一个匹配的报酬文本
        self.assertTrue(len(found) > 0, f"No reward text matched, found: {found}")

    def test_visualize_search_boxes(self):
        """
        【可视化调试专用】
        在 tests/images/take_delivery_example.png 上画出图标搜索区域。
        结果保存在 tests/_debug/icon_search_boxes.png (绿色为搜索框，红色为文字框)
        """
        import cv2
        import os

        # 请确保您有一张游戏截图放在这个位置
        image_path = 'tests/images/take_delivery_example.png'

        if not os.path.exists(image_path):
            print(f"\n[Warning] 图片文件不存在: {image_path}")
            print("请先截图游戏画面(运送委托界面)，保存为该文件名，然后再运行此测试。")
            return

        self.set_image(image_path)

        # 模拟 run() 中的 OCR 过程
        full_texts = self.task.ocr(box=self.task.box_of_screen(0.05, 0.15, 0.95, 0.95))

        reward_regex = r"(\d+\.?\d*)万"
        reward_pattern = re.compile(reward_regex, re.I)

        # 准备画布 (OpenCV 使用 BGR 格式)
        debug_frame = self.task.frame.copy()

        found_any = False

        for t in full_texts:
            name = t.name.strip()
            # 尝试匹配金额
            match = reward_pattern.search(name)
            if match:
                found_any = True
                reward_obj = t

                # --- 这里是复制自 TakeDeliveryTask.py 的逻辑，您可以在这里调整参数看效果 ---
                search_hw_ratio = 3.6
                search_h_ratio = 2.4
                min_box_size = 70

                search_width = max(reward_obj.height * search_hw_ratio, min_box_size)
                search_height = max(reward_obj.height * search_h_ratio, min_box_size)

                x_offset_val = (reward_obj.width / 2) - (search_width / 2)
                y_offset_val = -search_height
                target_real_height = search_height + reward_obj.height * 0.5

                icon_search_box = reward_obj.copy(
                    x_offset = x_offset_val,
                    y_offset = y_offset_val,
                    width_offset = search_width - reward_obj.width,
                    height_offset = target_real_height - reward_obj.height
                )

                # 边界检查
                if icon_search_box.y < 0:
                    icon_search_box.height += icon_search_box.y
                    icon_search_box.y = 0
                if icon_search_box.x < 0:
                    icon_search_box.width += icon_search_box.x
                    icon_search_box.x = 0
                # ---------------------------------------------

                # 画红色文字框 (OCR识别到的钱)
                rx, ry, rw, rh = int(reward_obj.x), int(reward_obj.y), int(reward_obj.width), int(reward_obj.height)
                cv2.rectangle(debug_frame, (rx, ry), (rx + rw, ry + rh), (0, 0, 255), 2)

                # 画绿色搜索框 (计算出的图标搜索区域)
                sx, sy, sw, sh = int(icon_search_box.x), int(icon_search_box.y), int(icon_search_box.width), int(icon_search_box.height)
                cv2.rectangle(debug_frame, (sx, sy), (sx + sw, sy + sh), (0, 255, 0), 2)

        os.makedirs('tests/_debug', exist_ok=True)
        output_path = 'tests/_debug/icon_search_boxes.png'
        cv2.imwrite(output_path, debug_frame)
        print(f"\n[Debug] 处理完成。")
        if found_any:
            print(f"[Debug] 结果已保存至: {os.path.abspath(output_path)}")
            print(f"[Debug] 红色框=识别到的金额文字, 绿色框=计算出的图标搜索区域")
        else:
            print(f"[Debug] 未在图片中识别到任何符合 '{reward_regex}' 的金额文字。请检查图片是否正确，或 OCR 是否正常工作。")

    def test_debug_ocr(self):
        """调试用：逐行运行 OCR 并打印结果，生成可视化截图到 tests/_debug。"""
        import os
        os.makedirs('tests/_debug', exist_ok=True)
        self.set_image('tests/images/take_delivery_example.png')
        rows = int(self.task.config.get('rows', 4))
        left = float(self.task.config.get('reward_col_left', 0.56))
        right = float(self.task.config.get('reward_col_right', 0.80))
        top = 0.18
        bottom = 0.78
        pattern = re.compile(self.task.config.get('reward_regex', ''), re.I)
        all_results = []
        for i in range(rows):
            row_top = top + (bottom - top) * (i / rows)
            row_bottom = top + (bottom - top) * ((i + 1) / rows)
            box = self.task.box_of_screen(left, row_top, right, row_bottom)
            texts = self.task.ocr(box=box, match=pattern)
            print(f"Row {i+1}: ocr -> {texts}")
            # 保存每行截图，便于人工查看
            frame = box.crop_frame(self.task.frame)
            self.task.screenshot(f"_debug/reward_row_{i+1}", frame=frame, show_box=True)
            all_results.append((i+1, texts))
        # 输出汇总到一个文件
        with open('tests/_debug/ocr_results.txt', 'w', encoding='utf-8') as f:
            for r, t in all_results:
                f.write(f"Row {r}: {t}\n")
        print('Debug ocr results saved to tests/_debug')


if __name__ == '__main__':
    unittest.main()
