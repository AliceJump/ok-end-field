"""自动送货坐标状态机与取货回退逻辑测试。"""

import re
import unittest

from src.data.delivery_area_service import (
    get_delivery_location_coordinate,
    get_delivery_target_coordinate,
)
from src.tasks.onetime.DeliveryTask import (
    DeliveryNavigationMode,
    DeliveryTask,
)


class TestDeliveryStateMachine(unittest.TestCase):
    """验证网格导航状态机的顺序、坐标和旧流程回退。"""

    def _task(self, location="武陵城", mode="网格导航"):
        task = object.__new__(DeliveryTask)
        task.delivery_area = "武陵"
        task._accepted_delivery_location = location
        task.config = {task.CFG_ARRIVAL_MODE: mode}
        task.logs = []
        task.log_info = lambda message: task.logs.append(("info", message))
        task.log_warning = lambda message: task.logs.append(("warning", message))
        return task

    def test_arrival_mode_defaults_to_legacy(self):
        task = self._task(mode="")

        self.assertEqual(task._arrival_mode(), DeliveryNavigationMode.LEGACY)

    def test_grid_state_machine_navigates_pickup_then_destination(self):
        task = self._task()
        calls = []
        target_pattern = re.compile("资源")
        task._navigate_delivery_coordinate = lambda coordinate, label: calls.append((label, coordinate)) or True
        task._pickup_receive_good_with_fallback = lambda: calls.append(("pickup", None)) or True
        task._recognize_delivery_end = lambda patterns: ("资源", target_pattern)
        task._submit_at_destination = lambda pattern: calls.append(("submit", pattern)) or True

        self.assertTrue(task._run_grid_delivery_state_machine({target_pattern: "资源"}))
        self.assertEqual(
            calls,
            [
                ("取货点", get_delivery_location_coordinate("武陵", "武陵城")),
                ("pickup", None),
                ("送货点", get_delivery_target_coordinate("武陵", "资源", "武陵城")),
                ("submit", target_pattern),
            ],
        )

    def test_pickup_prefers_direct_receive_good_template(self):
        task = self._task()
        calls = []
        task.find_feature = lambda **_kwargs: ["pickup-box"]
        task.click_with_alt = lambda result, **kwargs: calls.append((result, kwargs))
        task._legacy_pickup_search = lambda: calls.append("legacy") or True

        self.assertTrue(task._pickup_receive_good_with_fallback())
        self.assertEqual(calls, [("pickup-box", {"after_sleep": 2})])

    def test_pickup_falls_back_when_direct_template_missing(self):
        task = self._task()
        task.find_feature = lambda **_kwargs: []
        task._legacy_pickup_search = lambda: True

        self.assertTrue(task._pickup_receive_good_with_fallback())

    def test_missing_pickup_coordinate_falls_back_to_legacy_leg(self):
        task = self._task(location="不存在的地点")
        task._run_legacy_delivery_leg = lambda patterns: True

        self.assertTrue(task._run_grid_delivery_state_machine({}))

    def test_missing_destination_coordinate_falls_back_to_legacy_submit(self):
        task = self._task()
        target_pattern = re.compile("常沄")
        calls = []
        task._navigate_delivery_coordinate = lambda coordinate, label: calls.append((label, coordinate)) or True
        task._pickup_receive_good_with_fallback = lambda: True
        task._recognize_delivery_end = lambda patterns: ("常沄", target_pattern)
        task.zip_line_scroll_enabled = lambda: False
        task.on_zip_line_start = lambda end, **kwargs: calls.append(("ride", end, kwargs))
        task.to_end_and_submit = lambda pattern: calls.append(("submit", pattern)) or True

        self.assertTrue(task._run_grid_delivery_state_machine({target_pattern: "常沄"}))
        self.assertEqual(calls[-2][0], "ride")
        self.assertEqual(calls[-1], ("submit", target_pattern))


if __name__ == "__main__":
    unittest.main()
