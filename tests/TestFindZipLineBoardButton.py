# -*- coding: utf-8 -*-
"""验证传送后寻找「登上滑索架」按钮的两阶段逻辑（直接查找 + 踱步搜索）。"""
import time
import unittest
from types import SimpleNamespace

from src.tasks.onetime.DeliveryTask import DeliveryTask


class _FakeBox:
    """模拟 OCR 命中的按钮 Box。"""


def _make_stub(ocr_results, strafe_result="not-called"):
    """构造仅包含 _find_zip_line_board_button 所需接口的桩对象。"""
    stub = SimpleNamespace()
    stub._ocr_results = list(ocr_results)
    stub._strafe_result = strafe_result
    stub.strafe_calls = []
    stub.log_lines = []
    stub.lang = SimpleNamespace(
        DeliveryTask=SimpleNamespace(k_b0e3a2da="登上滑索架"))
    stub.box = SimpleNamespace(bottom_right="bottom_right")
    stub.ensure_main_calls = []
    stub.ensure_main = lambda *a, **kw: stub.ensure_main_calls.append(kw)
    stub.active_time = time.monotonic
    stub.next_frame = lambda: "frame"

    def _ocr(**kwargs):
        if stub._ocr_results:
            item = stub._ocr_results.pop(0)
            return [item] if item else []
        return []

    stub.ocr = _ocr
    stub.sleep = lambda *a, **kw: None
    stub.log_info = lambda msg: stub.log_lines.append(msg)

    def _strafe(check, passes=None, duration=0.2, keys=("w", "a", "s", "d"),
                time_out=-1):
        stub.strafe_calls.append(time_out)
        return stub._strafe_result

    stub.strafe_search = _strafe
    return stub


class TestFindZipLineBoardButton(unittest.TestCase):
    def test_direct_hit(self):
        """阶段一直接找到按钮：不进入踱步搜索。"""
        box = _FakeBox()
        stub = _make_stub(ocr_results=[None, box])
        result = DeliveryTask._find_zip_line_board_button(
            stub, direct_wait=5.0, total_time_out=60.0)
        self.assertIs(result, box)
        self.assertEqual(len(stub.ensure_main_calls), 1)  # 先确认主界面
        self.assertEqual(stub.strafe_calls, [])  # 未触发踱步

    def test_strafe_hit_after_direct_miss(self):
        """阶段一找不到时进入踱步搜索并命中。"""
        box = _FakeBox()
        stub = _make_stub(ocr_results=[], strafe_result=box)
        result = DeliveryTask._find_zip_line_board_button(
            stub, direct_wait=0.02, total_time_out=30.0)
        self.assertIs(result, box)
        self.assertEqual(len(stub.strafe_calls), 1)  # 进入踱步一次
        self.assertGreaterEqual(stub.strafe_calls[0], 1.0)  # 踱步时限为剩余时间
        self.assertTrue(any("踱步" in msg for msg in stub.log_lines))

    def test_timeout_returns_none(self):
        """踱步超时仍未找到：返回 None 并记录超时日志。"""
        stub = _make_stub(ocr_results=[], strafe_result=None)
        result = DeliveryTask._find_zip_line_board_button(
            stub, direct_wait=0.01, total_time_out=0.03)
        self.assertIsNone(result)
        self.assertTrue(any("超时" in msg for msg in stub.log_lines))


if __name__ == "__main__":
    unittest.main()
