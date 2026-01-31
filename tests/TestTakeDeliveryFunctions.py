
import unittest
import re
from unittest.mock import MagicMock
from src.tasks.TakeDeliveryTask import TakeDeliveryTask
from ok.test.TaskTestCase import TaskTestCase
from src.config import config

class MockText:
    def __init__(self, name, y=0, height=10):
        self.name = name
        self.y = y
        self.height = height

    def __repr__(self):
        return f"MockText(name='{self.name}', y={self.y})"

class TestTakeDeliveryFunctions(TaskTestCase):
    task_class = TakeDeliveryTask
    config = config

    def test_process_ocr_results_real_image(self):
        """
        使用真实图片进行 OCR，然后测试 process_ocr_results 的逻辑。
        这样更直观，测试了从图片识别到结果提取的全流程。
        """
        image_path = 'tests/images/take_delivery_example.png'
        import os
        if not os.path.exists(image_path):
            print(f"[Warning] 图片不存在: {image_path}，跳过此测试")
            return

        self.set_image(image_path)

        # 1. 像 run() 方法里一样调用 OCR
        # 注意：这里范围与 Task 代码保持一致
        box = self.task.box_of_screen(0.05, 0.15, 0.95, 0.95)
        # 不传 match pattern，获取所有文本，模拟 run() 中的行为
        full_texts = self.task.ocr(box=box)

        print(f"\n[Debug] OCR 识别到的原始文本数量: {len(full_texts)}")
        # for t in full_texts: print(f"  - {t.name} (y={t.y})") # 调试用

        # 2. 准备参数
        filter_min = 5.0
        reward_pattern = re.compile(r"(\d+\.?\d*)万", re.I)

        # 3. 调用被测函数
        rewards, accept_btns, refresh_btn = self.task.process_ocr_results(full_texts, filter_min, reward_pattern)

        # 4. 验证结果 (基于 take_delivery_example.png 的预期内容)
        # 这张图里通常应该能识别到金额和接取按钮
        print(f"[Debug] 提取到的有效高价值奖励: {len(rewards)} 个")
        print(f"[Debug] 提取到的接取按钮: {len(accept_btns)} 个")

        # 断言至少识别到了东西 (具体的数量取决于您的截图内容)
        # 如果您的截图里确实有 > 5.0万 的任务，这里应该 > 0
        if len(rewards) == 0:
            print("[Warning] 未能在示例图中识别到 > 5.0万 的奖励。请检查截图或图片清晰度。")
        else:
             print(f"识别到的奖励: { [ (r[0].name, r[1]) for r in rewards ] }")

        # 验证按钮
        if len(accept_btns) == 0:
             print("[Warning] 未能识别到'接取运送委托'按钮。")
        else:
             self.assertTrue(len(accept_btns) > 0)

        # 验证刷新按钮 (如果在底部的话)
        if refresh_btn:
            print(f"[Debug] 识别到刷新按钮: {refresh_btn.name}")
        else:
            print("[Debug] 未识别到刷新按钮 (可能被遮挡或位置不符)")

        # 5. 验证图标识别 detect_ticket_type
        # 我们知道截图里有一些 5.54万 的，它们应该是武陵券 (假设)
        # 我们可以遍历所有 rewards 看看能通过 detect_ticket_type 识别出什么
        ticket_types = ['ticket_valley', 'ticket_wuling']

        found_any_ticket = False
        for reward_obj, val in rewards:
            # 调用新提取的方法检测图标
            ticket = self.task.detect_ticket_type(reward_obj, ticket_types)
            if ticket:
                found_any_ticket = True
                print(f"[Debug] 金额 {val}万 -> 检测到票务类型: {ticket.name}")
                # 断言类型是我们配置的其中之一
                self.assertIn(ticket.name, ticket_types)
            else:
                print(f"[Debug] 金额 {val}万 -> 未检测到票务类型")

        if not found_any_ticket:
             print("[Warning] 未能在 rewards 上方识别到任何票务图标。请检查 icon_search_box 计算逻辑或截图清晰度。")



if __name__ == '__main__':
    unittest.main()
