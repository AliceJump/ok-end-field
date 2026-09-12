"""物品导航浮层目标信息（物品名 / 距离 / 方位 / 高度）与叠层文字绘制测试。"""

import ast
import math
import unittest
from pathlib import Path
from types import SimpleNamespace

import polib

from src.tasks.trigger.ItemNavigatorTask import COMPASS_LABELS, ItemNavigatorTask

I18N_ROOT = Path("i18n")
TASK_SOURCE = Path("src/tasks/trigger/ItemNavigatorTask.py")
EXPECTED_LINE_COUNT = 3
OVERLAY_CONFIG_KEYS = ("浮层信息", "浮层文字透明度", "浮层背景透明度", "浮层字号")


def _task_ast():
    return ast.parse(TASK_SOURCE.read_text(encoding="utf-8"))


def _literal_update_dict(attr_name):
    """解析 `self.<attr_name>.update({...})` 的字面量字典。

    用 AST 而不是实例化任务：任务构造依赖设备与配置，测试里不可用。
    """
    for node in ast.walk(_task_ast()):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == attr_name
            and node.args
            and isinstance(node.args[0], ast.Dict)
        ):
            return ast.literal_eval(node.args[0])
    raise AssertionError(f"未找到 self.{attr_name}.update(...) 字面量")


def _config_type_literals():
    """解析所有 `self.config_type["key"] = {...}` 的字面量。"""
    entries = {}
    for node in ast.walk(_task_ast()):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not (
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Attribute)
            and target.value.attr == "config_type"
            and isinstance(target.slice, ast.Constant)
        ):
            continue
        try:
            entries[target.slice.value] = ast.literal_eval(node.value)
        except ValueError:
            continue
    return entries


def _load_overlay_module():
    """浮层依赖 Qt 与 win32gui，缺失时跳过相关用例。"""
    try:
        from src.core.base_mixin.window_arrow_drawing_mixin import TextSpec, WindowArrowDrawingMixin
    except Exception as exc:  # pragma: no cover - 仅在非 Windows / 缺依赖环境触发
        raise unittest.SkipTest(f"浮层依赖不可用: {exc}") from exc
    return TextSpec, WindowArrowDrawingMixin


def _navigator_stub(recorded=None, drawn=None, size=(1920, 1080), config=None, cleared=None):
    """构造 build_target_info_lines / _draw_target_info_text 所需属性的替身。"""

    def tr(text, *args, **kwargs):
        if recorded is not None:
            recorded.append(text)
        return text

    def draw_window_text(**kwargs):
        if drawn is not None:
            drawn.append(kwargs)
        return True

    def clear_window_texts():
        if cleared is not None:
            cleared.append(True)

    stub = SimpleNamespace(
        tr=tr,
        draw_window_text=draw_window_text,
        clear_window_texts=clear_window_texts,
        log_error=lambda message, **kwargs: None,
        config=dict(config or {}),
        _get_window_arrow_size=lambda: size,
        _arrow_center_rel=(162 / 1920, 166 / 1080),
        _nearby_marker_radius_px=75.524 * 1.144,
        _info_text_gap_px=16.0,
        _info_text_key="target_info",
        _info_text_pos=(0.015, 0.2),
        _info_text_font_size_norm=0.024,
        _info_text_color=(255, 255, 255),
        _info_text_alpha=235,
        _info_text_line_spacing=1.35,
        _info_text_panel_alpha=150,
    )
    # 复用任务自身的方位/高度换算、文字纵向定位与配置读取，只把 tr 换成可记录的替身
    stub._compass_index = ItemNavigatorTask._compass_index
    stub._height_label = lambda dy_height: ItemNavigatorTask._height_label(stub, dy_height)
    stub.build_target_info_lines = lambda *args, **kwargs: ItemNavigatorTask.build_target_info_lines(
        stub, *args, **kwargs
    )
    stub._info_text_y_norm = lambda height: ItemNavigatorTask._info_text_y_norm(stub, height)
    stub._percent_to_alpha = ItemNavigatorTask._percent_to_alpha
    stub.INFO_TEXT_FONT_REFERENCE_HEIGHT = ItemNavigatorTask.INFO_TEXT_FONT_REFERENCE_HEIGHT
    stub._overlay_info_enabled = lambda: ItemNavigatorTask._overlay_info_enabled(stub)
    stub._overlay_text_alpha = lambda: ItemNavigatorTask._overlay_text_alpha(stub)
    stub._overlay_panel_alpha = lambda: ItemNavigatorTask._overlay_panel_alpha(stub)
    stub._overlay_font_size_norm = lambda: ItemNavigatorTask._overlay_font_size_norm(stub)
    return stub


class TestCompassIndex(unittest.TestCase):
    def test_cardinal_and_intercardinal_directions(self):
        cases = {
            "北": (0.0, 1.0),
            "东北": (1.0, 1.0),
            "东": (1.0, 0.0),
            "东南": (1.0, -1.0),
            "南": (0.0, -1.0),
            "西南": (-1.0, -1.0),
            "西": (-1.0, 0.0),
            "西北": (-1.0, 1.0),
        }
        for expected, (dx, dz) in cases.items():
            with self.subTest(expected=expected):
                index = ItemNavigatorTask._compass_index(dx, dz)
                self.assertEqual(COMPASS_LABELS[index], expected)

    def test_sector_boundary_uses_nearest_direction(self):
        # 22.5° 是「北」与「东北」的临界，应归入后者
        just_under = math.radians(22.4)
        just_over = math.radians(22.6)
        self.assertEqual(
            COMPASS_LABELS[ItemNavigatorTask._compass_index(math.sin(just_under), math.cos(just_under))], "北"
        )
        self.assertEqual(
            COMPASS_LABELS[ItemNavigatorTask._compass_index(math.sin(just_over), math.cos(just_over))], "东北"
        )

    def test_index_is_always_in_range(self):
        for degrees in range(-360, 721, 7):
            radians = math.radians(degrees)
            index = ItemNavigatorTask._compass_index(math.sin(radians), math.cos(radians))
            self.assertIn(index, range(len(COMPASS_LABELS)))


class TestTargetInfoLines(unittest.TestCase):
    def test_lines_contain_name_distance_bearing_and_height(self):
        stub = _navigator_stub()
        lines = ItemNavigatorTask.build_target_info_lines(stub, "晶锥天使", 1.0, 1.0, 12.34, 4.26)
        self.assertEqual(len(lines), EXPECTED_LINE_COUNT)
        self.assertEqual(lines[0], "晶锥天使")
        self.assertEqual(lines[1], "距离 12.3 · 方位 东北")
        self.assertEqual(lines[2], "高度 上方 4.3")

    def test_height_label_switches_between_above_below_and_level(self):
        stub = _navigator_stub()
        above = ItemNavigatorTask.build_target_info_lines(stub, "物品", 0.0, 1.0, 5.0, 3.5)
        below = ItemNavigatorTask.build_target_info_lines(stub, "物品", 0.0, 1.0, 5.0, -3.5)
        level = ItemNavigatorTask.build_target_info_lines(stub, "物品", 0.0, 1.0, 5.0, 0.0)
        self.assertEqual(above[2], "高度 上方 3.5")
        self.assertEqual(below[2], "高度 下方 3.5")
        self.assertEqual(level[2], "高度 同高 0.0")

    def test_blank_item_name_is_omitted(self):
        stub = _navigator_stub()
        for name in (None, "", "   "):
            with self.subTest(name=name):
                lines = ItemNavigatorTask.build_target_info_lines(stub, name, 0.0, 1.0, 5.0, 0.0)
                self.assertEqual(len(lines), EXPECTED_LINE_COUNT - 1)
                self.assertTrue(lines[0].startswith("距离"))

    def test_runtime_values_are_not_passed_to_tr(self):
        """运行时数据（物品名、距离、高度数值）只能经 .format() 注入，不能直接喂给 tr()。"""
        recorded = []
        stub = _navigator_stub(recorded=recorded)
        ItemNavigatorTask.build_target_info_lines(stub, "晶锥天使", 1.0, 1.0, 12.34, 4.26)
        self.assertTrue(recorded)
        for text in recorded:
            self.assertNotIn("晶锥天使", text)
            self.assertNotIn("12.3", text)
            self.assertNotIn("4.3", text)


class TestDrawTargetInfoText(unittest.TestCase):
    def test_overlay_text_uses_configured_style(self):
        drawn = []
        stub = _navigator_stub(drawn=drawn)
        ItemNavigatorTask._draw_target_info_text(stub, "晶锥天使", 0.0, 1.0, 12.34, -4.26)

        self.assertEqual(len(drawn), 1)
        kwargs = drawn[0]
        self.assertEqual(kwargs["text_key"], "target_info")
        self.assertEqual(kwargs["lines"], ["晶锥天使", "距离 12.3 · 方位 北", "高度 下方 4.3"])
        self.assertEqual(kwargs["x_norm"], 0.015)
        self.assertEqual(kwargs["y_norm"], ItemNavigatorTask._info_text_y_norm(stub, 1080))
        self.assertEqual(kwargs["font_size_norm"], 0.024)
        self.assertEqual(kwargs["color"], (255, 255, 255))
        self.assertEqual(kwargs["alpha"], 235)
        self.assertEqual(kwargs["line_spacing"], 1.35)
        self.assertEqual(kwargs["panel_alpha"], 150)

    def test_text_is_placed_below_the_nearby_marker_cloud(self):
        """文字顶边必须落在附近标记云团底边之下，否则会被黄色标记压住。"""
        drawn = []
        stub = _navigator_stub(drawn=drawn, size=(1920, 1080))
        ItemNavigatorTask._draw_target_info_text(stub, "物品", 0.0, 1.0, 5.0, 0.0)

        y_norm = drawn[0]["y_norm"]
        text_top_px = y_norm * 1080
        cloud_bottom_px = 1080 * stub._arrow_center_rel[1] + stub._nearby_marker_radius_px
        self.assertGreater(text_top_px, cloud_bottom_px)

    def test_missing_window_size_skips_drawing(self):
        drawn = []
        stub = _navigator_stub(drawn=drawn, size=(0, 0))
        ItemNavigatorTask._draw_target_info_text(stub, "物品", 0.0, 1.0, 5.0, 0.0)
        self.assertEqual(drawn, [])

    def test_overlay_failure_is_swallowed(self):
        logged = []
        stub = _navigator_stub()
        stub.draw_window_text = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
        stub.log_error = lambda message, **kwargs: logged.append(message)
        ItemNavigatorTask._draw_target_info_text(stub, "物品", 0.0, 1.0, 1.0, 0.0)
        self.assertTrue(logged)


class TestOverlayInfoToggle(unittest.TestCase):
    """「浮层信息」开关：关闭时不绘制，并主动清空已有文字。"""

    def test_enabled_by_default_when_config_missing(self):
        drawn = []
        stub = _navigator_stub(drawn=drawn)
        ItemNavigatorTask._draw_target_info_text(stub, "物品", 0.0, 1.0, 5.0, 0.0)
        self.assertEqual(len(drawn), 1)

    def test_enabled_draws_text(self):
        drawn = []
        stub = _navigator_stub(drawn=drawn, config={"浮层信息": True})
        ItemNavigatorTask._draw_target_info_text(stub, "物品", 0.0, 1.0, 5.0, 0.0)
        self.assertEqual(len(drawn), 1)

    def test_disabled_skips_drawing_and_clears_existing_text(self):
        drawn = []
        cleared = []
        stub = _navigator_stub(drawn=drawn, cleared=cleared, config={"浮层信息": False})
        ItemNavigatorTask._draw_target_info_text(stub, "物品", 0.0, 1.0, 5.0, 0.0)
        self.assertEqual(drawn, [], "关闭时不应再推送文字")
        self.assertEqual(cleared, [True], "关闭时应主动清一次，保证切换即时生效")

    def test_disabled_still_swallows_clear_errors(self):
        logged = []
        stub = _navigator_stub(config={"浮层信息": False})
        stub.clear_window_texts = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        stub.log_error = lambda message, **kwargs: logged.append(message)
        ItemNavigatorTask._draw_target_info_text(stub, "物品", 0.0, 1.0, 5.0, 0.0)
        self.assertTrue(logged)


class TestOverlayInfoConfig(unittest.TestCase):
    """透明度与字号配置的换算与回退。"""

    def test_percent_to_alpha_maps_full_range(self):
        cases = {0: 0, 100: 255, 92: 235, 59: 150, 50: 128}
        for percent, expected in cases.items():
            with self.subTest(percent=percent):
                self.assertEqual(ItemNavigatorTask._percent_to_alpha(percent, 999), expected)

    def test_percent_to_alpha_falls_back_on_invalid_input(self):
        for value in (None, "", "abc", float("nan")):
            with self.subTest(value=value):
                self.assertEqual(ItemNavigatorTask._percent_to_alpha(value, 235), 235)

    def test_percent_to_alpha_clamps_out_of_range_values(self):
        self.assertEqual(ItemNavigatorTask._percent_to_alpha(-40, 235), 0)
        self.assertEqual(ItemNavigatorTask._percent_to_alpha(400, 235), 255)

    def test_config_values_are_applied_to_the_draw_call(self):
        drawn = []
        stub = _navigator_stub(
            drawn=drawn,
            config={"浮层信息": True, "浮层文字透明度": 50, "浮层背景透明度": 0, "浮层字号": 54},
        )
        ItemNavigatorTask._draw_target_info_text(stub, "物品", 0.0, 1.0, 5.0, 0.0)

        kwargs = drawn[0]
        self.assertEqual(kwargs["alpha"], 128)
        self.assertEqual(kwargs["panel_alpha"], 0)
        # 54px 以 1080p 为基准 → 归一化字号
        self.assertAlmostEqual(kwargs["font_size_norm"], 54 / 1080)

    def test_invalid_config_values_fall_back_to_internal_defaults(self):
        stub = _navigator_stub(
            config={"浮层文字透明度": "oops", "浮层背景透明度": None, "浮层字号": 0},
        )
        self.assertEqual(stub._overlay_text_alpha(), stub._info_text_alpha)
        self.assertEqual(stub._overlay_panel_alpha(), stub._info_text_panel_alpha)
        self.assertEqual(stub._overlay_font_size_norm(), stub._info_text_font_size_norm)

    def test_font_size_scales_with_the_reference_height(self):
        stub = _navigator_stub(config={"浮层字号": 26})
        self.assertAlmostEqual(stub._overlay_font_size_norm(), 26 / 1080)
        # 基准 1080p 下 26px 约等于原内部默认 0.024
        self.assertAlmostEqual(stub._overlay_font_size_norm(), 0.024, places=3)


class TestOverlayInfoConfigSchema(unittest.TestCase):
    """config_type 的条件显示：关闭「浮层信息」时隐藏三个子项。"""

    CHILD_KEYS = ("浮层文字透明度", "浮层背景透明度", "浮层字号")

    def test_sub_configs_hide_children_when_disabled(self):
        entry = _config_type_literals()["浮层信息"]
        self.assertEqual(entry["sub_configs"], {True: list(self.CHILD_KEYS)})
        # 关闭（False）时没有任何子项可见
        self.assertNotIn(False, entry["sub_configs"])

    def test_numeric_children_declare_a_bounded_range(self):
        entries = _config_type_literals()
        for key in self.CHILD_KEYS:
            with self.subTest(key=key):
                spec = entries[key]
                self.assertIn("min", spec)
                self.assertIn("max", spec)
                self.assertLess(spec["min"], spec["max"])

    def test_numeric_children_use_integer_defaults(self):
        """框架按默认值类型选控件：int 才会得到带 min/max 的 SpinBox。"""
        defaults = _literal_update_dict("default_config")
        for key in self.CHILD_KEYS:
            with self.subTest(key=key):
                self.assertIsInstance(defaults[key], int)
                self.assertNotIsInstance(defaults[key], bool)


class TestOverlayInfoDefaultConfig(unittest.TestCase):
    """default_config 的默认值必须与内部回退值一致，且浮层默认开启。"""

    def test_overlay_info_is_on_by_default(self):
        self.assertIs(_literal_update_dict("default_config")["浮层信息"], True)

    def test_defaults_match_the_internal_fallbacks(self):
        defaults = _literal_update_dict("default_config")
        # 92% ↔ alpha 235；59% ↔ alpha 150；26px@1080p ↔ 0.024
        self.assertEqual(ItemNavigatorTask._percent_to_alpha(defaults["浮层文字透明度"], 0), 235)
        self.assertEqual(ItemNavigatorTask._percent_to_alpha(defaults["浮层背景透明度"], 0), 150)
        self.assertAlmostEqual(defaults["浮层字号"] / 1080, 0.024, places=3)


class TestOverlayInfoConfigVisibility(unittest.TestCase):
    """用框架真实的 config_visibility 验证条件显示行为。"""

    def _visible_keys(self, enabled):
        from ok.core.config_schema import config_visibility

        config = dict(_literal_update_dict("default_config"))
        config["浮层信息"] = enabled
        visible = config_visibility(config, _config_type_literals())
        return {key for key in OVERLAY_CONFIG_KEYS if visible(key)}

    def test_children_are_visible_only_when_enabled(self):
        self.assertEqual(self._visible_keys(True), set(OVERLAY_CONFIG_KEYS))
        self.assertEqual(self._visible_keys(False), {"浮层信息"})

    def test_parent_switch_itself_is_always_visible(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                self.assertIn("浮层信息", self._visible_keys(enabled))


class _FakeConfig:
    """ConfigCard 需要的最小配置对象。"""

    def __init__(self, data, defaults):
        self._data = dict(data)
        self._defaults = dict(defaults)

    def items(self):
        return list(self._data.items())

    def get(self, key, default=None):
        return self._data.get(key, default)

    def get_default(self, key):
        return self._defaults.get(key, self._data.get(key))

    def has_user_config(self):
        return True

    def reset_to_default(self):
        self._data = dict(self._defaults)

    def __setitem__(self, key, value):
        self._data[key] = value

    def __contains__(self, key):
        return key in self._data


class TestOverlayInfoConfigCardVisibility(unittest.TestCase):
    """真实 Qt 配置卡：关闭「浮层信息」时，三个子项被隐藏。"""

    CHILD_KEYS = ("浮层文字透明度", "浮层背景透明度", "浮层字号")

    @classmethod
    def setUpClass(cls):
        try:
            from ok import og
            from ok.ui.qt.tasks.ConfigCard import ConfigContentMixin
            from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
        except Exception as exc:  # pragma: no cover - 仅在缺 Qt 环境触发
            raise unittest.SkipTest(f"Qt 配置卡不可用: {exc}") from exc

        class _Card(ConfigContentMixin, QWidget):
            def __init__(self, config, defaults, descriptions, types):
                QWidget.__init__(self)
                self.viewLayout = QVBoxLayout(self)
                self._expand_enabled = True
                self._init_config_content(None, config, defaults, descriptions, types)

            def _on_empty_config_content(self):
                pass

        class _AppStub:
            """配置控件走 og.app.tr() 取标题，测试里用恒等替身。"""

            @staticmethod
            def tr(text):
                return text

        cls._Card = _Card
        cls.app = QApplication.instance() or QApplication([])
        # 无 GUI 主程序时 og.app 为 None，配置控件构造会失败
        cls._previous_app = og.app
        og.app = _AppStub()

    @classmethod
    def tearDownClass(cls):
        from ok import og

        og.app = cls._previous_app

    def _card(self, enabled):
        defaults = {
            key: value for key, value in _literal_update_dict("default_config").items() if key in OVERLAY_CONFIG_KEYS
        }
        descriptions = {
            key: value
            for key, value in _literal_update_dict("config_description").items()
            if key in OVERLAY_CONFIG_KEYS
        }
        types = {key: value for key, value in _config_type_literals().items() if key in OVERLAY_CONFIG_KEYS}
        data = dict(defaults)
        data["浮层信息"] = enabled
        return self._Card(_FakeConfig(data, defaults), defaults, descriptions, types)

    def _hidden(self, card, key):
        # 卡片未 show()，isVisible() 恒为 False；用 isHidden() 判断显式隐藏状态
        return card.config_widget_by_key[key].isHidden()

    def test_all_four_widgets_are_rendered(self):
        card = self._card(True)
        for key in OVERLAY_CONFIG_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, card.config_widget_by_key)

    def test_children_are_shown_when_enabled(self):
        card = self._card(True)
        for key in self.CHILD_KEYS:
            with self.subTest(key=key):
                self.assertFalse(self._hidden(card, key), f"{key} 应显示")

    def test_children_are_hidden_when_disabled(self):
        card = self._card(False)
        for key in self.CHILD_KEYS:
            with self.subTest(key=key):
                self.assertTrue(self._hidden(card, key), f"{key} 应隐藏")
        self.assertFalse(self._hidden(card, "浮层信息"), "父开关自身必须始终可见")

    def test_flipping_the_switch_toggles_children_immediately(self):
        card = self._card(True)
        switch = card.config_widget_by_key["浮层信息"].switch_button

        switch.setChecked(False)
        for key in self.CHILD_KEYS:
            with self.subTest(key=key, state="off"):
                self.assertTrue(self._hidden(card, key), f"关闭后 {key} 应隐藏")

        switch.setChecked(True)
        for key in self.CHILD_KEYS:
            with self.subTest(key=key, state="on"):
                self.assertFalse(self._hidden(card, key), f"开启后 {key} 应显示")


class TestOverlayConfigMsgids(unittest.TestCase):
    """新增的配置键名与说明必须落到全部语言目录。"""

    def _required_msgids(self):
        defaults = _literal_update_dict("default_config")
        descriptions = _literal_update_dict("config_description")
        msgids = [key for key in OVERLAY_CONFIG_KEYS if key in defaults]
        msgids.extend(descriptions[key] for key in OVERLAY_CONFIG_KEYS if key in descriptions)
        return msgids

    def test_all_overlay_config_keys_and_descriptions_are_translated(self):
        msgids = self._required_msgids()
        self.assertEqual(len(msgids), len(OVERLAY_CONFIG_KEYS) * 2, f"应同时覆盖键名与说明: {msgids}")

        missing = []
        for path in sorted(I18N_ROOT.glob("*/LC_MESSAGES/ok.po")):
            locale = path.parents[1].name
            entries = {entry.msgid: entry for entry in polib.pofile(str(path)) if entry.msgid}
            for msgid in msgids:
                entry = entries.get(msgid)
                if entry is None:
                    missing.append(f"{locale}: 缺少 msgid {msgid!r}")
                elif not entry.msgstr:
                    missing.append(f"{locale}: 未翻译 {msgid!r}")
        self.assertEqual(missing, [], "\n".join(missing))


class TestInfoTextYNorm(unittest.TestCase):
    """文字块纵向定位：必须跟随云团的原始像素半径，而非固定归一化值。"""

    def setUp(self):
        self.stub = _navigator_stub()

    def _cloud_bottom_norm(self, height):
        return (height * self.stub._arrow_center_rel[1] + self.stub._nearby_marker_radius_px) / height

    def test_y_clears_the_marker_cloud_at_common_resolutions(self):
        for height in (1440, 1080, 900, 768, 720, 648, 576):
            with self.subTest(height=height):
                y_norm = self.stub._info_text_y_norm(height)
                self.assertGreater(y_norm, self._cloud_bottom_norm(height))

    def test_gap_in_pixels_stays_constant_across_resolutions(self):
        gaps = []
        for height in (1080, 900, 720, 576):
            y_norm = self.stub._info_text_y_norm(height)
            text_top_px = y_norm * height
            cloud_bottom_px = height * self.stub._arrow_center_rel[1] + self.stub._nearby_marker_radius_px
            gaps.append(round(text_top_px - cloud_bottom_px))
        # 原始像素间隙应稳定（16px 附近），不随分辨率漂移
        self.assertLessEqual(max(gaps) - min(gaps), 2, f"间隙漂移过大: {gaps}")

    def test_low_resolution_y_is_larger_than_high_resolution(self):
        # 分辨率越低，云团归一化尺寸越大，文字必须更靠下
        self.assertGreater(self.stub._info_text_y_norm(720), self.stub._info_text_y_norm(1080))

    def test_y_never_drops_below_configured_floor(self):
        for height in (2160, 1440, 1080):
            with self.subTest(height=height):
                self.assertGreaterEqual(self.stub._info_text_y_norm(height), self.stub._info_text_pos[1])

    def test_y_is_clamped_for_degenerate_heights(self):
        self.assertLessEqual(self.stub._info_text_y_norm(1), 0.85)
        self.assertLessEqual(self.stub._info_text_y_norm(0), 0.85)
        self.assertGreaterEqual(self.stub._info_text_y_norm(1), 0.0)


class TestOverlayTextSpec(unittest.TestCase):
    def setUp(self):
        self.text_spec_cls, self.mixin_cls = _load_overlay_module()

    def _mixin_with_recorder(self, emitted):
        mixin = self.mixin_cls()
        mixin._init_window_arrow_drawing_mixin()
        mixin._ensure_window_arrow_controller = lambda: SimpleNamespace(
            text_updated=SimpleNamespace(emit=emitted.append)
        )
        return mixin

    def test_text_spec_is_normalized_and_emitted(self):
        emitted = []
        mixin = self._mixin_with_recorder(emitted)

        self.assertTrue(mixin.draw_window_text(["第一行", "", "第二行"], text_key="k"))
        self.assertEqual(len(emitted), 1)
        spec = emitted[0]
        self.assertIsInstance(spec, self.text_spec_cls)
        self.assertEqual(spec.text_key, "k")
        self.assertEqual(spec.lines, ["第一行", "第二行"])
        self.assertEqual(spec.color, mixin._window_arrow_color)
        self.assertEqual(spec.alpha, mixin._window_arrow_text_alpha)
        self.assertEqual(spec.font_size_norm, mixin._window_arrow_text_font_size_norm)

    def test_string_input_is_accepted_and_empty_input_is_rejected(self):
        emitted = []
        mixin = self._mixin_with_recorder(emitted)

        self.assertTrue(mixin.draw_window_text("单行"))
        self.assertEqual(emitted[0].lines, ["单行"])

        self.assertFalse(mixin.draw_window_text([]))
        self.assertFalse(mixin.draw_window_text(None))
        self.assertFalse(mixin.draw_window_text(""))
        self.assertEqual(len(emitted), 1)

    def test_missing_controller_reports_failure(self):
        mixin = self.mixin_cls()
        mixin._init_window_arrow_drawing_mixin()
        mixin._ensure_window_arrow_controller = lambda: None
        self.assertFalse(mixin.draw_window_text("文本"))


class TestOverlayTextMsgids(unittest.TestCase):
    """新增浮层文案必须落到全部语言目录。"""

    @classmethod
    def setUpClass(cls):
        cls.recorded = []
        stub = _navigator_stub(recorded=cls.recorded)
        for dx, dz in (
            (0.0, 1.0),
            (1.0, 1.0),
            (1.0, 0.0),
            (1.0, -1.0),
            (0.0, -1.0),
            (-1.0, -1.0),
            (-1.0, 0.0),
            (-1.0, 1.0),
        ):
            ItemNavigatorTask.build_target_info_lines(stub, "物品", dx, dz, 1.0, 1.0)
        for dy in (1.0, -1.0, 0.0):
            ItemNavigatorTask.build_target_info_lines(stub, "物品", 0.0, 1.0, 1.0, dy)

    def test_every_msgid_exists_and_is_translated_in_all_locales(self):
        self.assertTrue(self.recorded)
        missing = []
        for path in sorted(I18N_ROOT.glob("*/LC_MESSAGES/ok.po")):
            locale = path.parents[1].name
            entries = {entry.msgid: entry for entry in polib.pofile(str(path)) if entry.msgid}
            for msgid in self.recorded:
                entry = entries.get(msgid)
                if entry is None:
                    missing.append(f"{locale}: 缺少 msgid {msgid!r}")
                elif not entry.msgstr:
                    missing.append(f"{locale}: 未翻译 {msgid!r}")
        self.assertEqual(missing, [], "\n".join(missing))

    def test_all_compass_labels_are_covered(self):
        for label in COMPASS_LABELS:
            self.assertIn(label, self.recorded)
        for label in ("上方", "下方", "同高"):
            self.assertIn(label, self.recorded)


if __name__ == "__main__":
    unittest.main()
