"""Camera preparation must release held inputs and never take or submit a photo."""

import base64
import re
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import cv2
import numpy as np
from ok import Box, TaskDisabledException
from ok.util.config import Config
from qfluentwidgets import FluentIcon

from src.config import config as app_config
from src.data.FeatureList import FeatureList as fL
from src.tasks.onetime.DailyTask import DailyTask
from src.tasks.onetime.EnvironmentMonitoringPhotoTask import EnvironmentMonitoringPhotoTask

# 实测工业图 0c49e31645 与探索图 b29d3895d7 的左右模式徽章小裁剪。
MODE_MARKER_PNG = {
    "industrial_left": (
        "iVBORw0KGgoAAAANSUhEUgAAABYAAAAWCAIAAABL1vtsAAAD0ElEQVQ4EYXBb0jUdxwH8Pfn9ztP85Sz+YfcSbZTEL1OUnyitN2QbrtZmuLfHsxk"
        "tQeh4ZP1SMlGQ2IPkmkhi6SERpq1mGHqNSNhgQcKPtDzT8u/TzyxHbLfpd6v7+8z+cGNnzDY60Uej+fHGzecTid0v4+Nzc7OapoGQCJy5uY6cnJS"
        "UlKIiJlxkKIo6+vr1NXZ2dTYCICZ/X6/98UL6Eyy7Ha7s7KyiAi6+fn59zs7RAQdMztycqKjoyn47l1CQgKAzc3N/v7+D0IAiI6O/tzlys7OBkBE"
        "AAKBwDcXLqiqiojMzMzbt27RPhYCgKZpYy9fzszMQJefn//pyZOSJEHHzFfb2nw+H/5FdLurKzMzk/axEAAURXn85EkwGASQmJh4rq7OZDIREXSL"
        "i4tNly/DoLCw8Ptr16AjFgLA0tLS4LNnzAzgzOnTGRkZpAPAzF+fP78ZCCDCbDb39/VZLBboiIVg5lfj49PT0wCsVmtVZWV8fDwRAWBmn893ta0N"
        "BtXV1d9evIgIYiGY+WFfXyAQAHAsPb2kpMRsNhMRAGb+0uOBgSzLw8+fw4BYCGb++c6dnZ0dANnZ2V+43aQD8FNn59DQEAw6bt50OBwwIBaCmTu7"
        "ujRNA5DrdBYXFwMgIgAtra3MjAg1HK6prYVBfFwcsRDM3N3dvRcOA3A4HO5TpwAQEQ5i5paWlsmpKRj03L1LLASA3t7ev4JBABl2u8fjiYqKIiIc"
        "tLy8fKmxURMCEQUFBT9cv04sBIBRr9fv9wNISkysqKiwWCxEBANmvtTYuPT2LQx+efAgOTmZWAgA8/PzwyMj0NXW1KSmphIRDF6Nj7e3t8PgXF1d"
        "Q0MD7WMhAGxvbw8MDCihEDMfS08vKyuTZRkRqqpW19a+D4UQYbFYHg8MyLIMgFgIAKqqjnq9f755AyIwf+Zy5Z04QUTQPezru3f/PpgR0dTUVFZa"
        "Ch0JVZUkCcDKyspvg4Osi4uL+8rjSUtLY2ZFUSqrqmDwsc12r6eHiKCjgUePqiorAWia9sfr11NTU9B9dPhwcXGxzWYbHR2dmJiAQX19vd1uJyLo"
        "qKK8vKOjI/3oUWYG8OvTp2tra9ARkcvlch4/Lssy/gszh0IhKigoKD97trm52RIbCyAYDI56vRsbG9ARUZrN9ondnnrkiNVqjYmJkSRpb29PUZSt"
        "ra219fWFhQXKy8uLPXToypXvSs+USpLEzLu7u8MjI6urq4iQiKLMZpPJJEkSEWlCCE1Tw+EPQgCgvPx8MCclJbW2tBQVFZlkmZnD4fDk5OTc3Nzf"
        "ioL/8w9C+L8rhS1siQAAAABJRU5ErkJggg=="
    ),
    "industrial_right": (
        "iVBORw0KGgoAAAANSUhEUgAAABYAAAAWCAIAAABL1vtsAAADWklEQVQ4EY3BTUyTdxwH8O/vadNAgYSkvIrhMGmISwiWWkuoDDjYUji4gzsuoktc"
        "fDtAnLAGDiAvGeWtdTOLu8I8qFMS6CREhyAwApQXLwI7cCCxARM3WAttn//zW/YkTdrgcJ8PRcNhrVYLQFGUR48fPx0eXl9fF0IQEeIQUWZGhuXM"
        "ma+vXDmelwfV20DA5XIRCwEgHIm0tbX9+uwZM+NI+fn5fb09Jz45AdXvc3PEQuzu7bnd7lGfDwAz42OO5+W137lTXFwMQCgKsRCDQ0Mer1eWZRyJ"
        "iJKTk0OhEACj0egdGMjJyQFAf6yvf3nx4sHBATPjQ4gIQN6xY48ePoxEIp9VVgKQJOna1atfXb4MgL65dev5ixeswiFarbavp6e8vByqazduzM7O"
        "QpWbm+sbGQFANU7n20CAmZHIdOrUT/fvazQaxDGZzYghos6OjmqHg8rPnv07GGRmxBkaHPz05EkkGvX5mltaEENEVVVVvW43WSwWWZaZGTFE5F9Y"
        "wCEOp3N7exsxRGQwGMbHxqikpIRViLHZbN97vUjEgPn0aWZGjCRJOp1udnqazGazoijMjJj5uTmtVosYZiai6zdvzszMII4kSenp6c/Hx8lms4VC"
        "IWaGKiMjY3xsDImY2WK1CiEQR5Iki8Xy4717ZD93bufdO2YGQEQd7e3O6mrEWVhc7Ha7NzY2kEiSpJbm5s/Pn6dLdXXLKyvMDICIFufniQiAoiid"
        "XV2/PHkCgJlxiF6vn56aAkDTr17VNzREo1FmLisr++Hu3YmXL7/r7g4EAvhvkiR9ceFCU2MjABLRaF9//88PHjBzRUXF5OQkM+NIRJSdne31eIwF"
        "BQCIhdjZ2fnW5Vr0+/H/pKWlNd6+XVtTA0BRFGIhAGxubnZ0dfn9fmbGkTIzMxvq6x12O1Rv1tZIEYLwLyFET2/v0+HhSDjM+ACNRmMwGDz9/YWF"
        "hVBFotGmpiZaXV4uKiqCSgixtLQ0OTW1+vr11tbW3t6eLMupqalZWVnGggKr1eqw25OSkqB6/+d7j8c7MjpKlRUVXZ2dpaWliFEUJRgMhsNhWZaZ"
        "WaPR6HQ6vV6v0+kQ89fubmtr628TEwDIZDKlpKRcqqurra3NzsrCx+zv76+srg54PGtra1D9A3eikK45OMchAAAAAElFTkSuQmCC"
    ),
    "explore_left": (
        "iVBORw0KGgoAAAANSUhEUgAAABYAAAAWCAIAAABL1vtsAAADv0lEQVQ4EYXBb0yUBRwH8O/vYWfHsWu8AGlwY6BOptn6PZIhMN+x2IT4EzlbJ7EJ"
        "nms5FwJtAqLQZqxJmQeLP/NEpmCIzFggltzYsHiBLPP19aJbDp4XcC/Y4XN/eH7ZUzcf14s+H2JmAEQEoK6uruH48ZSUFJgWHj4cGRn57cmTWCxG"
        "RHiZ0+nM37//REMDMTMAIiooKPi2rw+m+NaWz+frHxgQEZhcWVlZLhdE8A+iQCCwtrZmt9uJmQGkp6f3XLr0xr59MF31+a76fLquiwiApKQk/9zc"
        "q04nEvRIpKi4WEQAEDMDKC8r67xwQVEUAIuLi41NTZFIBCYiOuZ2n2lshEVnV9f3U1MiAoCYGcDQ4OBb+fkANE1z19aGQiHDMGBSFOXR0hLhBT0S"
        "KSwqQgIxs8vlmpyYsNlsAEbHxr6+fNkwwTTQ3//2gQOwqKyuDgaDSCBVVT84evSzlhYAuq63trfPz88DEBEiysrKmrp7l4iQ8Gh5+YTHAwtSVfXL"
        "7u6SkhIA66FQfX39H8EgABEhorHR0bzdu5EgkMOHy1Y1DRakqurYzZt5eXkANE2rOXJkc3MTgIiUlpZ2X7wIi2vDw1e8XryMVFX9YWoqMzMTwMrK"
        "SnlFhYgAEJGOc+cytm8X/CspKWnizh392TMk6JHI8vIyqap6e3x8544dAFY1raq6OhqNAhARWBBRTU1N29mzsLji9V4bHiZVVft6ewsPHgSwtrb2"
        "UV3dyuoqABGBhdPp/PH+rP0VOxI0TSuvqIjH48TMHo/n45MnAYTD4TPNzUtLSwBEBBZtra3v19TA4pNTp35ZXARAzLxnz57RGzdgGhgcHBwaEhMS"
        "HA7HzwsLsPjz6dOKykoRAUDMDGD8u1u7du4CEAgEGjyejY0NEYGJiO7fu5eeng6LgsLCaDQKEzEzgNra2k9Pn8ZzRJOTk190dxuGAdPre/eOXL+u"
        "KAoS5vz+5pYWJBAzA8jOzv6qpyc3J0dRFADnOztnZma2traIaN7vdzqdSIjH48WHDkWjUSQQMwNQFKWqqqq9rY3wt/X19W+83unpaWY+5nbDYnZ2"
        "9qcHD0QEJpvNRswMgIgAnO/oeLe8XFEUmG5PTHh7e8PhsIjgP4goNTX1864uYmYARATA4XC0tbaWlr6jkALAEPk9EJjz+399/DgYDIZCoVgslpyc"
        "nJaWlpuTk5+fX1JS8lpGBjEzLFJSUpqbmqoqK2ERDod1XY/F42IYiqLYtm1Lfs5uh4neZCa8QEQA3quudrs/zM3Jxf8xDOMv9FuWkDPKyOYAAAAA"
        "SUVORK5CYII="
    ),
    "explore_right": (
        "iVBORw0KGgoAAAANSUhEUgAAABYAAAAWCAIAAABL1vtsAAADMklEQVQ4EY3Bz0ubBxwH4M/nfZMY5y86jFQbBJuDaH/Y6UCQdBmiNTrwOvcHjEKd"
        "O60drpf1pD28cTB7mGWlO9RdOle11Kwt2vWwKrGHFSVGvLjQKRit0TdqzPu+340XAgl2ds/D74aGLMsCoJDnzp8/U19fUVFBUkSQT9f1eDw+OzeX"
        "TCZhKy0t7QwGGdI0AA5VbW9vr62tJYljbW9vT0xObm5uwlZdXc2QphUUFHwcCNTV1QEgiXdJJpPhcPjvtTUA/FdI0xobGy/6/Yqi4FgicnBwUFhY"
        "CCCRSDwYH9/d3QXAn+7e/aynx+FwkMTbiAiAtbW1zy9fdjmdv46NAbAs648XLyKRCAAuLy35fD7acIRhGN/euDE3Nwfb4OBgU2MjbDs7Oz/euQOA"
        "yTdvSkpKSCLfwsLCV1evmqaJHE8eP0aOqamppViMB3t7LpeLJHL09vUtx2LI19bW9vW1a8ixsrIy+fAhzUyGNmSJyKWODhzx8+hoeXk5cqRSqZHb"
        "t2kZBgCSyIpEIt9cv458JH8Lh0kiS0RM0/x+eJiWYQAgiazOri7DMJBFUkQGBgY+bGpCDhHZ39//YWSE6f19p9NJEratra1Pe3qQj+TUo0eqqiKH"
        "iMTj8V/GxribTBYVFZEEICKDN29OT08jR0NDQ++VKzU1NcgnIk+ePl1cXOTreLyyspIkABHpCAZFBICiql/29XV1dgIgiSMymczwrVsAOHb/fnd3"
        "t6qqAOZfvuzv729pafmit9fj8eC/icifr17NzMwA4JCmfRQIfHDhAsnZ2dnm5maSOJaI6Lr+YHw8kUgAYEjTiouLO4NBr9eL/yedTs88exaNRgEQ"
        "YEjTALx/4kRra6vX6yWJY+m6/vvz58vLy7B5PB6GNA02koFA4NzZs6qq4m1EJJVKjU9MbGxswKaq6iddXRy9d299fR02kt5Tp2pOn648ebKsrMzt"
        "diuKkk6ndV1PJBJ/xeOxWMwwDNjeKyz0+/319fXc0/WpcHh1dRVZCul0uRwOh6IoJC3TNC0rc3homCay3G73pfZ2n88HgJZhHB4ezs/PR6PRXV3H"
        "uzidzqqqqot+v8fjge0fuc9mDqWBw88AAAAASUVORK5CYII="
    ),
}


class TestEnvironmentMonitoringPhotoFlow(unittest.TestCase):
    def test_daily_group_keeps_preview_out_of_daily_completion_plan(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Config, "config_folder", directory):
            task = EnvironmentMonitoringPhotoTask(SimpleNamespace(scene=None, pause_start=None), SimpleNamespace())
        self.assertEqual(task.group_name, "日常任务")
        self.assertEqual(task.group_icon, FluentIcon.CALENDAR)
        self.assertEqual(task.default_config, {})
        registration = ["src.tasks.onetime.EnvironmentMonitoringPhotoTask", "EnvironmentMonitoringPhotoTask"]
        self.assertEqual(app_config["onetime_tasks"].count(registration), 1)
        self.assertNotIn("环境监测", DailyTask.OTHER_TASKS)

    def task(self):
        task = Mock()
        task.lang = SimpleNamespace(
            environment_monitoring=SimpleNamespace(
                tool_wheel=re.compile("选择工具|松开确认"),
                camera_tool=re.compile("^拍照$"),
                pool_lotus_photo=re.compile("拍摄.*小池荷"),
            ),
            DeliveryTask=SimpleNamespace(k_96b876e3="工业"),
        )
        task.box = SimpleNamespace(right=Box(960, 0, 960, 1080))
        task.box_of_screen.side_effect = lambda x1, y1, x2, y2: Box(
            round(task.frame.shape[1] * x1),
            round(task.frame.shape[0] * y1),
            round(task.frame.shape[1] * (x2 - x1)),
            round(task.frame.shape[0] * (y2 - y1)),
        )
        task.ocr.return_value = []
        task.wait_ocr.return_value = [Box(1500, 800, 150, 40)]
        task._resolve_config_key.side_effect = lambda key, category: {"r": "f6", "w": "up", "d": "right"}.get(key, key)
        task.frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.next_frame.side_effect = lambda: task.frame
        task._camera_open.return_value = False
        task._world_mode.return_value = "explore"
        task.screenshot = Mock()
        task.normalize_pos.side_effect = lambda point: (round(1920 * point[0]), round(1080 * point[1]))
        return task

    def test_movement_cancellation_or_exception_always_releases_resolved_key(self):
        for error in (TaskDisabledException(), RuntimeError("input interrupted")):
            with self.subTest(error=type(error).__name__):
                task = self.task()
                task.sleep.side_effect = error
                with self.assertRaises(type(error)):
                    EnvironmentMonitoringPhotoTask._hold_route_key(task, "w", 0.5)
                task.send_key_down.assert_called_once_with("up")
                task.send_key_up.assert_called_once_with("up")

    def test_failed_focus_prevents_movement_key_down(self):
        task = self.task()
        task._require_input_focus.side_effect = RuntimeError("no focus")
        with self.assertRaisesRegex(RuntimeError, "no focus"):
            EnvironmentMonitoringPhotoTask._hold_route_key(task, "d", 3)
        task.send_key_down.assert_not_called()
        task.sleep.assert_not_called()

    def test_missing_wheel_releases_tool_without_cursor_move_or_short_press(self):
        task = self.task()
        task.wait_ocr.return_value = []
        diagnostics = Mock()
        diagnostics.attach_mock(task.screenshot, "capture")
        diagnostics.attach_mock(task.send_key_up, "release")
        with self.assertRaisesRegex(RuntimeError, "轮盘未打开"):
            EnvironmentMonitoringPhotoTask.open_camera(task)
        task.send_key_down.assert_called_once_with("f6")
        task.send_key_up.assert_called_once_with("f6")
        task.smooth_drag.assert_not_called()
        task.press_key.assert_not_called()
        task.log_info.assert_called_once()
        self.assertEqual(diagnostics.mock_calls[0].args, ("environment-monitoring-tool-wheel-missing",))
        self.assertIs(diagnostics.mock_calls[0].kwargs["frame"], task.frame)
        self.assertEqual(diagnostics.mock_calls[1], call.release("f6"))

    def test_failed_diagnostics_preserve_wheel_error_and_release_tool_key(self):
        for diagnostic in ("log_info", "screenshot"):
            with self.subTest(diagnostic=diagnostic):
                task = self.task()
                task.wait_ocr.return_value = []
                getattr(task, diagnostic).side_effect = RuntimeError("diagnostic failed")
                with self.assertRaisesRegex(RuntimeError, "快捷工具轮盘未打开"):
                    EnvironmentMonitoringPhotoTask.open_camera(task)
                task.send_key_up.assert_called_once_with("f6")
                task.smooth_drag.assert_not_called()
                task.press_key.assert_not_called()
                task.ocr.assert_not_called()

    def test_cancellation_during_diagnostics_is_propagated_and_releases_tool_key(self):
        task = self.task()
        task.wait_ocr.return_value = []
        task.screenshot.side_effect = TaskDisabledException()
        with self.assertRaises(TaskDisabledException):
            EnvironmentMonitoringPhotoTask.open_camera(task)
        task.send_key_up.assert_called_once_with("f6")
        task.press_key.assert_not_called()

    def test_hover_without_camera_label_releases_tool_without_activating_it(self):
        task = self.task()
        task.wait_ocr.side_effect = [[Box(700, 500, 100, 30)], []]
        with self.assertRaisesRegex(RuntimeError, "未选中相机"):
            EnvironmentMonitoringPhotoTask.open_camera(task)
        task.smooth_drag.assert_called_once()
        task.send_key_up.assert_called_once_with("f6")
        task.press_key.assert_not_called()

    def test_wheel_gate_is_scoped_to_center_and_camera_identity_is_checked_after_hover(self):
        task = self.task()
        EnvironmentMonitoringPhotoTask.open_camera(task)
        wheel_gate, identity_gate = task.wait_ocr.call_args_list
        self.assertEqual(wheel_gate.kwargs["match"], task.lang.environment_monitoring.tool_wheel)
        self.assertRegex("松开确认", wheel_gate.kwargs["match"])
        self.assertEqual(identity_gate.kwargs["match"], task.lang.environment_monitoring.camera_tool)
        for detection in (wheel_gate, identity_gate):
            area = detection.kwargs["box"]
            self.assertEqual((area.x, area.y, area.width, area.height), (768, 464, 384, 140))

    def test_tool_selection_cancellation_and_drag_failure_release_tool(self):
        for failing_method, error in (
            ("wait_ocr", TaskDisabledException()),
            ("smooth_drag", RuntimeError("drag interrupted")),
        ):
            with self.subTest(failing_method=failing_method):
                task = self.task()
                getattr(task, failing_method).side_effect = error
                with self.assertRaises(type(error)):
                    EnvironmentMonitoringPhotoTask.open_camera(task)
                task.send_key_down.assert_called_once_with("f6")
                task.send_key_up.assert_called_once_with("f6")
                task.press_key.assert_not_called()

    def test_selecting_camera_waits_after_tool_release_before_short_press(self):
        task = self.task()
        task.wait_until.side_effect = [False, True]
        events = Mock()
        events.attach_mock(task.send_key_up, "release")
        events.attach_mock(task.wait_until, "observe")
        events.attach_mock(task.press_key, "press")
        EnvironmentMonitoringPhotoTask.open_camera(task)
        tail = events.mock_calls
        self.assertEqual(tail[0], call.release("f6"))
        self.assertIs(tail[1].args[0], task._camera_open)
        self.assertGreater(tail[1].kwargs["time_out"], 0)
        self.assertEqual(tail[2], call.press("r", down_time=0.15))
        self.assertIs(tail[3].args[0], task._camera_open)
        task.press_key.assert_called_once_with("r", down_time=0.15)
        task.click.assert_not_called()

    def test_existing_camera_ui_never_reopens_tool_wheel_or_presses_r(self):
        task = self.task()
        task._camera_open.return_value = True
        EnvironmentMonitoringPhotoTask.open_camera(task)
        task._require_input_focus.assert_not_called()
        task.send_key_down.assert_not_called()
        task.wait_ocr.assert_not_called()
        task.press_key.assert_not_called()

    def test_camera_ui_appearing_after_selection_does_not_press_r_again(self):
        task = self.task()
        EnvironmentMonitoringPhotoTask.open_camera(task)
        task.send_key_up.assert_called_once_with("f6")
        task.press_key.assert_not_called()

    def test_camera_layout_requires_both_icons_in_the_supplied_frame(self):
        for escape_present, zoom_present in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(escape=escape_present, zoom=zoom_present):
                task = self.task()
                supplied_frame = np.full_like(task.frame, 170)
                present = {fL.esc: escape_present, fL.plus_button: zoom_present}
                task.find_one.side_effect = lambda feature, present=present, **kwargs: present[feature]
                self.assertEqual(
                    EnvironmentMonitoringPhotoTask._camera_open(task, frame=supplied_frame),
                    escape_present and zoom_present,
                )
                for detection in task.find_one.call_args_list:
                    self.assertIs(detection.kwargs["frame"], supplied_frame)
                    self.assertNotIn("threshold", detection.kwargs)
                task.ocr.assert_not_called()

    def test_failed_camera_entry_is_not_reported_as_open(self):
        task = self.task()
        task.wait_until.side_effect = [False, False]
        with self.assertRaisesRegex(RuntimeError, "未进入拍照界面"):
            EnvironmentMonitoringPhotoTask.open_camera(task)
        task.press_key.assert_called_once_with("r", down_time=0.15)
        task.send_key_up.assert_called_once_with("f6")

    def test_camera_ui_and_correct_target_are_both_required_for_checked_icon(self):
        for camera_open in (False, True):
            with self.subTest(camera_open=camera_open):
                task = self.task()
                task._camera_open.return_value = camera_open
                with patch(
                    "src.tasks.onetime.EnvironmentMonitoringPhotoTask.xiaochihe_target_checked", return_value=True
                ) as checked:
                    self.assertFalse(EnvironmentMonitoringPhotoTask.photo_condition_met(task))
                if camera_open:
                    checked.assert_called_once_with(task.frame)
                    task.ocr.assert_called_once()
                else:
                    checked.assert_not_called()
                    task.ocr.assert_not_called()

    def test_correct_camera_target_still_requires_a_checked_marker(self):
        for checked_result in (False, True):
            with self.subTest(checked=checked_result):
                task = self.task()
                task._camera_open.return_value = True
                task.ocr.return_value = [Box(100, 720, 150, 30)]
                with patch(
                    "src.tasks.onetime.EnvironmentMonitoringPhotoTask.xiaochihe_target_checked",
                    return_value=checked_result,
                ) as checked:
                    self.assertEqual(EnvironmentMonitoringPhotoTask.photo_condition_met(task), checked_result)
                checked.assert_called_once_with(task.frame)
                if checked_result:
                    self.assertIs(task.ocr.call_args.kwargs["frame"], task.frame)
                else:
                    task.ocr.assert_not_called()

    def test_target_identity_and_marker_keep_the_camera_detection_frame(self):
        task = self.task()
        original_frame = task.frame

        def camera_detected(**kwargs):
            self.assertIs(kwargs["frame"], original_frame)
            task.frame = np.full_like(original_frame, 255)
            return True

        task._camera_open.side_effect = camera_detected
        task.ocr.return_value = [Box(100, 720, 150, 30)]
        with patch(
            "src.tasks.onetime.EnvironmentMonitoringPhotoTask.xiaochihe_target_checked", return_value=True
        ) as checked:
            self.assertTrue(EnvironmentMonitoringPhotoTask.photo_condition_met(task))
        checked.assert_called_once_with(original_frame)
        self.assertIs(task.ocr.call_args.kwargs["frame"], original_frame)

    def test_route_requires_world_and_does_not_move_in_a_menu(self):
        task = self.task()
        task.in_world.return_value = False
        with self.assertRaisesRegex(RuntimeError, "未处于大世界"):
            EnvironmentMonitoringPhotoTask.walk_pool_lotus_route(task)
        task._hold_route_key.assert_not_called()
        task.press_key.assert_not_called()

    def test_unstable_world_after_route_prevents_tracking_key(self):
        task = self.task()
        task.in_world.return_value = True
        task.wait_until.return_value = False
        with self.assertRaisesRegex(RuntimeError, "路线结束后未恢复大世界"):
            EnvironmentMonitoringPhotoTask.walk_pool_lotus_route(task)
        task._hold_route_key.assert_has_calls([call("w", 0.5), call("d", 3)])
        self.assertGreater(task.wait_until.call_args.kwargs["settle_time"], 0)
        task.press_key.assert_not_called()

    def test_industrial_mode_must_disappear_before_movement(self):
        task = self.task()
        task.in_world.return_value = True
        task._world_mode.return_value = "industrial"
        task.wait_until.return_value = False
        with self.assertRaisesRegex(RuntimeError, "未切回探索模式"):
            EnvironmentMonitoringPhotoTask.walk_pool_lotus_route(task)
        task.press_key.assert_called_once_with("tab", after_sleep=0.5)
        task._hold_route_key.assert_not_called()
        task.ocr.assert_not_called()

    def test_route_rechecks_mode_after_switch(self):
        task = self.task()
        task.in_world.return_value = True
        task._world_mode.side_effect = ["industrial", "explore"]
        task.wait_until.side_effect = lambda condition, **kwargs: condition()
        EnvironmentMonitoringPhotoTask.walk_pool_lotus_route(task)
        self.assertEqual(task._world_mode.call_count, 2)
        task.ocr.assert_not_called()
        task._hold_route_key.assert_has_calls([call("w", 0.5), call("d", 3)])
        self.assertEqual(task.press_key.call_args_list, [call("tab", after_sleep=0.5), call("v", after_sleep=0.6)])

    def test_unknown_mode_stops_without_switching_or_moving(self):
        task = self.task()
        task.in_world.return_value = True
        task._world_mode.return_value = "unknown"
        with self.assertRaisesRegex(RuntimeError, "无法确认探索或工业模式"):
            EnvironmentMonitoringPhotoTask.walk_pool_lotus_route(task)
        task._hold_route_key.assert_not_called()
        task.press_key.assert_not_called()

    def test_unknown_mode_after_switch_is_not_accepted_as_exploration(self):
        task = self.task()
        task.in_world.return_value = True
        task._world_mode.side_effect = ["industrial", "unknown"]
        task.wait_until.side_effect = lambda condition, **kwargs: condition()
        with self.assertRaisesRegex(RuntimeError, "未切回探索模式"):
            EnvironmentMonitoringPhotoTask.walk_pool_lotus_route(task)
        task._hold_route_key.assert_not_called()

    def test_mode_colors_from_real_markers_skip_ocr_at_multiple_resolutions(self):
        for mode in ("industrial", "explore"):
            base = np.zeros((1080, 1920, 3), dtype=np.uint8)
            for side, x in (("left", 23), ("right", 201)):
                crop = cv2.imdecode(np.frombuffer(base64.b64decode(MODE_MARKER_PNG[f"{mode}_{side}"]), np.uint8), 1)
                base[44:66, x : x + 22] = crop
            for size in ((1280, 720), (1600, 900), (1920, 1080), (2560, 1440), (3840, 2160)):
                with self.subTest(mode=mode, size=size):
                    task = self.task()
                    task.frame = cv2.resize(base, size)
                    task.find_one.return_value = Box(90, 8, 50, 16)
                    self.assertEqual(EnvironmentMonitoringPhotoTask._world_mode(task), mode)
                    task.ocr.assert_not_called()

    def test_mode_detection_requires_tab_hud_before_color_or_ocr(self):
        task = self.task()
        task.find_one.return_value = None
        task.ocr.return_value = [Box(20, 10, 60, 25)]
        self.assertEqual(EnvironmentMonitoringPhotoTask._world_mode(task), "unknown")
        task.ocr.assert_not_called()

    def test_ambiguous_colors_allow_only_positive_industrial_ocr_fallback(self):
        for words, expected in (([], "unknown"), ([Box(20, 10, 60, 25)], "industrial")):
            with self.subTest(expected=expected):
                task = self.task()
                task.find_one.return_value = Box(90, 8, 50, 16)
                task.ocr.return_value = words
                self.assertEqual(EnvironmentMonitoringPhotoTask._world_mode(task), expected)
                self.assertEqual(task.ocr.call_args.kwargs["match"], task.lang.DeliveryTask.k_96b876e3)
                self.assertIs(task.ocr.call_args.kwargs["frame"], task.frame)

    def test_camera_lost_prevents_any_rotation_gesture(self):
        task = self.task()
        with self.assertRaisesRegex(RuntimeError, "相机界面已关闭"):
            EnvironmentMonitoringPhotoTask.rotate_photo_view(task)
        task.mouse_down.assert_not_called()
        task.smooth_drag.assert_not_called()
        task.mouse_up.assert_not_called()

    def test_rotation_cancellation_or_drag_failure_releases_mouse(self):
        for method, error in (("sleep", TaskDisabledException()), ("smooth_drag", RuntimeError("drag failed"))):
            with self.subTest(method=method):
                task = self.task()
                task._camera_open.return_value = True
                getattr(task, method).side_effect = error
                with self.assertRaises(type(error)):
                    EnvironmentMonitoringPhotoTask.rotate_photo_view(task)
                task.mouse_down.assert_called_once()
                task.mouse_up.assert_called_once_with(key="left")

    def test_initially_satisfied_condition_does_not_rotate(self):
        task = self.task()
        task.photo_condition_met.return_value = True
        self.assertTrue(EnvironmentMonitoringPhotoTask.adjust_photo_view(task))
        task.next_frame.assert_called_once_with()
        task.photo_condition_met.assert_called_once_with()
        task.rotate_photo_view.assert_not_called()

    def test_rotation_stops_immediately_when_condition_becomes_satisfied(self):
        task = self.task()
        task.photo_condition_met.side_effect = [False, False, True]
        self.assertTrue(EnvironmentMonitoringPhotoTask.adjust_photo_view(task))
        self.assertEqual(task.rotate_photo_view.call_count, 2)
        self.assertEqual(task.next_frame.call_count, 3)
        self.assertEqual(task.photo_condition_met.call_count, 3)

    def test_unsatisfied_condition_has_at_most_eight_rotations(self):
        task = self.task()
        task.photo_condition_met.return_value = False
        self.assertFalse(EnvironmentMonitoringPhotoTask.adjust_photo_view(task))
        self.assertEqual(task.rotate_photo_view.call_count, 8)
        self.assertEqual(task.next_frame.call_count, 9)
        task.press_key.assert_not_called()
        task.send_key_down.assert_not_called()

    def test_full_run_selects_only_pool_lotus_and_stops_without_shutter(self):
        for ready in (False, True):
            with self.subTest(ready=ready):
                task = self.task()
                task.adjust_photo_view.return_value = ready
                task._camera_open.return_value = True
                task.photo_condition_met.return_value = ready
                task.prepare_photo.side_effect = lambda task=task: EnvironmentMonitoringPhotoTask.prepare_photo(task)
                result = EnvironmentMonitoringPhotoTask.run.__wrapped__(task)
                task.prepare_observation.assert_called_once_with("小池荷")
                task.walk_pool_lotus_route.assert_called_once_with()
                task.open_camera.assert_called_once_with()
                task.adjust_photo_view.assert_called_once_with()
                self.assertEqual(
                    result, {"name": "小池荷", "camera_open": True, "photo_ready": ready, "photo_taken": False}
                )
                task.press_key.assert_not_called()
                task.click.assert_not_called()
                task.click_relative.assert_not_called()
                task.send_key_down.assert_not_called()

    def test_camera_lost_after_final_rotation_cannot_be_reported_as_open(self):
        task = self.task()
        task.photo_condition_met.return_value = False
        task.adjust_photo_view.side_effect = lambda: EnvironmentMonitoringPhotoTask.adjust_photo_view(task)
        with self.assertRaisesRegex(RuntimeError, "取景结束时相机界面已关闭"):
            EnvironmentMonitoringPhotoTask.prepare_photo(task)
        self.assertEqual(task.rotate_photo_view.call_count, 8)
        self.assertEqual(task.next_frame.call_count, 10)
        task._camera_open.assert_called_once_with(frame=task.frame)
        task.ocr.assert_not_called()
        task.log_info.assert_not_called()

    def test_final_frame_decides_ready_instead_of_the_previous_search_result(self):
        for search_ready, final_ready in ((True, False), (False, True)):
            with self.subTest(search_ready=search_ready, final_ready=final_ready):
                task = self.task()
                task.adjust_photo_view.return_value = search_ready
                task._camera_open.return_value = True
                task.photo_condition_met.return_value = final_ready
                result = EnvironmentMonitoringPhotoTask.prepare_photo(task)
                self.assertEqual(result["photo_ready"], final_ready)
                task._camera_open.assert_called_once_with(frame=task.frame)
                task.photo_condition_met.assert_called_once_with(frame=task.frame)
                task.press_key.assert_not_called()
                task.click.assert_not_called()

    def test_camera_failure_prevents_success_result_and_view_adjustment(self):
        task = self.task()
        task.open_camera.side_effect = RuntimeError("camera failed")
        with self.assertRaisesRegex(RuntimeError, "camera failed"):
            EnvironmentMonitoringPhotoTask.prepare_photo(task)
        task.adjust_photo_view.assert_not_called()
        task.log_info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
