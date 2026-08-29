"""验证传送后寻找「登上滑索架」按钮的三阶段逻辑（直接查找 + 踱步 + 前后移动）。"""

import unittest
from types import SimpleNamespace

from src.tasks.onetime.DeliveryTask import DeliveryTask


class _FakeBox:
    """模拟 OCR 命中的按钮 Box。"""


def _make_stub(ocr_results, strafe_results=None, advance_per_strafe=None):
    """构造仅包含 _find_zip_line_board_button 所需接口的桩对象。

    strafe_results: 每次 strafe_search 调用依次返回的值；None 表示该次返回未命中。
    advance_per_strafe: 每次踱步调用后时钟前进的秒数（模拟搜索真实耗时）。
    """
    stub = SimpleNamespace()
    stub._ocr_results = list(ocr_results)
    stub._strafe_results = list(strafe_results or [])
    stub.strafe_calls = []
    stub.ctrl_calls = []
    stub.log_lines = []
    stub.lang = SimpleNamespace(DeliveryTask=SimpleNamespace(k_b0e3a2da="登上滑索架"))
    stub.box = SimpleNamespace(bottom_right="bottom_right")
    stub.ensure_main_calls = []
    stub.ensure_main = lambda *a, **kw: stub.ensure_main_calls.append(kw)
    clock = {"t": 0.0}
    stub.clock = clock
    stub.active_time = lambda: clock["t"]
    stub.next_frame = lambda: "frame"

    def _ocr(**kwargs):
        if stub._ocr_results:
            item = stub._ocr_results.pop(0)
            return [item] if item else []
        return []

    stub.ocr = _ocr

    def _sleep(sec=0):
        clock["t"] += sec if isinstance(sec, (int, float)) else 0  # 桩时钟随等待前进

    stub.sleep = _sleep
    stub.log_info = lambda msg: stub.log_lines.append(msg)
    stub.press_key = lambda key, *a, **kw: stub.ctrl_calls.append(key)

    def _strafe(check, passes=None, duration=0.2, keys=("s", "w", "a", "d"), time_out=-1):
        stub.strafe_calls.append({"keys": keys, "time_out": time_out})
        if advance_per_strafe is not None:
            clock["t"] += advance_per_strafe  # 模拟踱步搜索真实耗时
        if stub._strafe_results:
            return stub._strafe_results.pop(0)
        return None

    stub.strafe_search = _strafe
    return stub


class TestFindZipLineBoardButton(unittest.TestCase):
    def test_direct_hit(self):
        """阶段一直接找到按钮：不进入踱步搜索，也不切换步行。"""
        box = _FakeBox()
        stub = _make_stub(ocr_results=[None, box])
        result = DeliveryTask._find_zip_line_board_button(stub, direct_wait=5.0, total_time_out=60.0)
        self.assertIs(result, box)
        self.assertEqual(len(stub.ensure_main_calls), 1)  # 先确认主界面
        self.assertEqual(stub.strafe_calls, [])  # 未触发踱步
        self.assertEqual(stub.ctrl_calls, [])  # 未切换步行/奔跑

    def test_strafe_hit_after_direct_miss(self):
        """阶段一找不到时进入 WASD 踱步搜索并命中，时限不超过 10 秒。"""
        box = _FakeBox()
        stub = _make_stub(ocr_results=[], strafe_results=[box])
        result = DeliveryTask._find_zip_line_board_button(stub, direct_wait=0.02, total_time_out=30.0)
        self.assertIs(result, box)
        self.assertEqual(len(stub.strafe_calls), 1)  # 仅进入踱步一次
        self.assertLessEqual(stub.strafe_calls[0]["time_out"], 10.0)  # 踱步最多 10 秒
        self.assertEqual(stub.strafe_calls[0]["keys"], ("s", "w", "a", "d"))  # 后退优先
        self.assertEqual(stub.ctrl_calls, ["ctrl", "ctrl"])  # 切步行 + 恢复奔跑
        self.assertTrue(any("踱步" in msg for msg in stub.log_lines))

    def test_ws_fallback_after_strafe_timeout(self):
        """踱步超时后改用仅 W/S 前后移动继续找，命中后恢复奔跑。"""
        box = _FakeBox()
        stub = _make_stub(ocr_results=[], strafe_results=[None, box])
        result = DeliveryTask._find_zip_line_board_button(stub, direct_wait=0.02, total_time_out=60.0)
        self.assertIs(result, box)
        self.assertEqual(len(stub.strafe_calls), 2)  # 踱步 + 前后移动各一次
        self.assertEqual(stub.strafe_calls[1]["keys"], ("s", "w"))  # 阶段三仅前后移动,后退优先
        # 阶段三时限为剩余时间：不超过总超时，且确实用掉了大部分预算
        self.assertLessEqual(stub.strafe_calls[1]["time_out"], 60.0)
        self.assertGreater(stub.strafe_calls[1]["time_out"], 55.0)
        self.assertEqual(stub.ctrl_calls, ["ctrl", "ctrl"])  # 结束恢复奔跑模式

    def test_timeout_returns_none(self):
        """总超时仍未找到：返回 None 并记录超时日志。"""
        stub = _make_stub(ocr_results=[], strafe_results=[None], advance_per_strafe=10.0)  # 踱步消耗掉全部剩余预算
        result = DeliveryTask._find_zip_line_board_button(stub, direct_wait=0.01, total_time_out=5.0)
        self.assertIsNone(result)
        self.assertTrue(any("超时" in msg for msg in stub.log_lines))
        self.assertEqual(len(stub.strafe_calls), 1)  # 总预算耗尽时不进入阶段三
        self.assertLessEqual(stub.strafe_calls[0]["time_out"], 5.0)  # 不超过总超时

    def test_exhausted_before_strafe_returns_immediately(self):
        """阶段一耗尽正数的短总预算：立即返回，不切步行也不进入移动搜索。"""
        stub = _make_stub(ocr_results=[])
        result = DeliveryTask._find_zip_line_board_button(stub, direct_wait=5.0, total_time_out=0.03)
        self.assertIsNone(result)
        self.assertEqual(stub.strafe_calls, [])
        self.assertEqual(stub.ctrl_calls, [])  # 未做任何模式切换
        # 阶段一被总截止时间截断：未跑满 direct_wait
        # （桩时钟按 sleep(0.1) 步进，留一个步长容差）
        self.assertLessEqual(stub.clock["t"], 0.03 + 0.1)


if __name__ == "__main__":
    unittest.main()
